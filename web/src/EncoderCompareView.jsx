import React, {useEffect,useMemo,useRef,useState} from 'react';
import {characteristicAxisMax,motorTemperatureC} from './model.js';
import './EncoderCompareView.css';

// User-confirmed encoder-to-output reduction ratio; this view does not reuse
// the motor profile value because the requested ratio differs from it.
const ENCODER_RATIO=9.78;
const AXIS_KEY='motor-bench:characteristic:limits';
const SAMPLE_MS=100;
const MAX_POINTS=12000;
const W=1000,H=300,L=70,R=66,T=26,B=40;

const finite=value=>Number.isFinite(value)?value:null;
const tick=value=>Number(value.toPrecision(4));
function fresh(snapshot,maxAge=1.5){
  return Boolean(snapshot)&&Number.isFinite(snapshot.receivedAt)
    &&Date.now()/1000-snapshot.receivedAt<=maxAge;
}
function show(value,digits=1){return value==null?'—':value.toFixed(digits);}
function savedTorqueMax(){
  try{
    const saved=JSON.parse(localStorage.getItem(AXIS_KEY)||'{}');
    const value=Number(saved.torque);
    return Number.isFinite(value)&&value>0?value:5;
  }catch{return 5;}
}

function EncoderCompareView({devices}) {
  const sensor=devices?.['torque-sensor'];
  const motor=devices?.['current-motor'];
  const sensorSnapshot=sensor?.connected?sensor.snapshot:null;
  const motorSnapshot=motor?.connected?motor.snapshot:null;
  const feedback=motorSnapshot?.feedback;
  const ntc=motorSnapshot?.ntc;
  const sensorFresh=fresh(sensorSnapshot);
  const encoderFresh=Boolean(feedback&&feedback.stale===false&&fresh(motorSnapshot));
  const torque=sensorFresh?finite(sensorSnapshot?.torque):null;
  const sensorRpmRaw=sensorFresh?finite(sensorSnapshot?.speed):null;
  const sensorRpm=sensorRpmRaw==null?null:Math.abs(sensorRpmRaw);
  const encoderRotorRpm=encoderFresh?finite(feedback?.speed_rpm):null;
  const encoderRpm=encoderRotorRpm==null?null:Math.abs(encoderRotorRpm)/ENCODER_RATIO;
  const temperature=motorTemperatureC(motor);
  const difference=sensorRpm!=null&&encoderRpm!=null?sensorRpm-encoderRpm:null;
  const differencePct=difference!=null&&encoderRpm>=.5?difference/encoderRpm*100:null;

  // The new view shares the characteristic chart's torque-axis upper bound.
  const [torqueMax,setTorqueMax]=useState(savedTorqueMax);
  useEffect(()=>{
    const update=event=>{
      const value=Number(event?.detail?.torque);
      setTorqueMax(Number.isFinite(value)&&value>0?value:5);
    };
    const storage=event=>{if(event.key===AXIS_KEY)setTorqueMax(savedTorqueMax());};
    window.addEventListener('motor-bench:characteristic-limits',update);
    window.addEventListener('storage',storage);
    return()=>{
      window.removeEventListener('motor-bench:characteristic-limits',update);
      window.removeEventListener('storage',storage);
    };
  },[]);

  const latest=useRef({});
  latest.current={torque:torque==null?null:Math.abs(torque),temp:temperature,sensorRpm,encoderRpm};
  const [history,setHistory]=useState([]);
  useEffect(()=>{
    const append=()=>{
      const point=latest.current;
      if(!Number.isFinite(point.torque))return;
      setHistory(previous=>[...previous,{t:Date.now(),...point}].slice(-MAX_POINTS));
    };
    append();
    const timer=window.setInterval(append,SAMPLE_MS);
    return()=>window.clearInterval(timer);
  },[]);

  const chart=useMemo(()=>{
    const limit=torqueMax;
    const width=Math.max(.001,limit/80);
    const bins=new Map();
    let clipped=false;
    for(const point of history){
      const x=point.torque;
      if(!Number.isFinite(x))continue;
      if(x>limit){clipped=true;continue;}
      const key=Math.floor(x/width);
      let bin=bins.get(key);
      if(!bin){bin={x:0,count:0,temp:0,tempN:0,sensor:0,sensorN:0,encoder:0,encoderN:0};bins.set(key,bin);}
      bin.x+=x;bin.count++;
      if(Number.isFinite(point.temp)){bin.temp+=point.temp;bin.tempN++;}
      if(Number.isFinite(point.sensorRpm)){bin.sensor+=point.sensorRpm;bin.sensorN++;}
      if(Number.isFinite(point.encoderRpm)){bin.encoder+=point.encoderRpm;bin.encoderN++;}
    }
    const rows=[...bins.values()].map(bin=>({torque:bin.x/bin.count,
      temp:bin.tempN?bin.temp/bin.tempN:null,
      sensorRpm:bin.sensorN?bin.sensor/bin.sensorN:null,
      encoderRpm:bin.encoderN?bin.encoder/bin.encoderN:null})).sort((a,b)=>a.torque-b.torque);
    const speedMax=characteristicAxisMax(Math.max(1,...rows.map(row=>Math.max(row.sensorRpm??0,row.encoderRpm??0))));
    const tempMax=characteristicAxisMax(Math.max(10,...rows.map(row=>row.temp??0)));
    const plotW=W-L-R,plotH=H-T-B;
    const xOf=torque=>L+torque/limit*plotW;
    const ySpeed=value=>T+(1-value/speedMax)*plotH;
    const yTemp=value=>T+(1-value/tempMax)*plotH;
    function pathFor(key,yOfValue){
      let path='',drawing=false;
      for(const row of rows){
        const value=row[key];
        if(!Number.isFinite(value)){drawing=false;continue;}
        path+=`${drawing?'L':'M'}${xOf(row.torque).toFixed(2)},${yOfValue(value).toFixed(2)} `;
        drawing=true;
      }
      return path;
    }
    return {speedMax,tempMax,sensorPath:pathFor('sensorRpm',ySpeed),
      encoderPath:pathFor('encoderRpm',ySpeed),tempPath:pathFor('temp',yTemp),
      hasData:rows.length>0,clipped};
  },[history,torqueMax]);

  const temperatureStatus=!motor?.connected?'电机未连接':ntc?.fault?'NTC 故障'
    :temperature==null?'温度无效或已过期':ntc?.tripped||ntc?.tripped_latched?'过温保护':'NTC 实时反馈';
  const sensorStatus=!sensor?.connected?'传感器未连接':sensorFresh?'实时反馈':'数据已过期';
  const encoderStatus=!motor?.connected?'电机未连接':feedback?.stale?'反馈已过期':encoderFresh?'实时反馈':'数据已过期';
  const torqueText=torque==null?'—':`${torque>=0?'+':''}${torque.toFixed(2)}`;
  const diffText=difference==null?'—':`${difference>=0?'+':''}${difference.toFixed(2)} rpm${differencePct==null?'':`（${differencePct>=0?'+':''}${differencePct.toFixed(1)}%）`}`;
  const axisMax=tick(torqueMax);

  return <section className="panel encoder-compare" aria-label="编码器与输出轴对比">
    <div className="encoder-compare-head">
      <div>
        <h2>编码器 / 输出轴对比</h2>
        <p>横轴为制动扭矩，量程与特性图一致（0–{axisMax} N·m）；曲线按扭矩分箱平均。温度来自电机 NTC，仅显示真实设备反馈，不参与控制。</p>
      </div>
      <span className="encoder-badge">实时同步 · 10 Hz</span>
    </div>
    <div className="encoder-cards">
      <article className="encoder-card">
        <span>电机温度</span>
        <strong>{temperature==null?'—':temperature.toFixed(1)}{temperature!=null&&<small>°C</small>}</strong>
        <em>{temperatureStatus}</em>
      </article>
      <article className="encoder-card">
        <span>制动扭矩（横轴）</span>
        <strong>{torqueText}{torque!=null&&<small>N·m</small>}</strong>
        <em>横轴 0–{axisMax} N·m · 取幅值 · {sensorStatus}</em>
      </article>
      <article className="encoder-card">
        <span>扭矩传感器输出转速</span>
        <strong>{show(sensorRpm,1)}{sensorRpm!=null&&<small>rpm</small>}</strong>
        <em>输出轴 · {sensorStatus}</em>
      </article>
      <article className="encoder-card">
        <span>编码器推算输出转速</span>
        <strong>{show(encoderRpm,1)}{encoderRpm!=null&&<small>rpm</small>}</strong>
        <em>转子 {show(encoderRotorRpm==null?null:Math.abs(encoderRotorRpm),0)} rpm ÷ 9.78 · {encoderStatus}</em>
      </article>
    </div>
    <p className="encoder-diff">差值（扭矩传感器 − 编码器）：{diffText} <span>转速均取幅值 · 减速比 9.78</span></p>
    <div className="encoder-chart-wrap">
      <svg className="encoder-chart" viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label="制动扭矩与扭矩传感器转速、编码器推算转速、电机温度对比曲线">
        <defs><clipPath id="encoder-plot"><rect x={L} y={T} width={W-L-R} height={H-T-B}/></clipPath></defs>
        {Array.from({length:5},(_,i)=>{
          const y=T+i*(H-T-B)/4;
          return <g key={i}>
            <line x1={L} x2={W-R} y1={y} y2={y} stroke={i===4?'#c8d2de':'#e7ecf2'} strokeDasharray="4 5"/>
            <text x={L-9} y={y+4} textAnchor="end" fill="#607087">{tick(chart.speedMax-i*chart.speedMax/4)}</text>
            <text x={W-R+9} y={y+4} fill="#607087">{tick(chart.tempMax-i*chart.tempMax/4)}</text>
          </g>;
        })}
        {Array.from({length:6},(_,i)=>{
          const x=L+i*(W-L-R)/5;
          return <g key={i}>
            <line x1={x} x2={x} y1={T} y2={H-B} stroke="#f1f3f6"/>
            <text x={x} y={H-B+22} textAnchor="middle" fill="#7b8798">{tick(i*torqueMax/5)}</text>
          </g>;
        })}
        <text x={L} y={15} fill="#318978" fontSize="12">转速 rpm</text>
        <text x={W-R} y={15} fill="#d64545" fontSize="12" textAnchor="end">温度 °C</text>
        <text x={(L+W-R)/2} y={H-6} textAnchor="middle" fill="#607087" fontSize="12">制动扭矩 N·m</text>
        <g clipPath="url(#encoder-plot)">
          <path d={chart.sensorPath} fill="none" stroke="#318978" strokeWidth="2.2" strokeLinejoin="round" strokeLinecap="round"/>
          <path d={chart.encoderPath} fill="none" stroke="#d28131" strokeWidth="2.2" strokeDasharray="6 4" strokeLinejoin="round" strokeLinecap="round"/>
          <path d={chart.tempPath} fill="none" stroke="#d64545" strokeWidth="2.2" strokeLinejoin="round" strokeLinecap="round"/>
        </g>
      </svg>
      {!chart.hasData&&<div className="encoder-empty">等待真实制动扭矩数据</div>}
    </div>
    <div className="encoder-legend">
      <span><i style={{background:'#318978'}}/>扭矩传感器输出转速 rpm（左轴）</span>
      <span><i style={{background:'#d28131'}}/>编码器推算转速 rpm（左轴，虚线）</span>
      <span><i style={{background:'#d64545'}}/>电机温度 °C（右轴）</span>
    </div>
    <p className="encoder-note">横轴量程与特性图一致（0–{axisMax} N·m），按制动扭矩分箱平均；扭矩取幅值。{chart.clipped?' 有扭矩超过上限的点未显示。':''}</p>
  </section>;
}

export default React.memo(EncoderCompareView);
