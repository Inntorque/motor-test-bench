import React, {useEffect, useRef, useState} from 'react';
import {motorTargetCurrentA} from './model.js';
import {TORQUE_SWEEP_DEFAULTS,torqueSweepError,torqueSweepRequest} from './torqueSweep.js';

const KEY='motor-bench:torque-sweep:v3';
function savedForm() {
  try {
    const current=localStorage.getItem(KEY)||localStorage.getItem('motor-bench:torque-sweep:v2');
    const saved=JSON.parse(current||localStorage.getItem('motor-bench:torque-sweep:v1')||'{}');
    // Migrate the old nonzero default to the requested zero-load baseline,
    // while retaining the operator's torque step and holding interval.
    return {motorMode:saved.motorMode??'maximum_output',negativeIdMinA:String(saved.negativeIdMinA??0),maxPhaseA:String(saved.maxPhaseA??0),...Object.fromEntries(Object.entries(TORQUE_SWEEP_DEFAULTS).map(([key,value])=>[key,
      key==='startNm'&&!current?'0':String(saved?.[key]??value)]))};
  } catch {return {...TORQUE_SWEEP_DEFAULTS,motorMode:'maximum_output',negativeIdMinA:'0',maxPhaseA:'0'};}
}
const show=(value,digits=3)=>Number.isFinite(value)?value.toFixed(digits):'—';
const phases={idle:'未运行',checking:'检查设备',ramping:'电流参考拉升 · 制动零励磁',awaiting_load:'输出请求已启用 · 等待手动加载',loading:'目标力矩阶梯加载',stopping:'两路停机确认中',stopped:'已结束'};

function CoupledCurrentPanel({token,bridgeReady,sensorConnected,outputSpeedRpm,motorSupplyOn,curveHeld,onMotorState,onBrakeState,onEvent,onStart,onRunState}) {
  const [form,setForm]=useState(savedForm),[status,setStatus]=useState({run:{},motor:{},brake:{}});
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const [pauseRequested,setPauseRequested]=useState(false);
  const epoch=useRef(0),intent=useRef(0),pending=useRef(0),stopPending=useRef(false),ended=useRef(null);
  const callbacks=useRef({});callbacks.current={onMotorState,onBrakeState,onEvent,onStart,onRunState};
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
  useEffect(()=>{
    let active=true,polling=false;
    async function poll() {
      if(polling)return;polling=true;
      const generation=epoch.current;
      try {
        const response=await fetch('/api/coupled-current/status?heartbeat=1',{cache:'no-store',signal:AbortSignal.timeout(3000)});
        if(!response.ok)throw new Error('测试状态读取失败，请检查通信服务');
        const data=await response.json();
        if(active&&generation===epoch.current&&!pending.current){applyRef.current(data);setError(previous=>previous.startsWith('测试状态读取失败')?'':previous);}
      } catch(failure){if(active&&generation===epoch.current&&!pending.current)setError(failure.message);}
      finally{polling=false;}
    }
    void poll();const timer=window.setInterval(poll,400);
    return()=>{active=false;window.clearInterval(timer);};
  },[]);
  const run=status.run||{},supported=status.capabilities?.fullIqRamp===true;
  const selectedMode=run.running?run.motorMode:(form.motorMode??'maximum_output');
  const maximum=selectedMode==='maximum_output';
  const mx=status.motor?.snapshot?.maximumOutput;
  const maxFresh=mx&&Date.now()/1000-mx.receivedAt<2;
  const limitNames={current:'电流矢量',voltage:'电压',source_estimate:'估算供电电流',speed:'转速设置',adc_range:'ADC 量程'};
  const invalid=torqueSweepError(form,status.torqueCalibration);
  const blockedReason=!bridgeReady?'通信桥未连接。':!supported?'通信服务尚未支持新版流程。'
    :maximum&&status.capabilities?.maximumOutput!==true?'通信服务尚未加载最大输出模式，请重启服务。'
    :status.safety?.stopping||run.phase==='stopping'?'正在确认输出关闭，请等待。'
    :status.safety?.blocked||run.stopConfirmed===false?'输出关闭未确认，请先重试停止。'
    :!motorSupplyOn?'电机电源输出关闭，请先手动开启电机电源。'
    :!status.motor?.connected?`电机通信未就绪：${status.motor?.error||'请连接电机并等待新鲜反馈。'}`
    :maximum&&(status.motor?.snapshot?.versionRaw??0)<0x0A3C?'需先刷入 0x0A3C 或更新固件。'
    :!status.brake?.device?.connected?'制动电源未连接。':!sensorConnected?'扭矩传感器未连接。':'';
  const ready=!blockedReason;
  const canLoad=ready&&run.running&&!run.loadRequested&&run.mode==='torque_sweep'&&run.phase==='awaiting_load';
  async function action(name,{pause=false}={}) {
    if(pending.current&&name!=='stop')return;
    if(name==='stop'&&stopPending.current)return;
    if(name==='start'&&invalid){setError(invalid);return;}
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
      if(name!=='stop'||!token.current){
        const response=await fetch('/api/health',{cache:'no-store',signal:AbortSignal.timeout(3000)});
        if(!response.ok)throw new Error('通信桥不可用');
        token.current=(await response.json()).token;
      }
      const response=await fetch(`/api/coupled-current/${name}`,{method:'POST',
        headers:{'Content-Type':'application/json','X-MotorBench-Token':token.current},
        body:JSON.stringify(name==='start'?torqueSweepRequest(form):name==='load'?{id:run.id}:{}),signal:AbortSignal.timeout(15000)});
      const data=await response.json();
      // A delayed start/load response must never undo a newer stop in the UI.
      if(currentIntent!==intent.current)return;
      applyRef.current(data);
      if(!response.ok)throw new Error(data.error||'力矩测试操作失败');
      if(name==='start'){
        callbacks.current.onStart(data.run);
        callbacks.current.onEvent(`启动${data.run?.motorMode==='maximum_output'?'最大输出模式':'固定 10 A 模式'}，电流参考由驱动平滑拉升；制动保持零励磁。`);
      }
      if(name==='load')callbacks.current.onEvent(`开始力矩加载：起点 ${form.startNm} N·m，步长 ${form.stepNm} N·m，逐级加载至停转、保护或手动停止`);
      if(name==='stop'&&pause&&data.run?.stopConfirmed===true)callbacks.current.onEvent('满转已暂停：电机输出关闭已确认，制动未加载；再次测试需重新启动①。');
    }catch(failure){if(currentIntent===intent.current)setError(failure.message);}
    finally{epoch.current++;pending.current--;setBusy(pending.current>0);if(name==='stop')stopPending.current=false;}
  }
  const field=(key,label,min,max,step)=><label key={key}>{label}<input aria-label={label} type="number" value={form[key]} min={min} max={max} step={step}
    disabled={busy||run.running} onChange={event=>setForm(previous=>({...previous,[key]:event.target.value}))}/></label>;
  const brake=status.brake?.device,amp=brake?.connected?brake.snapshot?.measured?.[1]/10**Number(brake.config?.currentDecimals??3):null;
  return <section className="panel coupled-current-panel" aria-label="电机满Iq与力矩加载测试">
    <div className="control-title"><div><h2>最大输出 / 力矩加载</h2><span>先空载准备 · 再逐级加力矩</span></div></div>
    <div className="coupled-device">
      <div className="coupled-device-title"><strong>① 电机空载准备</strong><span>{maximum?'动态 Iq 参考':'固定 10 A 参考'}</span></div>
      <p className="hw-readback">目标 Iq {show(motorTargetCurrentA(status.motor))} A · 实际 {show(status.motor?.snapshot?.feedback?.stale?null:status.motor?.snapshot?.feedback?.iq_a)} A</p>
      <label>电机输出模式<select aria-label="电机输出模式" disabled={busy||run.running} value={selectedMode} onChange={event=>setForm(previous=>({...previous,motorMode:event.target.value}))}>
        <option value="maximum_output">最大输出（动态 Iq）</option><option value="full_current">固定 10 A（对照）</option></select></label>
      <p className="hw-safety-note">{maximum?'MCU 持续在现有电压、ADC 量程、相电流和供电能力内求最大 Iq；加载改变时自动重算，电压饱和与跟踪误差只记录。':'固定发送 10 A 目标，驱动按现有 100 A/s 斜坡拉升并保持。'}相电流保护 {status.motor?.snapshot?.supportedMaxA??'—'} A。①始终保持制动关闭。</p>
      {maximum&&<>
        <p className="hw-readback">{maxFresh?`可用电流矢量 ${show(mx.operatingCurrentA)} A · Id 参考 ${show(mx.idReferenceA)} A · 电压边界 ${show(mx.voltageLimitV)} V`:'等待新鲜的最大输出诊断（需要固件 0x0A3C）'}</p>
        {maxFresh&&<><p className="hw-readback">限制来源：{mx.limits.map(item=>limitNames[item]||item).join('、')||'电流拉升 / 暂无明确限制'} · 估算母线 {show(mx.estimatedBusA)} / {show(mx.busLimitA)} A</p>
          <p className="hw-readback">{mx.windowMs} ms 窗口：Iq {show(mx.iqMeanA)} A · Iq/Id 误差 RMS {show(mx.iqErrorRmsA)} / {show(mx.idErrorRmsA)} A · 饱和 {(mx.saturatedFraction*100).toFixed(0)}%</p>
          <p className="hw-safety-note">窗口证据：{mx.configuredBoundaryObserved?'已观察到配置边界且跟踪满足显示判据':'尚不足以确认已达到可用边界'}。质量标记：{mx.quality.join('、')||'本窗口未见已监测异常'}。达到已配置边界需同时核对跟踪误差、采样质量与电源实测；不能据此认定硬件绝对极限。</p></>}
        <details><summary>最大输出设置</summary><div className="hw-form-grid">{field('maxPhaseA','相电流运行上限 A（0=自动）',0,30,.1)}{field('negativeIdMinA','弱磁 Id 下限 A（0=关闭）',-30,0,.1)}</div>
          <p className="hw-safety-note">自动上限取现有保护与 ADC 可测范围并留跟踪余量。负 Id 需有电机允许值；当前默认关闭弱磁。供电限流沿用电源设定，本次设置不保存至 Flash。</p></details>
      </>}
      <div className="hw-output-actions"><button className="button danger" disabled={busy||stopPending.current||!bridgeReady||!run.running||run.mode!=='torque_sweep'||run.loadRequested||!['checking','ramping','awaiting_load'].includes(run.phase)}
        onClick={()=>action('stop',{pause:true})}>暂停电机输出</button></div>
    </div>
    <div className="coupled-device">
      <div className="coupled-device-title"><strong>② 制动力矩阶梯</strong><span>传感器闭环 · 24 V 限压</span></div>
      <div className="hw-form-grid">{field('startNm','起始力矩 N·m',0,99.9,.1)}{field('stepNm','力矩步长 N·m',.01,1,.01)}
        {field('intervalS','每级保持时间 s',.5,30,.5)}</div>
      <p className="hw-readback">目标 {show(run.targetNm,2)} N·m · 实测 {show(run.measuredNm,2)} N·m</p>
      <p className="hw-readback">自动电流 {show(run.brakeCurrentA)} A · 读回 {show(amp)} A</p>
      {!run.running&&invalid&&<p className="hw-inline-error" role="alert">{invalid}</p>}
      <p className="hw-safety-note">起点 0 表示制动关闭，先记录零励磁基线，再按步长加载；实测扭矩保留轴承背景。</p>
      <p className="hw-safety-note">实测首次达到或越过本级目标后计时保持，再加一级；波动不重置计时，未达到时继续闭环调整，不因等待稳定超时而卸载。直到编码器确认停转、保护或手动停止。仅扭矩传感器报零不会自动卸载。0.3–2.7 N·m 有局部参考，超出后仅按实测闭环调整，不外推。制动电流 &lt;2 A；机械背景约 ±0.2 N·m。</p>
    </div>
    <div className="hw-output-actions coupled-stage-actions">
      <button className="button primary" disabled={busy||run.running||!ready||!!invalid} onClick={()=>action('start')}>① {maximum?'启动最大输出':'启动 10 A 调流'}</button>
      <button className="button primary" disabled={busy||!canLoad} onClick={()=>action('load')}>② 开始力矩加载</button>
      <button className="button danger" disabled={stopPending.current||!bridgeReady} onClick={()=>action('stop')}>停止两路输出</button>
    </div>
    <p className="hw-readback" role="status">{phases[run.phase]||'未运行'} · {Number(run.elapsed||0).toFixed(1)} s · 实时输出转速 {show(outputSpeedRpm,1)} rpm</p>
    {run.recordingError&&<p className="hw-inline-error">记录异常：{run.recordingError}</p>}
    {run.recordingId&&<p className="hw-readback">完整记录：{run.recordingId}</p>}
    {run.reason&&<p className="hw-readback">结束原因：{run.reason}</p>}
    {run.stopConfirmed===true&&<p className="hw-readback">{pauseRequested?'已暂停：电机输出关闭已确认，制动未加载。再次运行请重新启动①。':'电机与制动输出关闭均已确认'}</p>}
    {run.stopConfirmed===false&&<p className="hw-inline-error" role="alert">两路关闭未全部确认：{(run.stopErrors||[]).join('；')}。请检查设备并重试停止。</p>}
    {status.safety?.blocked&&!status.safety?.stopping&&<p className="hw-inline-error">{status.safety.reason}；{status.safety.errors?.join('；')}</p>}
    {error&&<p className="hw-inline-error" role="alert">{error}</p>}
    {!supported&&<p className="hw-safety-note">新版测试流程待通信服务重新启动后可用。</p>}
    {!run.running&&supported&&!error&&<p className="hw-safety-note">{blockedReason||invalid||(curveHeld?'上一轮曲线已保留，可以直接重新启动①；新试验开始后自动切换曲线。':'参数已就绪，先启动①，待空载准备完成后再点击②。')}</p>}
    <details className="coupled-help"><summary>测试与记录说明</summary><p className="hw-safety-note">两个启动步骤独立确认；①不会自动加载。最大输出模式自动调整参考；固定模式保持 10 A。相电流保护仍按既有流程设置并读回 ID7，不保存到 Flash；实际电流以反馈为准。任一路保护、通信失效、页面离开或停转，均停止两路输出。无自动重新上电或重新启动。停机后曲线保留，可以直接重新启动①，或点击“恢复实时”查看实时曲线。特性曲线保留②之前 1 秒的空载点及之后的加载数据；最大输出模式自动关联完整记录；MCU 连续统计与停机前快速轨迹保存在固件运行记录中。目标值和传感器实测值分别记录，电流参考只作初始估计，反馈调整以轴端实测力矩为准。</p></details>
  </section>;
}
export default React.memo(CoupledCurrentPanel);
