import test from 'node:test';
import assert from 'node:assert/strict';
import {Bench} from '../browser/bench.ts';
import {utc} from '../browser/bytes.ts';

async function fixture(journal={dirty:true,expected:['motor-supply','brake-supply','current-motor'],motorMayBeOn:true}) {
  const data=new Map([['output-journal',journal]]);
  const store={get:async key=>structuredClone(data.get(key)),put:async(key,value)=>data.set(key,structuredClone(value)),entries:async()=>[]};
  const env=new Bench({call:async()=>{throw new Error('Recovery must not issue device commands');}},store,()=>{});
  await env.initialize();env.timers.forEach(clearInterval);
  const devices={
    'current-motor':{connected:true,snapshot:{receivedAt:utc(),motor:{output_on:false,age_s:0}}},
    'motor-supply':{connected:true,snapshot:{receivedAt:utc(),status:128}},
    'brake-supply':{connected:true,snapshot:{receivedAt:utc(),status:0}},
  };
  env.source=()=>devices;
  return {env,devices,data,store};
}

test('reconnecting with fresh disabled drive and brake releases the old journal without cycling motor power',async()=>{
  const {env,data}=await fixture();
  assert.equal(env.blocked,true);
  await env.poll();
  assert.equal(env.blocked,false);
  assert.equal(env.coupled.run.stopConfirmed,true);
  assert.equal(env.recoveryExpected,null);
  assert.deepEqual(data.get('output-journal'),{dirty:true,expected:['motor-supply'],motorMayBeOn:false});
  let started=false;env.coupled.sweep=async()=>{started=true;};
  await env.coupled.start({mode:'torque_sweep',brake:{startNm:0,stepNm:.1,intervalS:1,maxNm:1}});
  assert.equal(started,true);
});

for(const [name,change] of [
  ['missing drive',d=>{d['current-motor'].connected=false;}],
  ['drive output on',d=>{d['current-motor'].snapshot.motor.output_on=true;}],
  ['stale drive',d=>{d['current-motor'].snapshot.receivedAt-=3;}],
  ['stale motor output feedback',d=>{d['current-motor'].snapshot.motor.age_s=2;}],
  ['missing power',d=>{d['motor-supply'].connected=false;}],
  ['stale brake',d=>{d['brake-supply'].snapshot.receivedAt-=3;}],
  ['brake output on',d=>{d['brake-supply'].snapshot.status=128;}],
]) test(`recovery waits for ${name} to be resolved`,async()=>{
  const {env,devices,data}=await fixture();change(devices);
  await env.poll();
  assert.equal(env.blocked,true);
  assert.ok(env.recoveryExpected);
  assert.equal(data.get('output-journal').motorMayBeOn,true);
});

test('failed journal write keeps the recovery block and exposes its cause',async()=>{
  const {env,store}=await fixture();store.put=async()=>{throw new Error('disk full');};
  await env.poll();
  assert.equal(env.blocked,true);assert.match(env.errors[0],/disk full/);
});

test('a newer stop is not cleared by an earlier recovery write',async()=>{
  const {env,store}=await fixture();let finish;
  store.put=()=>new Promise(resolve=>{finish=resolve;});
  const polling=env.poll();await new Promise(resolve=>setImmediate(resolve));
  env.epoch++;env.stopping=true;
  finish();await polling;
  assert.equal(env.blocked,true);assert.ok(env.recoveryExpected);
});

test('a journal with unknown affected devices still requires an explicit stop',async()=>{
  const {env}=await fixture();
  env.recoveryExpected=new Set();env.recoveryMotor=false;
  await env.poll();
  assert.equal(env.blocked,true);
});


test('power-only recovery does not require a drive that was never connected',async()=>{
  const {env,devices,data}=await fixture({dirty:true,expected:['motor-supply','brake-supply'],motorMayBeOn:false});
  devices['current-motor'].connected=false;
  devices['motor-supply'].snapshot.status=0;
  env.motor.run.stopConfirmed=false;
  await env.reconcileRecovery();
  assert.equal(env.blocked,false);assert.equal(env.motor.run.stopConfirmed,null);assert.equal(env.coupled.run.stopConfirmed,null);
  assert.equal(data.get('output-journal').dirty,false);
});

test('confirmed power-off and low residual voltage resolve old motor uncertainty without CAN',async()=>{
  const {env,devices,data}=await fixture();
  devices['current-motor'].connected=false;
  Object.assign(devices['motor-supply'],{config:{voltageDecimals:2}});
  Object.assign(devices['motor-supply'].snapshot,{status:0,measured:[0,0]});
  await env.reconcileRecovery();
  assert.equal(env.blocked,false);assert.equal(env.motorIsolated,true);
  assert.equal(env.motor.run.stopConfirmed,true);assert.equal(data.get('output-journal').dirty,false);
});

for(const [name,change] of [
  ['residual voltage',d=>d.snapshot.measured[0]=240],
  ['missing voltage',d=>delete d.snapshot.measured],
  ['missing scale',d=>delete d.config],
  ['stale power',d=>d.snapshot.receivedAt-=3],
  ['power still on',d=>d.snapshot.status=128],
]) test(`motor recovery cannot infer isolation with ${name}`,async()=>{
  const {env,devices}=await fixture();devices['current-motor'].connected=false;
  const power=devices['motor-supply'];power.config={voltageDecimals:2};Object.assign(power.snapshot,{status:0,measured:[0,0]});change(power);
  await env.reconcileRecovery();assert.equal(env.blocked,true);
});

test('unconnected start is rejected before ownership or dirty journal is written',async()=>{
  const {env,data}=await fixture(undefined);
  env.blocked=false;env.recoveryExpected=null;env.expected.clear();data.delete('output-journal');
  const headers={'x-workbench-client':'never-connected-client','x-control-epoch':String(env.epoch)};
  await assert.rejects(env.claim(headers,{energizing:true,motor:true}),/电机未连接/);
  assert.equal(env.owner,null);assert.equal(env.expected.size,0);assert.equal(data.has('output-journal'),false);
});

test('a clean launch with no connections does not ask for a stop',async()=>{
  const {env}=await fixture({dirty:false,expected:[],motorMayBeOn:false});
  assert.equal(env.blocked,false);assert.equal(env.reason,'');assert.equal(env.motor.run.stopConfirmed,null);
});

test('a known powered supply alone is not mistaken for an enabled motor',async()=>{
  const {env,devices,data}=await fixture({dirty:true,expected:['motor-supply'],motorMayBeOn:false});
  devices['current-motor'].connected=false;devices['brake-supply'].connected=false;
  await env.reconcileRecovery();
  assert.equal(env.blocked,false);assert.equal(env.motor.run.stopConfirmed,null);assert.equal(env.brake.run.stopConfirmed,null);assert.equal(env.coupled.run.stopConfirmed,null);
  assert.deepEqual(data.get('output-journal'),{dirty:true,expected:['motor-supply'],motorMayBeOn:false});
});
