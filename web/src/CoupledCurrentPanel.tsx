import type {CoupledCurrent} from '../../browser/control.ts';
import type {RunState} from '../../browser/types.ts';
type PanelCallbacks = {onRunState?: (state: RunState) => void; onMotorState?: (state: unknown) => void; onBrakeState?: (state: unknown) => void; onEvent?: (message: string) => void};
import {subscribeDevices} from './deviceStore.ts';
import {apiFetch} from './apiClient.ts';
import React, {useEffect, useRef, useState} from 'react';
import ControlDialog from './ControlDialog.tsx';
import {isOutputPoint} from '../../browser/characteristic-points.ts';
import {TORQUE_SWEEP_DEFAULTS,torqueSweepError,torqueSweepRequest} from './torqueSweep.ts';

const DESKTOP=Boolean(globalThis.window?.inntorqueDesktop);
const KEY='motor-bench:torque-sweep:v3';
function savedForm(): Record<string, string> {
  try {
    const current=localStorage.getItem(KEY)||localStorage.getItem('motor-bench:torque-sweep:v2');
    const saved=JSON.parse(current||localStorage.getItem('motor-bench:torque-sweep:v1')||'{}');
    return {motorMode:saved.motorMode==='current_loop'?'current_loop':'dyno',targetA:String(saved.targetA??0),...Object.fromEntries(Object.entries(TORQUE_SWEEP_DEFAULTS).map(([key,value])=>[key,
      key==='startNm'&&!current?'0':String(saved?.[key]??value)]))};
  } catch {return {...TORQUE_SWEEP_DEFAULTS,motorMode:'dyno',targetA:'0'};}
}
const show=(value,digits=3)=>Number.isFinite(value)?value.toFixed(digits):'—';
const phases={idle:'未运行',checking:'检查设备',ramping:'空载升速',awaiting_load:'待确认空载转速',loading:'阶梯加载',stopping:'两路停机确认中',stopped:'已结束'};

function CoupledCurrentPanel({token,bridgeReady,sensorConnected,missingDevices,outputSpeedRpm,motorSupplyOn,onMotorState,onBrakeState,onEvent,onRunState}) {
  const [form,setForm]=useState(savedForm),[status,setStatus]=useState<Partial<ReturnType<CoupledCurrent['status']>>>({run:{},motor:{},brake:{}});
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const [pauseRequested,setPauseRequested]=useState(false);
  const epoch=useRef(0),intent=useRef(0),pending=useRef(0),stopPending=useRef(false),ended=useRef(null);
  const callbacks=useRef<PanelCallbacks>({});callbacks.current={onMotorState,onBrakeState,onEvent,onRunState};
  function apply(data) {
    setStatus(previous=>({...previous,...data}));
    if(data.run)callbacks.current.onRunState?.(data.run);
    if(data.motor)callbacks.current.onMotorState(data.motor);
    if(data.brake?.device)callbacks.current.onBrakeState(data.brake.device);
    if(data.run?.id&&!data.run.running&&ended.current!==data.run.id) {
      ended.current=data.run.id;
      callbacks.current.onEvent(`力矩测试结束：${data.run.reason||'任务结束'}；${data.run.stopConfirmed===true?'两路输出关闭已确认':data.run.stopConfirmed===false?'存在未确认关闭的输出':'未开启输出'}`);
    }
  }
  const applyRef=useRef(apply);applyRef.current=apply;
  useEffect(()=>{try{localStorage.setItem(KEY,JSON.stringify(form));}catch{}},[form]);
  useEffect(()=>subscribeDevices(state=>{
    if(pending.current)return;
    const data=state.routes?.['/api/coupled-current/status'];
    if(data)applyRef.current(state.streamOnline===false?{...data,motor:{connected:false}}:data);
  }),[]);
  const run=status.run||{},supported=status.capabilities?.fullIqRamp===true;
  const selectedMode=run.running?run.motorMode:(form.motorMode??'dyno');
  const maximum=selectedMode==='dyno';
  const invalid=torqueSweepError(form,status.torqueCalibration,status.motor?.snapshot?.capabilities);
  const blockedReason=!bridgeReady?'通信桥未连接。':!supported?'通信服务尚未支持新版流程。'
    :status.safety?.stopping||run.phase==='stopping'?'正在确认输出关闭，请等待。'
    :status.safety?.blocked||run.stopConfirmed===false?'输出关闭未确认，请先重试停止。'
    :!motorSupplyOn?'电机电源输出关闭，请先手动开启电机电源。'
    :!status.motor?.connected?`电机通信未就绪：${status.motor?.error||'请连接电机并等待新鲜反馈。'}`
    :!run.running&&status.motor?.snapshot?.startBlockReason?status.motor.snapshot.startBlockReason
    :!status.motor?.snapshot?.capabilities?.[maximum?'dyno':'currentLoop']?'驱动板未声明所选控制模式的完整能力。'
    :!status.brake?.device?.connected?'制动电源未连接。':!sensorConnected?'扭矩传感器未连接。':'';
  const elapsed=run.loadStartedAt!=null ? (run.measurementEndedAt!=null?run.measurementEndedAt-run.loadStartedAt:run.loadElapsedS) : run.elapsed;
  const ready=!blockedReason && !missingDevices;
  const canLoad=ready&&run.running&&!run.loadRequested&&run.mode==='torque_sweep'&&run.phase==='awaiting_load';
  async function action(name,{pause=false}={}) {
    const clickedAt = Date.now() / 1000;
    if(pending.current&&name!=='stop')return;
    if(name==='stop'&&stopPending.current)return;
    if((name==='start'||name==='target')&&invalid){setError(invalid);return;}
    if(name==='load'&&!canLoad)return;
    if(name==='stop')stopPending.current=true;
    const currentIntent=++intent.current;
    if(name==='start')setPauseRequested(false);
    if(name==='stop'){
      setPauseRequested(pause);
      setStatus(previous=>({...previous,run:{...previous.run,phase:'stopping',stopConfirmed:null}}));
    }
    epoch.current++;pending.current++;setBusy(true);setError('');
    try {
      if(!token.current||!['load','stop'].includes(name)){
        const response=await apiFetch('/api/health',{cache:'no-store',signal:AbortSignal.timeout(3000)});
        if(!response.ok)throw new Error('通信桥不可用');
        token.current=(await response.json()).token;
      }
      const response=await apiFetch(`/api/coupled-current/${name}`,{method:'POST',
        headers:{'Content-Type':'application/json','X-MotorBench-Token':token.current},
        body:JSON.stringify(name==='start'?torqueSweepRequest(form,status.torqueCalibration):name==='load'?{id:run.id,clickedAt}:name==='target'?{id:run.id,targetA:Number(form.targetA)}:{}),signal:AbortSignal.timeout(15000)});
      const data=await response.json();
      // A delayed start/load response must never undo a newer stop in the UI.
      if(currentIntent!==intent.current)return;
      applyRef.current(data);
      if(!response.ok)throw new Error(data.error||'力矩测试操作失败');
      if(name==='start'){
        callbacks.current.onEvent(`启动${data.run?.motorMode==='dyno'?'测功模式':'电流环模式'}，电流参考由驱动平滑拉升；制动保持零励磁。`);
      }
      if(name==='target')callbacks.current.onEvent(`电流环目标 Iq 已设置为 ${form.targetA} A`);
      if(name==='load')callbacks.current.onEvent(`开始力矩加载：起点 ${form.startNm} N·m，步长 ${form.stepNm} N·m，结束目标 ${run.torqueConfig?.maxNm??'—'} N·m`);
      if(name==='stop'&&pause&&data.run?.stopConfirmed===true)callbacks.current.onEvent('满转已暂停：电机输出关闭已确认，制动未加载；再次测试需重新启动①。');
    }catch(failure){if(currentIntent===intent.current)setError(failure.message);}
    finally{epoch.current++;pending.current--;setBusy(pending.current>0);if(name==='stop')stopPending.current=false;}
  }
  async function clearFault() {
    if(pending.current)return;
    const currentIntent=++intent.current;
    pending.current++;setBusy(true);setError('');
    try {
      const health=await apiFetch('/api/health',{cache:'no-store',signal:AbortSignal.timeout(3000)});
      if(!health.ok)throw new Error('通信桥不可用');
      token.current=(await health.json()).token;
      const response=await apiFetch('/api/motor-current/clear-fault',{method:'POST',
        headers:{'Content-Type':'application/json','X-MotorBench-Token':token.current},
        body:'{}',signal:AbortSignal.timeout(10000)});
      const data=await response.json();
      if(currentIntent!==intent.current)return;
      if(!response.ok)throw new Error(data.error||'清除故障失败');
      setStatus(previous=>({...previous,motor:data}));
      callbacks.current.onMotorState?.(data);
      callbacks.current.onEvent('已请求清除电机故障，设备状态已读回。');
    }catch(failure){if(currentIntent===intent.current)setError(failure.message);}
    finally{pending.current--;setBusy(pending.current>0);}
  }
  const field=(key,label,min,max,step)=><label key={key}>{label}<input aria-label={label} type="number" value={run.running ? run.torqueConfig?.[key] ?? form[key] : form[key]} min={min} max={max} step={step}
    disabled={busy||run.running} onChange={event=>setForm(previous=>({...previous,[key]:event.target.value}))}/></label>;
  const brake=status.brake?.device,amp=brake?.connected?brake.snapshot?.measured?.[1]/10**Number(brake.config?.currentDecimals??3):null;
  const notices = [
    run.recordingError && {text:'记录异常：'+run.recordingError,alert:true},
    error && {text:error,alert:true},
    run.stopConfirmed===false && {text:'两路关闭未全部确认：'+(run.stopErrors||[]).join('；')+'。请重试停止。',alert:true},
    status.safety?.blocked&&!status.safety?.stopping&&run.stopConfirmed!==false && {text:[status.safety.reason,...(status.safety.errors||[])].filter(Boolean).join('；'),alert:true},
    missingDevices && {text:'待连接：'+missingDevices,alert:false},
    !run.running&&invalid && {text:invalid,alert:true},
    !supported && {text:'通信服务尚未就绪。',alert:false},
    !run.running&&supported&&!error&&!invalid&&run.stopConfirmed!==false&&!status.safety?.blocked&&!missingDevices&&blockedReason && {text:blockedReason,alert:false},
    run.reason && {text:'结束原因：'+run.reason,alert:false},
    run.stopConfirmed===true && {text:pauseRequested?'电机已暂停，制动未加载。':'电机与制动输出关闭均已确认',alert:false},
  ].filter(Boolean);
  const points=run.characteristicPoints?.filter(isOutputPoint) || [];
  const advanced=<dl className="bench-diagnostics">
    <dt>轴端扭矩</dt><dd>{show(run.measuredNm)} N·m</dd>
    <dt>制动器励磁电流 · 设定</dt><dd>{show(run.brakeCurrentA)} A</dd>
    <dt>制动器励磁电流 · 回读</dt><dd>{show(amp)} A</dd>
    {run.recordingId&&<><dt>记录编号</dt><dd>{run.recordingId}</dd></>}
  </dl>;
  const noticeList = notices.map((notice,index)=><p key={index} className={notice.alert?'hw-inline-error':'hw-readback'}>{notice.text}</p>);
  return <section className="panel coupled-current-panel" aria-label="电机模式与力矩加载测试">
    <div className="control-title"><h2>电机控制 / 力矩加载</h2></div>
    <div className="hw-output-actions coupled-stage-actions">
      <button className="button primary" disabled={busy||run.running||!ready||!!invalid} onClick={()=>action('start')}>① {maximum?'启动测功模式':'启动电流环模式'}</button>
      <button className="button primary" disabled={busy||!canLoad} onClick={()=>action('load')}>② 开启力矩输出</button>
      <button className="button danger" disabled={stopPending.current||!bridgeReady} onClick={()=>action('stop')}>停止两路输出</button>
      {globalThis.INNTORQUE_BROWSER_DIRECT && status.motor?.connected && status.motor?.snapshot?.motor?.fault &&
        <button className="button" disabled={busy||run.running||run.stopConfirmed!==true||status.motor.snapshot.motor.output_on!==false}
          onClick={clearFault}>{busy?'正在清除故障…':'清除故障'}</button>}
    </div>
    {notices.length>0&&<div className="coupled-notices">
      {DESKTOP?<><p className={notices[0].alert?'hw-inline-error':'hw-readback'} role={notices[0].alert?'alert':'status'} title={notices[0].text}>{notices[0].text}</p>
        {(notices.length>1||notices[0].alert||run.reason)&&<ControlDialog label="状态详情">{noticeList}</ControlDialog>}</>:noticeList}
    </div>}
    <div className="coupled-device coupled-motor">
      <div className="coupled-device-title"><strong>① 电机空载准备</strong></div>
      <label className="coupled-mode">电机输出模式<select aria-label="电机输出模式" disabled={busy||run.running} value={selectedMode} onChange={event=>setForm(previous=>({...previous,motorMode:event.target.value}))}>
        <option value="dyno">测功模式</option><option value="current_loop">电流环模式</option></select></label>
      {!maximum&&<div className="coupled-current-target"><label>目标 Iq / A 峰值<input aria-label="目标 Iq / A 峰值" type="number" min={status.motor?.snapshot?.capabilities?.currentLoopMinA??undefined} max={status.motor?.snapshot?.capabilities?.currentLoopMaxA??undefined} step={.01} value={form.targetA} disabled={busy||!!run.loadRequested} onChange={event=>setForm(previous=>({...previous,targetA:event.target.value}))}/></label>
        <button className="button" disabled={busy||!run.running||!!run.loadRequested||!!invalid} onClick={()=>action('target')}>应用电流</button></div>}
      <div className="hw-output-actions coupled-motor-actions">
        {DESKTOP?<ControlDialog label="诊断读数">{advanced}</ControlDialog>:<details className="coupled-help"><summary>诊断读数</summary>{advanced}</details>}<button className="button danger" disabled={busy||stopPending.current||!bridgeReady||!run.running||run.mode!=='torque_sweep'||run.loadRequested||!['checking','ramping','awaiting_load'].includes(run.phase)}
        onClick={()=>action('stop',{pause:true})}>暂停电机输出</button></div>
    </div>
    <div className="coupled-device coupled-brake">
      <div className="coupled-device-title"><strong>② 制动力矩阶梯 · 固定 2 s</strong></div>
      <div className="hw-form-grid coupled-load-fields">{field('startNm','起始力矩 N·m',0,status.torqueCalibration?.maxTargetNm??99.9,.1)}{field('stepNm','力矩步长 N·m',.01,1,.01)}</div>
      {run.loadStartedAt!=null && <dl className="stage-progress">
        <div><dt>{run.running?'当前设定':'结束设定'}</dt><dd>{show(run.targetNm,2)} N·m</dd></div>
        <div><dt>最近完成</dt><dd>{show(points.at(-1)?.torque,2)} N·m</dd></div>
        <div><dt>已生成</dt><dd>{points.length} 点</dd></div>
      </dl>}
    </div>
    {run.running && <p className="run-status" role="status">{run.stopConfirmation?.state==='confirming'
      ? `保持制动 · 零速确认 ${show(run.stopConfirmation.durationS,1)} / 5 s`
      : `${phases[run.phase]||'运行中'} · ${Math.max(0,elapsed||0).toFixed(1)} s`}
      {run.loadStartedAt==null && Number.isFinite(outputSpeedRpm) && ` · ${show(outputSpeedRpm,1)} rpm`}</p>}
  </section>;
}
export default React.memo(CoupledCurrentPanel);
