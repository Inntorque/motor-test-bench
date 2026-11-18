import React, {useEffect,useRef,useState} from 'react';
import {CharacteristicPlot,characteristicPlotData} from './CharacteristicPlot.tsx';
import ControlDialog from './ControlDialog.tsx';
const DESKTOP=Boolean(globalThis.window?.inntorqueDesktop);
const AXIS_KEY='motor-bench:characteristic:limits';
function savedAxisLimits() {
  try {
    const saved=JSON.parse(localStorage.getItem(AXIS_KEY)||'{}');
    const positive=(value,fallback)=>Number.isFinite(Number(value))&&Number(value)>0?String(value):fallback;
    const legacy=saved?.schema!==2;
    return {torque:legacy&&Number(saved?.torque)===5?'':positive(saved?.torque,''),
      power:legacy&&Number(saved?.power)===50?'':positive(saved?.power,'')};
  } catch {
    return {torque:'',power:''};
  }
}

function TorqueCharacteristics({loadRun}) {
  const chart=useRef(null), viewport=useRef<HTMLDivElement>(null);
  const [size,setSize]=useState({width:1040,height:790});
  useEffect(()=>{
    if(!DESKTOP)return;
    const observer=new ResizeObserver(([entry])=>{
      const {width,height}=entry.contentRect;
      if(width>0&&height>0)setSize({width:Math.max(640,Math.round(width)),height:Math.max(200,Math.round(height))});
    });
    observer.observe(viewport.current);
    return()=>observer.disconnect();
  },[]);
  const [initialLimits]=useState(savedAxisLimits);
  const [torqueLimit,setTorqueLimit]=useState(initialLimits.torque),[speedLimit,setSpeedLimit]=useState('');
  const [powerLimit,setPowerLimit]=useState(initialLimits.power);
  const [lineMode,setLineMode]=useState('measured'),[fitRate,setFitRate]=useState(60),[expanded,setExpanded]=useState(false);
  useEffect(()=>{
    try { localStorage.setItem(AXIS_KEY, JSON.stringify({schema:2,torque:torqueLimit,power:powerLimit})); } catch { /* Storage may be unavailable. */ }
    window.dispatchEvent(new CustomEvent('motor-bench:characteristic-limits',{detail:{torque:torqueLimit,power:powerLimit}}));
  },[torqueLimit,powerLimit]);
  useEffect(()=>{
    if(!expanded)return;
    const close=event=>{if(event.key==='Escape'&&!document.querySelector('dialog[open]'))setExpanded(false);};
    window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);
  },[expanded]);
  const model=characteristicPlotData(loadRun,torqueLimit,powerLimit,speedLimit);
  const {points,autoTorque,autoPower,autoRpmBound,bound,series}=model;
  const clipped=series.filter(s=>points.some(p=>p[s.key]>s.upper)).map(s=>s.label);
  if(points.some(p=>p.torque>bound))clipped.unshift('扭矩');
  const value=(n,digits=3)=>Number.isFinite(n)?n.toFixed(digits):'—';
  function exportChart() {
    if(!chart.current)return;
    const copy=chart.current.cloneNode(true);
    const original=[chart.current,...chart.current.querySelectorAll('*')];
    const copied=[copy,...copy.querySelectorAll('*')];
    const properties=['font-family','font-size','font-weight','font-style','fill','stroke','stroke-width','stroke-dasharray','text-anchor','dominant-baseline','opacity'];
    original.forEach((element,i)=>{
      const style=getComputedStyle(element);
      properties.forEach(key=>copied[i].style.setProperty(key,style.getPropertyValue(key)));
    });
    copy.querySelector('[data-chart-inspector]')?.remove();
    copy.setAttribute('xmlns','http://www.w3.org/2000/svg');copy.setAttribute('width',String(size.width));copy.setAttribute('height',String(size.height));
    const url=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(copy)],{type:'image/svg+xml;charset=utf-8'}));
    const link=document.createElement('a');link.href=url;link.download=`motor-characteristic-${loadRun?.id||'measured'}.svg`;link.click();
    window.setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  const axisTools=<div className="characteristic-tools characteristic-axis-tools">
    <label>制动输出设定扭矩上限 N·m <input aria-label="扭矩横轴上限 N·m" type="number" min="0.01" step="0.1" value={torqueLimit} placeholder={`自动（${autoTorque}）`} title="修改后自动保存，下次打开自动恢复" onChange={e=>setTorqueLimit(e.target.value)}/></label>
    <label>转速纵轴上限 r/min <input aria-label="转速纵轴上限 r/min" type="number" min="1" step="10" value={speedLimit}
      placeholder={`自动（${autoRpmBound}）`} title="留空时按实测转速自动调整量程" onChange={e=>setSpeedLimit(e.target.value)}/></label>
    <label>输出功率轴上限 W <input aria-label="输出功率轴上限 W" type="number" min="0.1" step="10" value={powerLimit} placeholder={`自动（${autoPower}）`} title="修改后自动保存，下次打开自动恢复" onChange={e=>setPowerLimit(e.target.value)}/></label>
    <label>显示方式 <select aria-label="特性曲线连线方式" value={lineMode} onChange={e=>setLineMode(e.target.value)}><option value="scatter">离散采样点</option><option value="measured">按输出档位连线（默认）</option><option value="linear">线性趋势（拟合）</option><option value="curve">曲线拟合（可调拟合率）</option></select></label>
    {lineMode==='curve'&&<label>拟合率
      <input aria-label="曲线拟合率" type="range" min="0" max="100" step="1" value={fitRate}
        onChange={e=>setFitRate(Number(e.target.value))} style={{width:160}}/>
      <span>{fitRate}%</span></label>}
  </div>;
  const pointTable=()=><div className="characteristic-table"><table><thead><tr>{['序号','计划时间 s','实测时间 s','采样偏差 ms','实测点间隔 s','制动输出设定 N·m','转速 rpm','U V','I A','Po W','Pi W','效率 %','样本数','类型'].map(label=><th key={label}>{label}</th>)}</tr></thead>
    <tbody>{points.map((point,index)=><tr key={`${point.stage}-${point.kind}-${point.receivedAt}`}><td>{index+1}</td><td>{point.scheduledAt!=null?value(point.scheduledAt-loadRun.loadStartedAt):'—'}</td><td>{value(point.receivedAt-loadRun.loadStartedAt)}</td><td>{point.scheduledAt!=null?value((point.receivedAt-point.scheduledAt)*1000,1):'—'}</td><td>{index?value(point.receivedAt-points[index-1].receivedAt):'—'}</td>{['torque','speed','voltage','current','power','inputPower','efficiency'].map(key=><td key={key}>{value(point[key])}</td>)}<td>{point.sampleCount}</td><td>{point.kind==='stall'?'确认堵转':'级末汇总'}</td></tr>)}</tbody></table>
    {!points.length&&<p>尚无有效加载采样点。</p>}</div>;
  return <div className={`torque-characteristics ${expanded?'characteristic-expanded':''}`}>
    <div className="characteristic-tools"><button className="button small" onClick={()=>setExpanded(!expanded)}>{expanded?'退出放大':'放大查看'}</button>
      {loadRun&&!loadRun.running&&points.length>0&&<button className="button small" onClick={exportChart}>导出本次特性图 SVG</button>}
      {DESKTOP&&<ControlDialog label="坐标与连线">{axisTools}</ControlDialog>}
      <ControlDialog label={`采样点表（${points.length}）`}>{pointTable}</ControlDialog>
      {!points.length&&<span>{loadRun?.running && loadRun.loadStartedAt ? "等待首个档位完成" : loadRun?.running===false ? "无有效档位" : "等待加载"}</span>}</div>
    {!DESKTOP&&axisTools}
    {DESKTOP&&clipped.length>0&&<div className="characteristic-range-warning">{clipped.join("、")}超出显示量程</div>}
    <div ref={viewport} className="characteristic-scroll"><CharacteristicPlot chartRef={chart} loadRun={loadRun} model={model} size={size} desktop={DESKTOP} interactive lineMode={lineMode} fitRate={fitRate}/></div>
  </div>;
}
export default React.memo(TorqueCharacteristics);
