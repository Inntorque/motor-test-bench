import React, {useEffect, useRef, useState} from 'react';
import {motorStepInputError} from './model.js';
import {brakeModeText} from './supplyPolicy.js';

const FORM_KEY='motor-bench:brake-step:form';
function savedForm() {
  try {
    const saved=JSON.parse(localStorage.getItem(FORM_KEY)||'{}');
    return Object.fromEntries(['stepA','intervalS','durationS'].map(key=>[key,String(saved?.[key]??'')]));
  } catch {return {stepA:'',intervalS:'',durationS:''};}
}

export default function BrakeCurrentPanel({token,onState,onEvent,children}) {
  const [mode,setMode]=useState('manual');
  const [form,setForm]=useState(savedForm);
  const [run,setRun]=useState({});
  const [device,setDevice]=useState({connected:false});
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  const [error,setError]=useState('');
  const stopPending=useRef(false);
  const actionEpoch=useRef(0);
  const pendingActions=useRef(0);
  const callback=useRef(onState); callback.current=onState;
  useEffect(()=>{try {localStorage.setItem(FORM_KEY,JSON.stringify(form));}catch {}},[form]);
  useEffect(()=>{
    let active=true,polling=false;
    async function poll() {
      if(polling)return;
      polling=true;
      const epoch=actionEpoch.current;
      try {
        const response=await fetch('/api/brake-current/status',{cache:'no-store',signal:AbortSignal.timeout(2500)});
        if(!response.ok)throw new Error('制动步进通信不可用');
        const data=await response.json();
        if(active && epoch===actionEpoch.current && !pendingActions.current){
          setRun(data.run||{});setDevice(data.device||{connected:false});
          setError(previous=>previous.startsWith('制动步进状态读取失败')?'':previous);
          if(data.run?.running&&data.run.mode!=='torque')setMode('step');
        }
      }catch {if(active && epoch===actionEpoch.current && !pendingActions.current)setError('制动步进状态读取失败；可尝试停止制动输出。');}
      finally {polling=false;}
    }
    void poll(); const timer=window.setInterval(poll,500);
    return ()=>{active=false;window.clearInterval(timer);};
  },[]);
  async function action(name) {
    if(name==='stop' && stopPending.current)return;
    if(name==='start') {
      const invalid=motorStepInputError(form,2.3) || (Number(form.durationS)<=Number(form.intervalS)?'持续时间必须大于步进间隔。':'');
      if(invalid){setError(invalid);return;}
    }
    if(name==='stop')stopPending.current=true;
    actionEpoch.current++;pendingActions.current++;
    setBusy(true);setError('');setMessage(name==='start'?'正在启动制动电流步进…':'正在关闭制动输出…');
    try {
      if(name!=='stop'||!token.current){
        const health=await fetch('/api/health',{cache:'no-store',signal:AbortSignal.timeout(2500)});
        if(!health.ok)throw new Error('通信桥不可用');
        token.current=(await health.json()).token;
      }
      const response=await fetch(`/api/brake-current/${name}`,{
        method:'POST',headers:{'Content-Type':'application/json','X-MotorBench-Token':token.current},
        body:JSON.stringify(name==='start'?Object.fromEntries(Object.entries(form).map(([k,v])=>[k,Number(v)])):{}),
        signal:AbortSignal.timeout(15000),
      });
      const data=await response.json();
      if(data.run)setRun(data.run);
      if(data.device){setDevice(data.device);callback.current(data.device);}
      if(!response.ok)throw new Error(data.error||'制动步进操作失败');
      if(name==='stop' && data.run?.stopConfirmed!==true)throw new Error('关闭输出尚未确认，请检查电源');
      const text=name==='start'?'制动步进已启动。':'制动输出关闭已确认。';
      setMessage(text);onEvent(text);
    }catch(failure){setMessage('');setError(failure.message);}
    finally{actionEpoch.current++;pendingActions.current--;setBusy(false);if(name==='stop')stopPending.current=false;}
  }
  const measured=device.connected?device.snapshot?.measured?.[1]:null;
  const current=Number.isFinite(measured)?(measured/10**Number(device.config?.currentDecimals??3)).toFixed(3):'—';
  if(run.running&&run.mode==='torque')return <div className="brake-control-panel">
    <p className="hw-readback">力矩测试已接管制动电源 · 目标 {Number(run.targetNm||0).toFixed(2)} N·m · 实际电流 {current} A</p>
    <p className="hw-safety-note">加载由曲线旁的两阶段测试统一控制。</p>
    <button className="button danger" onClick={()=>action('stop')}>停止两路输出</button>
    {error&&<p className="hw-inline-error" role="alert">{error}</p>}
  </div>;
  return <div className="brake-control-panel">
    <div className="hw-output-actions brake-mode-tabs" aria-label="制动控制模式">
      <button className={`button ${mode==='manual'?'primary':''}`} disabled={busy||run.running} aria-pressed={mode==='manual'} onClick={()=>setMode('manual')}>手动定值</button>
      <button className={`button ${mode==='step'?'primary':''}`} disabled={busy||run.running} aria-pressed={mode==='step'} onClick={()=>setMode('step')}>电流步进</button>
    </div>
    {mode==='manual'?children:<div className="brake-step-controls">
      <div className="hw-form-grid">
        {[
          ['stepA','制动电流步长 A','0.001','0.001','2.3'],
          ['intervalS','制动步进间隔 s','0.1','0.2','10'],
          ['durationS','制动持续时间 s','0.1','1','255'],
        ].map(([key,label,step,min,max])=><label key={key}>{label}<input type="number" aria-label={label}
          step={step} min={min} max={max} value={form[key]} placeholder="请输入" disabled={busy||run.running}
          onChange={event=>setForm(previous=>({...previous,[key]:event.target.value}))}/></label>)}
      </div>
      <p className="hw-safety-note">从 0 A 开始，每隔指定秒数增加一次电流，最高 2.3 A 后保持。电压上限固定 24 V；到期、通信异常或离开本页超过 3 秒均关闭制动输出。启动前请先关闭已有制动输出。</p>
      <div className="hw-output-actions">
        <button className="button primary" disabled={busy||run.running} onClick={()=>action('start')}>{run.running?'制动步进运行中':'开始制动电流步进'}</button>
        <button className="button danger" onClick={()=>action('stop')}>停止制动输出</button>
      </div>
      <p className="hw-readback">{run.running?'运行中':'未运行'} · 已步进 {run.steps||0} 次 · {Number(run.elapsed||0).toFixed(1)} s · 当前目标 {Number(run.targetA||0).toFixed(3)} A</p>
      <p className="hw-readback">实际电流 {current} A · {brakeModeText(device)}</p>
      {run.limitReached&&<p className="hw-readback">已达到限幅，保持到持续时间结束。</p>}
      {run.reason&&<p className="hw-readback">结束原因：{run.reason}</p>}
      {run.stopConfirmed===false&&!run.running&&<p className="hw-inline-error">制动输出关闭未确认，请现场关闭电源。</p>}
    </div>}
    {message&&<p className="hw-readback" role="status">{message}</p>}
    {error&&<p className="hw-inline-error" role="alert">{error}</p>}
  </div>;
}
