import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
const base=resolve(process.env.LATENCY_SOURCE_DIR || '.'),tag=process.env.LATENCY_TAG || (process.env.LATENCY_SOURCE_DIR?'before':'after');
const load=path=>import(pathToFileURL(resolve(base,path)).href);
const {Bench}=await load('browser/bench.ts'),{jsonValue}=await load('browser/bytes.ts'),{LegacyApp}=await load('browser/legacy.ts');
class Store {data=new Map();async get(k){return structuredClone(this.data.get(k));}async put(k,v){this.data.set(k,structuredClone(v));}async entries(){return [];}}
const calls={motor:0},snapshot={receivedAt:1800000000,motor:{state:0,output_on:false,mode:1},readiness:{flags:37},ntc:{valid:true,fault:false,age_s:0,temp_c:25,limit_c:45},encoder:{valid:true,stale:false},feedback:{iq_a:0,id_a:0,speed_rpm:0,position_deg:0,stale:false,position_stale:false},capabilities:{modes:58,fields:(1<<24)-1,maximumOutput:true},limits:Object.fromEntries(Array.from({length:35},(_,i)=>[i+512,i*100])),limitA:30,supportedMaxA:30,fullTargetA:10};
const noIo={call:async()=>[]};
const env=new Bench(noIo,new Store(),()=>{});await env.subjects.initialize();
env.token='replay-token';env.subjects.data.motors[0].id='original-motor';
Object.assign(env.subjects.data.testSetups[0],{id:'original-setup',motorId:'original-motor'});
for(let i=0;i<30;i++)env.subjects.data.motors.push({id:String(i),name:'Replay motor '+i});
env.device.snapshot=()=>{calls.motor++;return jsonValue(snapshot);};
const headers={'x-motorbench-token':env.token};
const median=a=>[...a].sort((a,b)=>a-b)[a.length>>1];
const results={tag,description:'Isolated replay costs, not whole-app percentages',snapshot:jsonValue(env.routes()),runs:{}};
for(const [name,action] of [['heartbeat',()=>env.request('/api/control/heartbeat','POST',{},headers)],['sensorFrames',()=>env.request('/api/sensors/torque-sensor/frames?since=0','GET',{})],['publish',()=>env.publish()]]){
 const ms=[];for(let r=0;r<6;r++){calls.motor=0;const t=performance.now();for(let i=0;i<2000;i++)await action();if(r)ms.push(performance.now()-t);}
 results.runs[name]={medianMs:median(ms),motorSnapshotsPerCall:calls.motor/2000,runsMs:ms};
}
const store=await load('web/src/deviceStore.ts');let source;
globalThis.EventSource=class {listeners={};constructor(){source=this;}addEventListener(name,fn){this.listeners[name]=fn;}};
const state={devices:{'torque-sensor':{connected:true,snapshot:{receivedAt:1800000000,torque:2,speed:3}}}};
store.startDeviceStore(async()=>new Response(JSON.stringify(state)));await new Promise(r=>setTimeout(r,0));
let createEvent=(kind,data)=>new MessageEvent(kind,{data:JSON.stringify({type:kind,data})});
try{const {DeviceEvent}=await load('browser/events.ts');createEvent=(kind,data)=>new DeviceEvent(kind,data);}catch(e){if(e.code!=='ERR_MODULE_NOT_FOUND')throw e;}
const packets=Array.from({length:1000},(_,i)=>({config:{torqueDecimals:2,speedDecimals:1},frames:Array.from({length:20},(_,j)=>{const n=1+i*20+j;return[n,1800000000+n/1000,n%37,n%19];})}));
const ms=[];for(let r=0;r<6;r++){source.listeners.sensor_stream(createEvent('sensor_stream',{config:{},reset:true}));let cursor=0;const t=performance.now();for(const p of packets){source.listeners.sensor_frames(createEvent('sensor_frames',p));const b=store.sensorFrameBatch(cursor);assert.equal(b.frames.length,20);cursor=b.frames.at(-1)[0];}if(r)ms.push(performance.now()-t);}
results.runs.frames={medianMs:median(ms),runsMs:ms,packets:1000,samples:20000};
assert.deepEqual(store.sensorFrameBatch(19980).frames,packets.at(-1).frames);
for(const id of ['sg-6010hb','rm-7010']) {
 const app=new LegacyApp(id,noIo,new Store(),()=>{});
 app.telem.append(Array.from({length:4000},(_,i)=>[i+1,1800000000+i/1000,i%30,-i%21,i%15,240]));app.telemSeq=4000;
 const runs=[];for(let r=0;r<6;r++){const t=performance.now();for(let i=0;i<2000;i++)app.batch(3980,0);if(r)runs.push(performance.now()-t);}
 results.runs[id]={medianMs:median(runs),runsMs:runs,batches:2000,samplesPerBatch:20};
}
await mkdir('.runtime/latency-20261103',{recursive:true});
await writeFile(`.runtime/latency-20261103/${tag}-node.json`,JSON.stringify(results,null,2));
console.log(JSON.stringify({tag,runs:results.runs},null,2));
