import React, { useEffect, useState } from 'react';
import AutomaticTestsPanel from './AutomaticTestsPanel.jsx';

const TYPES = {
  baseline: ['零加载基线', '关闭制动励磁，记录有符号扭矩均值、标准差、峰峰值、RMS 和实际转速。'],
  ripple: ['稳态波动', '先在工作台保持工况稳定，统计转矩与转速的峰峰波动率、变异系数。'],
  temperature: ['NTC 温度记录', '记录电机已有 NTC 测点的初温、末温、最高温和温度变化；填写环境温度后计算末次温升。'],
};
const STATES = {settling:'等待稳定',running:'正在采集',completed:'采集完成',stopped:'手动结束',interrupted:'采集中断'};
const show = (n, digits=3) => Number.isFinite(n) ? n.toFixed(digits) : '—';
const running = record => ['running','settling'].includes(record?.state);

function PreviewChart({ report }) {
  const [chosen,setChosen] = useState('');
  const metric=chosen || (report.kind==='temperature' ? 'temperature' : 'torque');
  const rows=report.preview?.[metric] || [];
  const unit={torque:'N·m',speed:'rpm',temperature:'°C'}[metric];
  let low=0, high=1;
  if(rows.length) {
    const values=rows.map(row=>row[1]);
    if(metric==='temperature') {low=Math.floor(Math.min(...values))-1;high=Math.ceil(Math.max(...values))+1;}
    else if(metric==='torque') {high=Math.max(.01,...values.map(Math.abs))*1.1;low=-high;}
    else high=Math.max(1,...values)*1.1;
  }
  const end=Math.max(1,rows.at(-1)?.[0] || 0);
  const path=rows.map(([t,value],index)=>`${index?'L':'M'}${50+t/end*640},${175-(value-low)/(high-low)*145}`).join(' ');
  return <section className="special-preview" aria-label="专项曲线预览">
    <label>预览指标 <select aria-label="专项曲线预览指标" value={metric} onChange={event=>setChosen(event.target.value)}><option value="torque">扭矩</option><option value="speed">转速</option><option value="temperature">NTC 温度</option></select></label>
    {rows.length ? <svg viewBox="0 0 720 215" role="img" aria-label={`专项${metric}曲线，单位${unit}`}>
      {[0,.5,1].map(v=><g key={v}><line x1="50" x2="690" y1={30+v*145} y2={30+v*145} stroke="#d4dce7"/><text x="42" y={34+v*145} textAnchor="end">{show(high-v*(high-low),2)}</text></g>)}
      <path d={path} fill="none" stroke="#1455c0" strokeWidth="2.5" vectorEffect="non-scaling-stroke"/><text x="50" y="200">0 s</text><text x="690" y="200" textAnchor="end">{show(end,1)} s</text>
    </svg> : <p>该指标暂无有效预览数据。</p>}
    <p>预览约每 0.5 秒抽点并压缩，完整原始数据保存在 CSV，统计使用全部有效采集点。</p>
  </section>;
}

function SpecializedTestsPanel({ token, bridgeReady, onWorkbench }) {
  const [form,setForm] = useState({kind:'baseline',name:'',durationS:'10',settleS:'2',ambientC:'',
    torqueMeanFloorNm:'0.01',speedMeanFloorRpm:'1',manualBrakeOff:false,steadyConfirmed:false});
  const [data,setData] = useState({current:null,records:[],availability:{}});
  const [selectedId,setSelectedId] = useState('');
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  const [section,setSection] = useState('automatic');
  const patch = change => setForm(previous=>({...previous,...change}));
  async function getStatus() {
    const response = await fetch('/api/specialized/status',{cache:'no-store',signal:AbortSignal.timeout(4000)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '专项测试服务不可用');
    return result;
  }
  useEffect(()=>{
    let active=true, inFlight=false;
    async function poll() {
      if(inFlight) return;
      inFlight=true;
      try { const result=await getStatus(); if(active) {setData(result);setError('');} }
      catch(failure) { if(active) setError(failure.message); }
      finally {inFlight=false;}
    }
    void poll();
    const timer=setInterval(poll,1000);
    return ()=>{active=false;clearInterval(timer);};
  },[]);
  const current=data.current;
  const active=running(current);
  const availability=data.availability || {};
  const problems=[];
  if(!bridgeReady) problems.push('本机通信桥未就绪');
  if(form.kind==='temperature') {if(!availability.temperature) problems.push('电机未连接或 NTC 读数无效/过期');}
  else if(!availability.sensor) problems.push('请先连接扭矩传感器');
  if(form.kind==='baseline') {
    if(availability.brakeOutput===true) problems.push('制动电源仍在输出');
    if(availability.brakeOutput==null && !form.manualBrakeOff) problems.push('需确认制动励磁已关闭');
  }
  if(form.kind==='ripple' && !form.steadyConfirmed) problems.push('需确认工况已稳定');
  const valid = Number(form.durationS)>=1 && Number(form.durationS)<=3600 && form.durationS.trim()!=='' &&
    form.settleS.trim()!=='' && Number(form.settleS)>=0 && Number(form.settleS)<=300 &&
    (form.ambientC==='' || (Number(form.ambientC)>=-40 && Number(form.ambientC)<=100)) &&
    Number(form.torqueMeanFloorNm)>=0 && Number(form.torqueMeanFloorNm)<=50 &&
    Number(form.speedMeanFloorRpm)>=0 && Number(form.speedMeanFloorRpm)<=10000;
  async function action(name) {
    setBusy(true);setError('');
    try {
      const payload=name==='stop' ? {id:current.id} : {...form,
        name:form.name.trim() || TYPES[form.kind][0], durationS:Number(form.durationS),settleS:Number(form.settleS),
        ambientC:form.ambientC===''?null:Number(form.ambientC),
        torqueMeanFloorNm:Number(form.torqueMeanFloorNm),speedMeanFloorRpm:Number(form.speedMeanFloorRpm)};
      const response=await fetch(`/api/specialized/${name}`,{method:'POST',
        headers:{'Content-Type':'application/json','X-MotorBench-Token':token.current},body:JSON.stringify(payload)});
      const result=await response.json();
      if(!response.ok) throw new Error(result.error || '专项测试操作失败');
      setSelectedId(result.current.id);
      setData(await getStatus());
    } catch(failure) {setError(failure.message);}
    finally {setBusy(false);}
  }
  const report=current && (!selectedId || current.id===selectedId) ? current
    : (data.records || []).find(r=>r.id===selectedId) || data.records?.[0];
  const metrics=report?.stats || {};
  const download = (id,name) => `/api/specialized/files/${id}/${name}`;
  return <section className="specialized-tests" aria-label="专项测试">
    <div className="panel special-intro"><h2>专项测试</h2><p>空载与带载脚本按顺序控制电机并保存结果；被动测量可独立记录现有工况。</p>
      <div className="hw-output-actions"><button className={`button ${section==='automatic'?'primary':''}`} onClick={()=>setSection('automatic')}>空载 / 带载自动测试</button>
        <button className={`button ${section==='passive'?'primary':''}`} onClick={()=>setSection('passive')}>被动测量与统计{active?' · 正在采集':''}</button>
        <button className="button" onClick={()=>onWorkbench('bench')}>返回工作台控制设备</button></div></div>
    <div hidden={section!=='automatic'}><AutomaticTestsPanel token={token} bridgeReady={bridgeReady}/></div>
    <div hidden={section!=='passive'}><p className="setpoint-hint">被动采集只管理记录，电机和电源输出由工作台控制。请先设置运行工况。</p>
    <div className="special-layout">
      <section className="panel special-config"><h3>测试配方</h3>
        <div className="hw-form-grid">
          <label>测试项目<select aria-label="专项测试项目" value={form.kind} disabled={active || busy} onChange={e=>patch({kind:e.target.value})}>{Object.entries(TYPES).map(([id,[label]])=><option key={id} value={id}>{label}</option>)}</select></label>
          <label>记录名称<input value={form.name} maxLength={100} placeholder={TYPES[form.kind][0]} disabled={active || busy} onChange={e=>patch({name:e.target.value})}/></label>
          <label>有效采集时长 s<input aria-label="专项采集时长 s" type="number" min="1" max="3600" value={form.durationS} disabled={active || busy} onChange={e=>patch({durationS:e.target.value})}/></label>
          <label>稳定等待 s<input aria-label="专项稳定等待 s" type="number" min="0" max="300" value={form.settleS} disabled={active || busy} onChange={e=>patch({settleS:e.target.value})}/></label>
          {form.kind==='temperature' && <label>环境温度 °C（可选）<input type="number" min="-40" max="100" step="0.1" value={form.ambientC} disabled={active || busy} onChange={e=>patch({ambientC:e.target.value})}/></label>}
        </div>
        <p>{TYPES[form.kind][1]}</p>
        {form.kind==='baseline' && <label className="special-confirm"><input type="checkbox" checked={form.manualBrakeOff} disabled={active || busy} onChange={e=>patch({manualBrakeOff:e.target.checked})}/>我已在现场确认制动励磁供电关闭或断开（人工确认，会随报告保存）</label>}
        {form.kind==='ripple' && <label className="special-confirm"><input type="checkbox" checked={form.steadyConfirmed} disabled={active || busy} onChange={e=>patch({steadyConfirmed:e.target.checked})}/>工况已经稳定，采集期间不调整目标电流和制动设定</label>}
        <details><summary>波动率计算设置</summary><div className="hw-form-grid">
          <label>转矩归一化下限 N·m<input type="number" min="0" max="50" step="0.01" value={form.torqueMeanFloorNm} disabled={active || busy} onChange={e=>patch({torqueMeanFloorNm:e.target.value})}/></label>
          <label>转速归一化下限 rpm<input type="number" min="0" max="10000" step="1" value={form.speedMeanFloorRpm} disabled={active || busy} onChange={e=>patch({speedMeanFloorRpm:e.target.value})}/></label>
        </div><p>均值绝对值不高于下限时只报告绝对波动；峰峰波动率 = (最大−最小)/|均值|，变异系数 = 标准差/|均值|。</p></details>
        {!active && problems.length>0 && <ul className="special-problems">{problems.map(p=><li key={p}>{p}</li>)}</ul>}
        {error && <p className="hw-inline-error" role="alert">{error}</p>}
        <div className="hw-output-actions"><button className="button primary" disabled={busy || active || problems.length>0 || !valid} onClick={()=>action('start')}>开始专项采集</button>
          <button className="button" disabled={busy || !active} onClick={()=>action('stop')}>结束采集</button></div>
        {current && <p role="status">{STATES[current.state]} · {show(current.elapsedS,1)} s · 扭矩/转速 {current.sensorSamples} 帧 · NTC 快照 {current.temperatureSamples} 次</p>}
        <p className="setpoint-hint">稳定等待期间保留原始数据，不计入统计。断线、帧丢失、零加载时励磁开启等情况会中断记录。1000 帧/秒是通信速率。</p>
      </section>
      <section className="panel special-result"><h3>统计结果</h3>
        {!report ? <p>完成采集后显示结果，记录保存在本机工程 recordings/specialized 目录。</p> : <>
          <h4>{report.name}</h4><p>{STATES[report.state]} · {report.reason || '采集中'}{!running(report) && ` · ${report.valid ? '数据可用于统计' : '记录不完整或工况不满足要求'}`}</p>
          <div className="records-table-wrap"><table><thead><tr><th>指标</th><th>有效点</th><th>均值</th><th>最小</th><th>最大</th><th>标准差</th><th>RMS</th><th>峰峰值</th></tr></thead><tbody>
            {[['torque','扭矩 N·m'],['speed','转速 rpm'],['powerW','轴功率 W'],['actualIq','实际 Iq A'],['targetIq','目标 Iq A'],['temperature','NTC °C']].map(([key,label])=><tr key={key}><th>{label}</th><td>{metrics[key]?.count || 0}</td>{['mean','min','max','std','rms','peakToPeak'].map(field=><td key={field}>{show(metrics[key]?.[field])}</td>)}</tr>)}
          </tbody></table></div>
          <p>转矩峰峰波动率：{show(metrics.torque?.peakToPeakPercent)} % · 变异系数：{show(metrics.torque?.cvPercent)} %</p>
          <p>转速峰峰波动率：{show(metrics.speed?.peakToPeakPercent)} % · 变异系数：{show(metrics.speed?.cvPercent)} %</p>
          <p>NTC 温度变化：{show(report.temperatureChangeC,1)} °C · 末次相对环境温升：{show(report.endRiseAboveAmbientC,1)} °C</p>
          <PreviewChart key={report.id} report={report} />
          <p>结果为描述性统计，尚未设置合格阈值；NTC 仅代表电机现有测点，未判定热稳定或额定持续能力。</p>
          {!running(report) && <div className="hw-output-actions"><a className="button" href={download(report.id,'data.csv')} download>导出专项原始 CSV</a><a className="button" href={download(report.id,'report.json')} download>导出结果报告 JSON</a></div>}
        </>}
      </section>
    </div>
    <section className="panel special-history"><h3>专项测试记录</h3><div className="records-table-wrap"><table><thead><tr><th>时间</th><th>项目 / 名称</th><th>状态</th><th>数据</th><th/></tr></thead><tbody>
      {(data.records || []).map(record=><tr key={record.id}><td>{new Date(record.startedAt*1000).toLocaleString()}</td><td>{TYPES[record.kind]?.[0]} / {record.name}</td><td>{STATES[record.state]}</td><td>{record.sensorSamples} 帧 / {record.temperatureSamples} 次温度</td><td><button className="button small" onClick={()=>setSelectedId(record.id)}>查看结果</button></td></tr>)}
    </tbody></table></div>{!data.records?.length && <p>暂无专项测试记录。</p>}</section></div>
  </section>;
}
export default React.memo(SpecializedTestsPanel);
