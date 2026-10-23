import React, {useEffect,useId,useMemo,useRef,useState} from 'react';
import {CHARACTERISTIC_COLUMNS,CharacteristicWindow,curveCharacteristic,linearCharacteristic,measuredStopPoint} from './characteristicData.js';
import {MOTOR_SUPPLY_CURRENT_LIMIT,SUPPLY_VOLTAGE} from './supplyPolicy.js';
import {measuredBrakeLoad} from './loadTrial.js';
const tick=value=>Number(value.toPrecision(4));
const SERIES=[
  {key:'voltage',label:'母线电压',symbol:'U(V)',upper:24,color:'#7565ff',axis:34,side:'left',labelAt:.3,dy:19},
  {key:'power',label:'输出轴功率',symbol:'Po(W)',upper:50,color:'#f07800',axis:100,side:'left',labelAt:.7,dy:-12},
  {key:'speed',label:'输出转速',symbol:'n(r/min)',upper:300,color:'#fb7278',axis:168,side:'left',labelAt:.93,dy:-13},
  {key:'current',label:'母线输入电流',symbol:'I(A)',upper:10,color:'#c000c0',axis:850,side:'right',labelAt:.55,dy:-12},
  {key:'inputPower',label:'输入功率',symbol:'Pi(W)',upper:SUPPLY_VOLTAGE*MOTOR_SUPPLY_CURRENT_LIMIT,color:'#1627ed',axis:921,side:'right',labelAt:.3,dy:20},
  {key:'efficiency',label:'效率',symbol:'EFF(%)',upper:100,color:'#00bac4',axis:1000,side:'right',labelAt:.6,dy:20},
];
const LEFT=200,RIGHT=820,TOP=100,BOTTOM=720;
const AXIS_KEY='motor-bench:characteristic:limits';
function savedAxisLimits() {
  try {
    const saved=JSON.parse(localStorage.getItem(AXIS_KEY)||'{}');
    const positive=(value,fallback)=>Number.isFinite(Number(value))&&Number(value)>0?String(value):fallback;
    return {torque:positive(saved?.torque,'5'),power:positive(saved?.power,'50')};
  } catch {
    return {torque:'5',power:'50'};
  }
}

function TorqueCharacteristics({samples,windowSeconds,loadRun}) {
  const chart=useRef(null);
  const clipId=useId().replace(/:/g,'');
  const [initialLimits]=useState(savedAxisLimits);
  const [torqueLimit,setTorqueLimit]=useState(initialLimits.torque),[speedLimit,setSpeedLimit]=useState('300');
  const [powerLimit,setPowerLimit]=useState(initialLimits.power);
  const bound=Number.isFinite(Number(torqueLimit))&&Number(torqueLimit)>0?Number(torqueLimit):5;
  const rpmBound=Number.isFinite(Number(speedLimit))&&Number(speedLimit)>0?Number(speedLimit):300;
  const outputBound=Number.isFinite(Number(powerLimit))&&Number(powerLimit)>0?Number(powerLimit):50;
  const [lineMode,setLineMode]=useState('measured'),[fitRate,setFitRate]=useState(60),[expanded,setExpanded]=useState(false);
  useEffect(()=>{
    try { localStorage.setItem(AXIS_KEY, JSON.stringify({torque: torqueLimit, power: powerLimit})); } catch { /* Storage may be unavailable. */ }
    window.dispatchEvent(new CustomEvent('motor-bench:characteristic-limits',{detail:{torque:torqueLimit,power:powerLimit}}));
  },[torqueLimit,powerLimit]);
  useEffect(()=>{
    if(!expanded)return;
    const close=event=>{if(event.key==='Escape')setExpanded(false);};
    window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);
  },[expanded]);
  const statistics=useRef(null);
  if(!statistics.current)statistics.current=new CharacteristicWindow();
  const points=useMemo(()=>{
    const rows=statistics.current.update(samples,windowSeconds);
    const maxTorque=rows.reduce((max,row)=>Math.max(max,row.torque/row.count),0);
    const step=Math.max(.001,maxTorque/600),bins=new Map();
    for(const row of rows){
      const key=Math.floor(row.torque/row.count/step);
      let bin=bins.get(key);
      if(!bin){bin={torque:0,count:0,sums:Array(6).fill(0),counts:Array(6).fill(0)};bins.set(key,bin);}
      bin.torque+=row.torque;bin.count+=row.count;
      for(let i=0;i<6;i++){bin.sums[i]+=row.sums[i];bin.counts[i]+=row.counts[i];}
    }
    return [...bins.entries()].sort((a,b)=>a[0]-b[0]).map(([,b])=>({torque:b.torque/b.count,
      ...Object.fromEntries(CHARACTERISTIC_COLUMNS.map((key,i)=>[key,b.counts[i]?b.sums[i]/b.counts[i]:null]))}));
  },[samples,windowSeconds]);
  const series=SERIES.map(s=>s.key==='speed'?{...s,upper:rpmBound}:s.key==='power'?{...s,upper:outputBound}:s);
  const load=useMemo(()=>measuredBrakeLoad(samples),[samples]);
  const stop=measuredStopPoint(loadRun);
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
    copy.setAttribute('xmlns','http://www.w3.org/2000/svg');copy.setAttribute('width','1040');copy.setAttribute('height','790');
    const background=document.createElementNS('http://www.w3.org/2000/svg','rect');
    for(const [key,value] of Object.entries({width:1040,height:790,fill:'white'}))background.setAttribute(key,String(value));
    copy.prepend(background);
    const url=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(copy)],{type:'image/svg+xml;charset=utf-8'}));
    const link=document.createElement('a');link.href=url;link.download=`motor-characteristic-${loadRun?.id||'measured'}.svg`;link.click();
    window.setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  return <div className={`torque-characteristics ${expanded?'characteristic-expanded':''}`}>
    {loadRun&&<div className="characteristic-load" aria-label="本次曲线制动负载">
      <strong>{loadRun.mode==='torque_sweep'?`目标制动力矩 ${value(loadRun.targetNm,2)} N·m · Iq 满载扫描`:`固定制动 ${value(Number(loadRun.brake?.currentA))} A · 24 V 上限`}</strong>
      <span>制动实测 {value(load?.current)} A / {value(load?.voltage,2)} V · 轴端负载 {value(load?.torque,2)} N·m{load?(load.steady?'（近 1 s 稳态均值）':load.moving?'（非稳态均值）':'（停转时轴端读数）'):''}</span>
      <small>FZD-10 额定 100 N·m · {loadRun.mode==='torque_sweep'?'局部实测参考＋传感器反馈':'轴端负载为传感器实测'}{loadRun.running?'':' · 试验快照已保留'}</small>
    </div>}
    <div className="characteristic-tools"><button className="button small" onClick={()=>setExpanded(!expanded)}>{expanded?'退出放大':'放大查看'}</button>
      {loadRun&&!loadRun.running&&points.length>0&&<button className="button small" onClick={exportChart}>导出本次特性图 SVG</button>}
      <span>24 V DC · 母线预设限流 10 A · U / Po / n / I / Pi / EFF 六曲线</span></div>
    <div className="characteristic-tools characteristic-axis-tools">
      <label>扭矩横轴上限 N·m <input aria-label="扭矩横轴上限 N·m" type="number" min="0.01" step="0.1" value={torqueLimit} title="修改后自动保存，下次打开自动恢复" onChange={e=>setTorqueLimit(e.target.value)}/></label>
      <label>转速纵轴上限 r/min <input aria-label="转速纵轴上限 r/min" type="number" min="1" step="10" value={speedLimit} onChange={e=>setSpeedLimit(e.target.value)}/></label>
      <label>输出功率轴上限 W <input aria-label="输出功率轴上限 W" type="number" min="0.1" step="10" value={powerLimit} title="修改后自动保存，下次打开自动恢复" onChange={e=>setPowerLimit(e.target.value)}/></label>
      <label>连线方式 <select aria-label="特性曲线连线方式" value={lineMode} onChange={e=>setLineMode(e.target.value)}><option value="measured">实测连续折线</option><option value="linear">线性趋势（拟合）</option><option value="curve">曲线拟合（可调拟合率）</option></select></label>
      {lineMode==='curve'&&<label>拟合率
        <input aria-label="曲线拟合率" type="range" min="0" max="100" step="1" value={fitRate}
          onChange={e=>setFitRate(Number(e.target.value))} style={{width:160}}/>
        <span>{fitRate}%</span></label>}
    </div>
    <div className="characteristic-scroll"><svg ref={chart} className="motor-performance-chart" viewBox="0 0 1040 790" role="img" aria-label="电机六曲线特性图：电压、输出功率、转速、母线输入电流、输入功率和效率">
      <title>24 V / 10 A 电机特性图，六项均由实测数据绘制</title>
      <text x={(LEFT+RIGHT)/2} y="27" textAnchor="middle" className="performance-chart-title">SG-6010HB · 24 V DC · 10 A</text>
      {loadRun&&<text x={RIGHT} y="77" textAnchor="end" className="characteristic-load-svg">{loadRun.mode==='torque_sweep'?`目标 ${value(loadRun.targetNm,2)} N·m`:`制动 ${value(Number(loadRun.brake?.currentA))} A`} · 轴端实测 {value(load?.torque,2)} N·m</text>}
      <defs><clipPath id={clipId}><rect x={LEFT} y={TOP} width={RIGHT-LEFT} height={BOTTOM-TOP}/></clipPath></defs>
      <rect x={LEFT} y={TOP} width={RIGHT-LEFT} height={BOTTOM-TOP} fill="white" stroke="#161616" strokeWidth="1.1"/>
      {Array.from({length:11},(_,i)=><g key={i}>
        {i>0&&i<10&&<>
          <line x1={LEFT} x2={RIGHT} y1={TOP+i*(BOTTOM-TOP)/10} y2={TOP+i*(BOTTOM-TOP)/10} stroke="#2c2c2c" strokeDasharray="8 6" strokeWidth="1"/>
          <line x1={LEFT+i*(RIGHT-LEFT)/10} x2={LEFT+i*(RIGHT-LEFT)/10} y1={TOP} y2={BOTTOM} stroke="#2c2c2c" strokeDasharray="8 6" strokeWidth="1"/>
        </>}
        <text x={LEFT+i*(RIGHT-LEFT)/10} y={BOTTOM+28} textAnchor="middle" fill="#111">{tick(i*bound/10)}</text>
      </g>)}
      {series.map(s=>{
        const curve=lineMode==='linear'?linearCharacteristic(points,s.key)
          :lineMode==='curve'?curveCharacteristic(points,s.key,fitRate)
          :stop&&Number.isFinite(stop[s.key])?[...points,stop]:points;
        let drawing=false,path='';
        for(const p of curve){
          if(!Number.isFinite(p[s.key])){drawing=false;continue;}
          path+=`${drawing?'L':'M'}${(LEFT+p.torque/bound*(RIGHT-LEFT)).toFixed(2)},${(BOTTOM-p[s.key]/s.upper*(BOTTOM-TOP)).toFixed(2)} `;drawing=true;
        }
        const visible=curve.filter(p=>Number.isFinite(p[s.key])&&p.torque>=0&&p.torque<=bound&&p[s.key]>=0&&p[s.key]<=s.upper);
        const hasCoverage=visible.length>1&&visible.at(-1).torque-visible[0].torque>=bound*.1;
        const annotation=hasCoverage?visible[Math.floor((visible.length-1)*s.labelAt)]:null;
        return <g key={s.key} data-series={s.key} fill={s.color}>
          <text x={s.axis} y="61" textAnchor="middle" className="performance-axis-unit">{s.symbol}</text>
          {Array.from({length:11},(_,i)=><text key={i} x={s.axis} y={BOTTOM-i*(BOTTOM-TOP)/10+6} textAnchor="middle">{tick(i*s.upper/10)}</text>)}
          <g clipPath={`url(#${clipId})`}>
            <path d={path} fill="none" stroke={s.color} strokeWidth="2.8" vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round"/>
            {stop&&Number.isFinite(stop[s.key])&&<circle
              cx={LEFT+stop.torque/bound*(RIGHT-LEFT)} cy={BOTTOM-stop[s.key]/s.upper*(BOTTOM-TOP)}
              r="4" stroke={s.color} strokeWidth="2" fill="white"><title>确认停转实测终点：{stop.torque} N·m，{stop.speed} rpm</title></circle>}
            {visible.length===1&&<circle cx={LEFT+visible[0].torque/bound*(RIGHT-LEFT)} cy={BOTTOM-visible[0][s.key]/s.upper*(BOTTOM-TOP)} r="3"/>}
            {annotation&&<text x={Math.min(RIGHT-64,Math.max(LEFT+6,LEFT+annotation.torque/bound*(RIGHT-LEFT)+6))}
              y={Math.min(BOTTOM-8,Math.max(TOP+18,BOTTOM-annotation[s.key]/s.upper*(BOTTOM-TOP)+s.dy))} className="performance-curve-label">{s.symbol}</text>}
          </g>
        </g>;
      })}
      <text x={(LEFT+RIGHT)/2} y="779" textAnchor="middle" fill="#111">T(N·m)</text>
    </svg></div>
    {stop&&<p className="panel-caption">确认停转实测终点：{value(stop.torque,2)} N·m · {value(stop.speed,1)} rpm · {value(stop.power,2)} W；空心圆单独保留，未参与扭矩分箱平均。</p>}
    {points.some(p=>p.torque>bound||series.some(s=>Number.isFinite(p[s.key])&&p[s.key]>s.upper))&&<p className="panel-caption">有实测值超出显示量程，超出部分保留在原始数据中；可调整扭矩或转速坐标查看。</p>}
    {!points.length&&<p className="panel-caption">等待真实扭矩数据，曲线按实际测量结果生成。</p>}
    {lineMode==='linear'&&<p className="panel-caption">线性拟合仅覆盖实测区间，不代表原始曲线形状。</p>}
    {lineMode==='curve'&&<p className="panel-caption">局部二次曲线拟合，仅覆盖实测区间，不外推。拟合率 {fitRate}%：数值越高越贴近实测点，越低越平滑；仍为二次曲线，不会退化成直线。</p>}
    <p className="panel-caption">{windowSeconds===Infinity?'本次试验全过程':'最近 '+windowSeconds+' 秒'}按实测扭矩分箱：Po = |T × n| / 9550 × 1000；Pi = 实测母线 U × I；EFF = Po / Pi × 100%。I 为母线实测电流。电源数据过期或 Pi 为零时对应曲线留空；24 V / 10 A 是面板预设条件。</p>
  </div>;
}
export default React.memo(TorqueCharacteristics);
