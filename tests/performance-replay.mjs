// Isolated algorithm replay. Source baseline comes from the task backup, never from a device.
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
import {EncoderBins} from '../web/src/encoderBins.ts';
import {characteristicAxisMax} from '../web/src/model.ts';
const backup=(await readFile('.runtime/layout-performance-backup.txt','utf8')).trim();
const original=path=>execFileSync('tar',['-xOf',`${backup}/before.tar.gz`,path],{encoding:'utf8'});
const storeFile=`${backup}/device-store-before.mjs`;await writeFile(storeFile,original('web/src/deviceStore.js'));
const before=await import(pathToFileURL(storeFile).href),after=await import(pathToFileURL(resolve('web/src/deviceStore.js')).href);
async function prepare(module){
  let source;globalThis.EventSource=class{constructor(){source=this;this.listeners={};}addEventListener(name,callback){this.listeners[name]=callback;}};
  module.startDeviceStore(async()=>new Response(JSON.stringify({devices:{'torque-sensor':{connected:true,snapshot:{receivedAt:1,torque:2,speed:3}}}})));
  await new Promise(resolve=>setTimeout(resolve,0));return source;
}
const beforeSource=await prepare(before),afterSource=await prepare(after);
const emit=(source,name,data)=>source.listeners[name]({data:JSON.stringify({data})});
const initial=Array.from({length:16000},(_,i)=>[i+1,100+i/1000,i%41,i%17]);
const packets=Array.from({length:1000},(_,i)=>JSON.stringify({data:{config:{torqueDecimals:2,speedDecimals:1},frames:Array.from({length:20},(_,j)=>{const n=16001+i*20+j;return [n,100+n/1000,n%41,n%17];})}}));
const clock=Date.now;Date.now=()=>100000;
async function frames(module,source,direct){
  emit(source,'state',{devices:{'torque-sensor':{connected:true,snapshot:{receivedAt:100,torque:2,speed:3}}}});
  emit(source,'sensor_stream',{config:{},reset:true});emit(source,'sensor_frames',{config:{},frames:initial});
  let cursor=16000,count=0;const start=performance.now();
  for(const data of packets){source.listeners.sensor_frames({data});const batch=direct?module.sensorFrameBatch(cursor):await module.cachedResponse(`/api/sensors/torque-sensor/frames?since=${cursor}`).json();count+=batch.frames.length;cursor=batch.frames.at(-1)[0];}
  assert.equal(count,20000);return performance.now()-start;
}
const source=original('web/src/EncoderCompareView.jsx');
const body=source.slice(source.indexOf('  const chart=useMemo(()=>{')+'  const chart=useMemo(()=>{'.length,source.indexOf('  },[history,torqueMax]);'));
const constants='const W=1000,H=300,L=70,R=66,T=26,B=40;';
const oldChart=new Function('history','torqueMax','characteristicAxisMax',constants+body);
const suffix=body.slice(body.indexOf('    const speedMax='));
const newChart=new Function('rows','limit','clipped','characteristicAxisMax',constants+suffix);
const points=Array.from({length:14000},(_,i)=>({torque:(i%800)/160,temp:i%11?25+i%13*.1:null,sensorRpm:200-i%97*.4,encoderRpm:i%7?190+i%79*.2:null}));
function encoder(optimized){
  let history=points.slice(0,12000);const bins=new EncoderBins(5);for(const point of history)bins.append(point);
  let chart;const start=performance.now();
  for(const point of points.slice(12000)){
    if(optimized){bins.append(point);chart=newChart(bins.rows(),5,bins.clipped>0,characteristicAxisMax);}
    else{history=[...history,point].slice(-12000);chart=oldChart(history,5,characteristicAxisMax);}
  }
  assert.deepEqual(chart,oldChart(points.slice(-12000),5,characteristicAxisMax));
  return performance.now()-start;
}
const median=values=>values.sort((a,b)=>a-b)[Math.floor(values.length/2)];
const results={description:'Isolated Node replay; these are algorithm costs, not whole-app CPU percentages',frames:{packets:1000,framesPerPacket:20,retainedFrames:16000},encoder:{retainedPoints:12000,updates:2000,torqueBins:80,identicalFinalSvgPaths:true}};
try{
 await frames(before,beforeSource,false);await frames(after,afterSource,true);encoder(false);encoder(true);
 const a=[],b=[],c=[],d=[];
 for(let i=0;i<5;i++){a.push(await frames(before,beforeSource,false));b.push(await frames(after,afterSource,true));c.push(encoder(false));d.push(encoder(true));}
 for(const [key,oldValues,newValues] of [['frames',a,b],['encoder',c,d]]){
  const oldMs=median(oldValues),newMs=median(newValues);Object.assign(results[key],{beforeMedianMs:oldMs,afterMedianMs:newMs,reductionPercent:(1-newMs/oldMs)*100,beforeRunsMs:oldValues,afterRunsMs:newValues});
 }
 await mkdir('.runtime',{recursive:true});await writeFile('.runtime/layout-performance-js.json',JSON.stringify(results,null,2)+'\n');console.log(JSON.stringify(results,null,2));
}finally{Date.now=clock;}
