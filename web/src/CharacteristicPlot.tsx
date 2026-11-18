import React,{useId,useState} from 'react';
import {isOutputPoint} from '../../browser/characteristic-points.ts';
import {characteristicAutoUpper,fitCharacteristic} from './characteristicData.ts';
import {MOTOR_SUPPLY_CURRENT_LIMIT,SUPPLY_VOLTAGE} from './supplyPolicy.ts';
const tick=value=>Number(value.toPrecision(4));
const SERIES=[
  {key:'voltage',label:'母线电压',unit:'V',symbol:'U(V)',upper:10,axisStep:10,color:'#7565ff',axis:34,side:'left',labelAt:.3,dy:19},
  {key:'power',label:'轴功率',unit:'W',symbol:'Po(W)',upper:50,color:'#f07800',axis:100,side:'left',labelAt:.7,dy:-12},
  {key:'speed',label:'转速',unit:'rpm',symbol:'n(r/min)',upper:300,color:'#fb7278',axis:168,side:'left',labelAt:.93,dy:-13},
  {key:'current',label:'母线电流',unit:'A',symbol:'I(A)',upper:10,axisStep:10,color:'#17932d',axis:850,side:'right',labelAt:.55,dy:-12},
  {key:'inputPower',label:'输入功率',unit:'W',symbol:'Pi(W)',upper:SUPPLY_VOLTAGE*MOTOR_SUPPLY_CURRENT_LIMIT,axisStep:20,color:'#1627ed',axis:921,side:'right',labelAt:.3,dy:20},
  {key:'efficiency',label:'效率',unit:'%',symbol:'EFF(%)',upper:100,color:'#00bac4',axis:1000,side:'right',labelAt:.6,dy:20},
];

export function characteristicPlotData(loadRun, torqueLimit='', powerLimit='', speedLimit='') {
  const points=loadRun?.mode==='torque_sweep' && loadRun.loadStartedAt && [1,2].includes(loadRun.characteristicSchema)
    ? (loadRun.characteristicPoints || []).filter(isOutputPoint) : [];
  const stop=points.find(point=>point.kind==='stall') || null;
  const autoTorque=characteristicAutoUpper(points,'torque',1,1,1.02);
  const autoPower=characteristicAutoUpper(points,'power',10,10);
  const bound=Number(torqueLimit)>0?Number(torqueLimit):autoTorque;
  const outputBound=Number(powerLimit)>0?Number(powerLimit):autoPower;
  const supplyVoltage=points.find(point=>Number.isFinite(point.voltage))?.voltage;
  const chartTitle=['电机特性图',loadRun?.motorName,Number.isFinite(supplyVoltage)?`起始回读 ${tick(supplyVoltage)} V`:null].filter(Boolean).join(' · ');
  const autoRpmBound=characteristicAutoUpper(points,'speed',10,10);
  const rpmBound=Number.isFinite(Number(speedLimit))&&Number(speedLimit)>0?Number(speedLimit):autoRpmBound;
  const series=SERIES.map(s=>({
    ...s,
    upper:s.key==='speed'?rpmBound:s.key==='power'?outputBound:
      s.key==='efficiency'?characteristicAutoUpper(points,s.key,100,10):s.axisStep?characteristicAutoUpper(points,s.key,s.upper,s.axisStep):s.upper,
  }));
  return {points,stop,autoTorque,autoPower,autoRpmBound,bound,chartTitle,series};
}

function StageInspector({points,bound,left,right,top,bottom}) {
  const [index,setIndex]=useState<number | null>(null);
  const visible=points.filter(point=>point.torque>=0 && point.torque<=bound);
  if(!visible.length) return null;
  const point=index==null?null:visible[index];
  const chartX=torque=>left+torque/bound*(right-left);
  const x=point?chartX(point.torque):0;
  const boxWidth=248,boxHeight=84;
  const boxX=x+boxWidth+10<=right ? x+8 : Math.max(left,x-boxWidth-8);
  const boxY=Math.min(top+6,bottom-boxHeight);
  function inspect(event: React.PointerEvent<SVGRectElement>) {
    const svg=event.currentTarget.ownerSVGElement;
    const matrix=svg?.getScreenCTM();
    if(!matrix)return;
    const cursor=new DOMPoint(event.clientX,event.clientY).matrixTransform(matrix.inverse());
    const torque=(cursor.x-left)*bound/(right-left);
    let closest=0;
    for(let i=1;i<visible.length;i++){
      if(Math.abs(visible[i].torque-torque)<Math.abs(visible[closest].torque-torque))closest=i;
    }
    setIndex(closest);
  }
  return <g data-chart-inspector="">
    <rect x={left} y={top} width={right-left} height={bottom-top} fill="transparent" cursor="crosshair"
      tabIndex={0} role="slider" aria-label="按输出档位读数" aria-valuemin={1} aria-valuemax={visible.length}
      aria-valuenow={point?index+1:1} aria-valuetext={point?`${point.torque} N·m，${SERIES.map(s=>s.label+' '+(point[s.key]??'—')+' '+s.unit).join('，')}`:undefined}
      onPointerMove={inspect} onPointerDown={inspect} onPointerLeave={()=>setIndex(null)}
      onFocus={()=>setIndex(previous=>previous??0)} onBlur={()=>setIndex(null)}
      onKeyDown={e=>{
        if(e.key==='Escape'){setIndex(null);return;}
        if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
        e.preventDefault();
        const last=visible.length-1;
        setIndex(e.key==='Home'?0:e.key==='End'?last:Math.max(0,Math.min(last,(index??0)+(e.key==='ArrowLeft'?-1:1))));
      }}/>
    {point&&<g pointerEvents="none" data-stage-readout={point.stage}>
      <line x1={x} x2={x} y1={top} y2={bottom} stroke="#51667f" strokeDasharray="4 4"/>
      <g transform={`translate(${boxX} ${boxY})`} fontSize="12" fill="#203247">
        <rect width={boxWidth} height={boxHeight} rx="5" fill="white" stroke="#a5b3c3"/>
        <text x="10" y="17" fontWeight="bold">制动设定 {tick(point.torque)} N·m</text>
        {SERIES.map((s,i)=><text key={s.key} x={10+Math.floor(i/3)*122} y={37+i%3*18}>
          {s.label} {Number.isFinite(point[s.key])?tick(point[s.key])+' '+s.unit:'—'}
        </text>)}
      </g>
    </g>}
  </g>;
}

export function CharacteristicPlot({loadRun,model,size={width:1040,height:790},desktop=false,interactive=false,lineMode='measured',fitRate=60,chartRef=null}) {
  const {points,stop,bound,chartTitle,series}=model;
  const value=(n,digits=3)=>Number.isFinite(n)?n.toFixed(digits):'—';
  const LEFT=desktop?180:200,RIGHT=desktop?size.width-180:820;
  const TOP=desktop?64:100,BOTTOM=desktop?size.height-44:720;
  const divisions=desktop&&size.height<400?5:10;
  const clipId=useId().replace(/:/g,'');
  return <svg xmlns="http://www.w3.org/2000/svg" ref={chartRef} width={size.width} height={size.height} fontFamily="sans-serif" fontSize="16" className="motor-performance-chart" viewBox={`0 0 ${size.width} ${size.height}`} role="img" aria-label="电机六曲线特性图：电压、输出功率、转速、母线输入电流、输入功率和效率">
      <rect width="100%" height="100%" fill="white"/>
      <title>{chartTitle} · 制动输出设定扭矩</title>
      <text x={(LEFT+RIGHT)/2} y={desktop?20:27} textAnchor="middle" className="performance-chart-title" fontSize="21">{chartTitle.length>44?chartTitle.slice(0,41)+'…':chartTitle}</text>
      {loadRun?.running&&loadRun.loadStartedAt&&Number.isFinite(loadRun.targetNm)&&<text x={RIGHT} y={desktop?58:77} textAnchor="end" className="characteristic-load-svg">制动设定 {value(loadRun.targetNm,2)} N·m</text>}
      {!loadRun?.running&&loadRun?.id&&points.length>0&&<text x={RIGHT} y={desktop?58:81} textAnchor="end" fontSize={desktop?10:13} fill="#444">
        {stop?`堵转时制动设定 ${value(stop.torque,2)} N·m`:`制动输出设定范围 ${value(points[0].torque,2)}–${value(points.at(-1).torque,2)} N·m`}
      </text>}
      <defs><clipPath id={clipId}><rect x={LEFT} y={TOP} width={RIGHT-LEFT} height={BOTTOM-TOP}/></clipPath></defs>
      <rect x={LEFT} y={TOP} width={RIGHT-LEFT} height={BOTTOM-TOP} fill="white" stroke="#161616" strokeWidth="1.1"/>
      {Array.from({length:divisions+1},(_,i)=><g key={i}>
        {i>0&&i<divisions&&<>
          <line x1={LEFT} x2={RIGHT} y1={TOP+i*(BOTTOM-TOP)/divisions} y2={TOP+i*(BOTTOM-TOP)/divisions} stroke="#2c2c2c" strokeDasharray="8 6" strokeWidth="1"/>
          <line x1={LEFT+i*(RIGHT-LEFT)/divisions} x2={LEFT+i*(RIGHT-LEFT)/divisions} y1={TOP} y2={BOTTOM} stroke="#2c2c2c" strokeDasharray="8 6" strokeWidth="1"/>
        </>}
        <text x={LEFT+i*(RIGHT-LEFT)/divisions} y={BOTTOM+(desktop?18:28)} textAnchor="middle" fill="#111">{tick(i*bound/divisions)}</text>
      </g>)}
      {series.map((s,index)=>{
        const axis=desktop?(index<3?[28,85,145][index]:size.width-[150,92,32][index-3]):s.axis;
        const curve=lineMode==='scatter'||lineMode==='measured'?points:fitCharacteristic(points.filter(point=>point!==stop),s.key,lineMode,fitRate,stop);
        let drawing=false,path='';
        for(const p of curve){
          if(!Number.isFinite(p[s.key])){drawing=false;continue;}
          path+=`${drawing?'L':'M'}${(LEFT+p.torque/bound*(RIGHT-LEFT)).toFixed(2)},${(BOTTOM-p[s.key]/s.upper*(BOTTOM-TOP)).toFixed(2)} `;drawing=true;
        }
        const visible=curve.filter(p=>Number.isFinite(p[s.key])&&p.torque>=0&&p.torque<=bound&&p[s.key]>=0&&p[s.key]<=s.upper);
        const hasCoverage=visible.length>1&&visible.at(-1).torque-visible[0].torque>=bound*.1;
        const annotation=hasCoverage?visible[Math.floor((visible.length-1)*s.labelAt)]:null;
        return <g key={s.key} data-series={s.key} fill={s.color}>
          <text x={axis} y={desktop?43:61} textAnchor="middle" className="performance-axis-unit">{s.symbol}</text>
          {Array.from({length:divisions+1},(_,i)=><text key={i} x={axis} y={BOTTOM-i*(BOTTOM-TOP)/divisions+6} textAnchor="middle">{tick(i*s.upper/divisions)}</text>)}
          <g clipPath={`url(#${clipId})`}>
            {lineMode!=='scatter'&&<path d={path} fill="none" stroke={s.color} strokeWidth="2.8" vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round"/>}
            {lineMode==='scatter'&&visible.map(point=><circle key={`${point.stage}-${point.kind}-${point.receivedAt}`} data-stage={point.stage}
              cx={LEFT+point.torque/bound*(RIGHT-LEFT)} cy={BOTTOM-point[s.key]/s.upper*(BOTTOM-TOP)}
              r="3.5" stroke={s.color} fill={point.kind==='stall'?'white':s.color}><title>第 {point.stage+1} 级：目标 {value(point.torque)} N·m，{s.symbol} {value(point[s.key])}</title></circle>)}
            {lineMode!=='scatter'&&stop&&Number.isFinite(stop[s.key])&&<circle
              cx={LEFT+stop.torque/bound*(RIGHT-LEFT)} cy={BOTTOM-stop[s.key]/s.upper*(BOTTOM-TOP)}
              r="4" stroke={s.color} strokeWidth="2" fill="white"><title>确认堵转：目标 {stop.torque} N·m，{stop.speed} rpm</title></circle>}
            {lineMode!=='scatter'&&visible.length===1&&<circle cx={LEFT+visible[0].torque/bound*(RIGHT-LEFT)} cy={BOTTOM-visible[0][s.key]/s.upper*(BOTTOM-TOP)} r="3"/>}
            {annotation&&<text x={Math.min(RIGHT-64,Math.max(LEFT+6,LEFT+annotation.torque/bound*(RIGHT-LEFT)+6))}
              y={Math.min(BOTTOM-8,Math.max(TOP+18,BOTTOM-annotation[s.key]/s.upper*(BOTTOM-TOP)+s.dy))} className="performance-curve-label">{s.symbol}</text>}
          </g>
        </g>;
      })}
      {interactive&&<StageInspector key={loadRun?.id} points={points} bound={bound} left={LEFT} right={RIGHT} top={TOP} bottom={BOTTOM}/>}
      <text x={(LEFT+RIGHT)/2} y={size.height-11} textAnchor="middle" fill="#111">制动输出设定扭矩 T (N·m)</text>
    </svg>;
}
