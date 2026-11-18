import test from 'node:test';
import assert from 'node:assert/strict';
import {Bench} from '../browser/bench.ts';
import {utc,sleep} from '../browser/bytes.ts';

async function fixture() {
  const data=new Map(),events=[];
  const store={get:async key=>data.get(key),put:async(key,value)=>data.set(key,value),entries:async()=>[]};
  const env=new Bench({call:async()=>{throw Error('Unexpected I/O');}},store,()=>{});
  await env.subjects.initialize();
  env.motorConfig=async hint=>({device:'can-test',node:hint?.node??1});
  const power=env.channels['motor-supply'];
  power.config={};power.connected=true;power.snapshot={receivedAt:utc(),status:0};
  power.read=async()=>{power.snapshot.receivedAt=utc();return power.status();};
  power.output=async on=>{events.push(['power',on]);power.snapshot={receivedAt:utc(),status:on?128:0};return power.status();};
  let connected=false,ready=false,attempts=0;
  env.motor.status=()=>({connected,snapshot:connected?{motor:{output_on:false},capabilities:{nativeControl:true},startBlockReason:''}:null,error:env.motor.error,run:env.motor.run});
  env.motor.connect=async config=>{events.push(['connect',config.node]);attempts++;if(!ready)throw Error('booting');connected=true;env.motor.error='';return env.motor.status();};
  env.device.close=async()=>{connected=false;events.push(['disconnect']);};
  env.motor.start=async()=>{throw Error('Connection must not start motor output');};
  const request=(path,body={})=>env.request(path,'POST',body,{'x-motorbench-token':env.token,'x-workbench-client':'power-connect-test','x-control-epoch':String(env.epoch)});
  const drain=()=>new Promise(resolve=>setImmediate(resolve));
  return {env,power,events,request,drain,setReady:()=>{ready=true;},attempts:()=>attempts};
}

test('power ON waits through board boot, then publishes connected status without starting motor',async()=>{
  const f=await fixture();
  await assert.rejects(f.request('/api/motor-current/connect',{node:3}),/booting/);
  await f.env.poll();assert.equal(f.attempts(),1,'no retries while power is off');
  await f.request('/api/supplies/motor-supply/output',{on:true});await f.drain();
  assert.equal(f.attempts(),2);assert.match(f.env.motor.error,/自动连接/);
  f.setReady();await sleep(1050);await f.env.poll();await f.drain();
  assert.equal(f.attempts(),3);assert.equal(f.env.motor.status().connected,true);
  assert.deepEqual(f.env.connection.report,['电机驱动板：已连接']);
  assert.equal(f.env.routes()['/api/coupled-current/status'].motor.connected,true);
  await sleep(1050);await f.env.poll();assert.equal(f.attempts(),3);
  assert.deepEqual(f.events,[['connect',3],['power',true],['connect',3],['connect',3]]);
});

test('boot handshake is single-flight and does not block power polling',async()=>{
  const f=await fixture();let resolve,attempts=0;
  f.env.motor.connect=async()=>{attempts++;await new Promise(done=>{resolve=done;});return f.env.motor.status();};
  await f.request('/api/supplies/motor-supply/output',{on:true});await f.drain();
  for(let i=0;i<5;i++)await f.env.poll();
  assert.equal(attempts,1);assert.ok(f.power.snapshot.receivedAt);
  resolve();await f.drain();
});

test('manual disconnect cancels pending retries; enabling power again permits a new connection',async()=>{
  const f=await fixture();
  await f.request('/api/supplies/motor-supply/output',{on:true});await f.drain();
  await f.request('/api/motor-current/disconnect');f.setReady();
  await sleep(1050);await f.env.poll();await f.drain();assert.equal(f.attempts(),1);
  await f.request('/api/supplies/motor-supply/output',{on:true});await f.drain();assert.equal(f.attempts(),2);assert.equal(f.env.motor.status().connected,true);
});

test('power OFF cancels retries even if an earlier handshake fails later',async()=>{
  const f=await fixture();let reject;
  f.env.motor.connect=()=>new Promise((_,fail)=>{reject=fail;});
  await f.request('/api/supplies/motor-supply/output',{on:true});await f.drain();
  f.env.stopOutputs=async()=>{f.power.snapshot.status=0;f.env.stopKeys.clear();f.env.stopping=false;f.env.blocked=false;};
  await f.request('/api/supplies/motor-supply/output',{on:false});await f.env.stopTask;
  reject(Error('late timeout'));await f.drain();
  f.power.snapshot.status=128;f.env.motor.connect=async()=>{throw Error('must not retry after cancellation');};
  await sleep(1050);await f.env.poll();await f.drain();
  assert.equal(f.env.waitingForMotor,false);
});

test('an already connected driver is not reopened when power ON is requested',async()=>{
  const f=await fixture();f.setReady();await f.request('/api/motor-current/connect');
  await f.request('/api/supplies/motor-supply/output',{on:true});await f.drain();await f.env.poll();
  assert.equal(f.attempts(),1);
});


test('connect-all leaves a powered-off board waiting and repairs its report after power ON',async()=>{
  const f=await fixture();f.env.io.call=async()=>[];
  f.env.sensor.status=()=>({connected:true});
  f.env.channels['brake-supply'].connected=true;
  await f.env.connectAll({'current-motor':{node:2}});await f.drain();
  assert.equal(f.env.connection.running,false);assert.match(f.env.connection.report.at(-1),/等待驱动板上电回传/);
  f.setReady();await f.request('/api/supplies/motor-supply/output',{on:true});await f.drain();
  assert.equal(f.env.motor.status().connected,true);
  assert.equal(f.env.connection.report.filter(line=>line.startsWith('电机驱动板：')).length,1);
  assert.equal(f.env.connection.report.at(-1),'电机驱动板：已连接');
});
