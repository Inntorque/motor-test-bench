import test from 'node:test';
import assert from 'node:assert/strict';
import {Bench} from '../browser/bench.js';
import {CurrentMotor,CoupledCurrent,StandstillConfirmation,BrakeTorque,BrakeCurrent} from '../browser/control.js';
import {TestSubjects,Recordings} from '../browser/storage.js';
import {Specialized} from '../browser/specialized.js';
import {fit,positioning,thermal} from '../browser/metrics.js';
import {utc} from '../browser/bytes.js';
class MemoryStore {
  data = new Map(); queries = [];
  async get(key) {return structuredClone(this.data.get(key));}
  async put(key,value) {this.data.set(key,structuredClone(value));}
  async entries(prefix) {this.queries.push(prefix);return [...this.data].filter(([key]) => key.startsWith(prefix)).sort(([a],[b]) => a.localeCompare(b)).map(([,value]) => structuredClone(value));}
}
function deferred() {let resolve;const promise = new Promise(done => {resolve = done;});return {promise,resolve};}
const snapshot = () => ({receivedAt:utc(),motor:{state:0,output_on:false,mode:1,updated_at:utc()},readiness:{flags:37},ntc:{valid:true,fault:false,age_s:0,temp_c:25,limit_c:45},encoder:{valid:true,stale:false},feedback:{iq_a:0,id_a:0,speed_rpm:0,position_deg:0,updated_at:utc(),position_at:utc(),stale:false,position_stale:false},capabilities:{modes:58,fields:(1 << 24) - 1,maximumOutput:true},limits:{517:-30000,519:20000000},limitA:30,supportedMaxA:30,fullTargetA:10});
const noIo = {call:async () => []};
async function bench(store = new MemoryStore()) {const env = new Bench(noIo,store,() => {});await env.subjects.initialize();return env;}
const client = env => ({'x-motorbench-token':env.token,'x-workbench-client':'browser-test-client-0001','x-control-epoch':String(env.epoch)});

test('subject setup changes remain locked during recording; bare and geared ratios stay distinct',async () => {
  const store = new MemoryStore();let busy = false;
  const subjects = new TestSubjects(store,() => busy);await subjects.initialize();
  const motor = (await subjects.create('motor','Browser motor')).item;
  const bare = (await subjects.saveSetup({motorId:motor.id,name:'裸机',hasGearbox:false,ratio:1,massKg:1,volumeL:.5})).item;
  const geared = (await subjects.saveSetup({motorId:motor.id,name:'带箱',hasGearbox:true,ratio:9.67,massKg:2,volumeL:1})).item;
  await subjects.select({motorId:motor.id,testSetupId:geared.id});assert.equal(subjects.selection().testSetup.ratio,9.67);
  busy = true;await assert.rejects(subjects.select({motorId:motor.id,testSetupId:bare.id}),/测试或记录/);await assert.rejects(subjects.saveSetup({...bare,ratio:1}),/测试或记录/);
  busy = false;await subjects.select({motorId:motor.id,testSetupId:bare.id});assert.equal(subjects.selection().testSetup.ratio,1);
  await assert.rejects(subjects.saveSetup({motorId:motor.id,name:'错误裸机',hasGearbox:false,ratio:2}),/裸机/);
});

test('recording persists every raw sensor frame, freezes names and setup, and history never loads sample chunks',async t => {
  const store = new MemoryStore(), source = () => ({sensor:{connected:true,snapshot:{receivedAt:utc()}}});
  let subject = {motor:{name:'motor A'},driverBoard:{name:'driver A'},testSetup:{id:'setupA',name:'bare',ratio:1,axis:'motor'}};
  const recorder = new Recordings(store,source,() => subject);t.after(() => clearInterval(recorder.timer));
  const report = await recorder.start({name:'native run'});subject = {motor:{name:'motor B'},testSetup:{ratio:9}};
  recorder.observe('sensor_frames',{config:{torqueDecimals:2,speedDecimals:1},frames:Array.from({length:1000},(_,index) => [index + 1,utc(),-123,250])});
  await recorder.stop(report.id);
  const jsonl = await recorder.file(report.id,'timeline.jsonl'), rows = jsonl.body.trim().split('\n').map(row => JSON.parse(row));
  const frames = rows.filter(row => row.source === 'sensor_frames');assert.equal(frames.length,1000);assert.equal(frames[999].sensor_sequence,1000);assert.equal(frames[0].torque_Nm,-1.23);assert.equal(frames[0].motor_name,'motor A');assert.equal(frames[0].driver_board_name,'driver A');assert.equal(frames[0].reduction_ratio,1);
  store.queries = [];assert.equal((await recorder.history()).length,1);assert.deepEqual(store.queries,['reports:recordings:']);
  const csv = await recorder.file(report.id,'timeline.csv');assert.ok(csv.body.includes('sensor_sequence'));assert.ok(csv.body.includes('motor A'));
});

test('a failed IndexedDB chunk write retains the buffered samples and retries the same chunk number',async t => {
  const store = new MemoryStore(), recorder = new Recordings(store,() => ({sensor:{connected:true,snapshot:{receivedAt:utc()}}}),() => ({}));
  t.after(() => clearInterval(recorder.timer));const report = await recorder.start();recorder.observe('sample',{value:42});
  const put = store.put.bind(store);let fail = true;
  store.put = async (key,value) => {if (key.startsWith('chunks:') && fail) throw new Error('quota');return put(key,value);};
  await assert.rejects(recorder.flush(),/quota/);assert.ok(recorder.buffer.length > 0);assert.equal(recorder.part,0);
  fail = false;await recorder.flush();assert.equal(recorder.buffer.length,0);assert.equal(recorder.part,1);await recorder.stop(report.id);
});

test('STOP during prepare cancels a pending current start before any START command is sent',async () => {
  const gate = deferred();let starts = 0;
  const device = {snapshot,prepare:() => gate.promise,start:async () => {starts++;},stop:async () => ({deviceDisabled:true})};
  const motor = new CurrentMotor(device,{});await motor.start({mode:'maximum_output',busLimitA:20,busVoltageV:24});
  const task = motor.task;await motor.stop();gate.resolve(123);await task;
  assert.equal(starts,0);assert.equal(motor.run.running,false);assert.equal(motor.run.stopConfirmed,true);
});

test('STOP while the output journal is pending cancels the dispatch and never starts the motor',async () => {
  const store = new MemoryStore(), env = await bench(store), entered = deferred(), release = deferred();let starts = 0;
  env.motor.start = async () => {starts++;};const put = store.put.bind(store);
  store.put = async (key,value) => {if (key === 'output-journal' && value.dirty) {entered.resolve();await release.promise;}return put(key,value);};
  const request = env.request('/api/motor-current/start','POST',{mode:'maximum_output'},client(env));await entered.promise;
  env.beginStop('test');await env.stopTask;release.resolve();await assert.rejects(request,/停止取消/);assert.equal(starts,0);
});

test('journal failure prevents ON and does not call the motor execution layer',async () => {
  const store = new MemoryStore(), env = await bench(store);let starts = 0;
  env.motor.start = async () => {starts++;};store.put = async () => {throw new Error('storage full');};
  await assert.rejects(env.request('/api/motor-current/start','POST',{mode:'maximum_output'},client(env)),/storage full/);assert.equal(starts,0);
});

test('a late PSU OFF request is drained while CAN STOP confirmation is pending',async () => {
  const env = await bench(), entered = deferred(), release = deferred();let off = 0;
  env.device.bus = {closed:false};env.motor.stop = async () => {entered.resolve();await release.promise;};
  env.channels['motor-supply'].config = {};env.channels['motor-supply'].output = async enabled => {assert.equal(enabled,false);off++;};
  env.beginStop('motor stop');await entered.promise;env.beginStop('power off',['motor-supply']);release.resolve();await env.stopTask;
  assert.equal(off,1);assert.equal(env.safety().stopping,false);
});

test('STOP during automatic-record creation cannot launch the coupled motor later',async () => {
  const entered = deferred(), release = deferred();let starts = 0;
  const power = {status:() => ({connected:true,config:{},snapshot:{receivedAt:utc(),status:128,settings:[1,2400,2000],measured:[2400,0]}}),read:async function(){return this.status();}};
  power.status = () => ({connected:true,config:{protocol:'ru',voltageDecimals:2,currentDecimals:2,measuredCurrentDecimals:2},snapshot:{receivedAt:utc(),status:128,settings:[1,2400,2000],measured:[2400,0]}});
  const brake = {config:{},output:async () => ({}),setValues:async () => ({}),status:() => ({connected:true})};
  const env = {channels:{'motor-supply':power,'brake-supply':brake},sensor:{status:() => ({connected:true,snapshot:{receivedAt:utc()}})},motor:{run:{running:false},device:{},status:() => ({snapshot:snapshot()}),start:async () => {starts++;}},brake:{run:{running:false},status:() => ({}),stop:async () => {}},safety:() => ({}),recordings:{live:null,start:async () => {entered.resolve();await release.promise;return {id:'test'};}},observe:() => {}};
  const coupled = new CoupledCurrent(env);await coupled.start({mode:'torque_sweep',motorMode:'maximum_output',brake:{startNm:0,stepNm:.1,maxNm:1,intervalS:1}});await entered.promise;
  await coupled.stop();release.resolve();await coupled.task;assert.equal(starts,0);
});

test('STOP during specialized report creation cannot allocate a fresh task generation afterward',async () => {
  const env = await bench(), entered = deferred(), release = deferred();let prepare = 0;
  const motor = (await env.subjects.create('motor','recipe motor')).item;
  const setup = (await env.subjects.saveSetup({motorId:motor.id,name:'bare',hasGearbox:false,ratio:1})).item;await env.subjects.select({motorId:motor.id,testSetupId:setup.id});
  env.motor.status = () => ({connected:true,snapshot:snapshot()});env.motor.healthy = () => {};env.device.prepare = async () => {prepare++;};
  env.sensor.status = () => ({connected:true,snapshot:{receivedAt:utc()}});
  const original = env.specialized.save.bind(env.specialized);let first = true;
  env.specialized.save = async report => {if (first) {first = false;entered.resolve();await release.promise;}return original(report);};
  const start = env.specialized.start({suite:'performance',test:'kt',motionConfirmed:true,iqLevelsA:[.5,1,1.5]},'automatic');await entered.promise;
  env.epoch++;env.specialized.generation.automatic++;release.resolve();await assert.rejects(start,/停止取消/);assert.equal(prepare,0);assert.equal(env.specialized.active(),false);
});

test('standstill confirmation uses fresh independent positions; repeated cached positions do not advance time',() => {
  const detector = new StandstillConfirmation(), motor = snapshot();motor.motor.state = 2;motor.motor.output_on = true;
  const measured = {speed:0,speedDecimals:1};const status = {snapshot:motor};
  motor.motor.updated_at = motor.feedback.updated_at = motor.feedback.position_at = utc() - .5;
  assert.equal(detector.update(measured,status,true),false);const count = detector.samples.length;
  for (let i = 0; i < 100; i++) assert.equal(detector.update(measured,status,true),false);
  assert.equal(detector.samples.length,count);assert.equal(detector.evidence.confirmed,false);
});

test('torque sweep keeps the original measured feedforward boundary and strict 1.999 A excitation cap',() => {
  const control = new BrakeTorque();for (let i = 0; i < 1000; i++) assert.ok(control.update(99,0) <= 1.999);
  assert.equal(control.current,1.999);assert.equal(control.update(0,1),0);assert.equal(control.current,0);
});

test('specialized metrics keep measured-current regression, directional repeats and thermal operating-point qualification',() => {
  assert.deepEqual(fit([[1,2],[2,4],[3,6]]),{slope:2,intercept:0,rSquared:1,count:3,minIqA:1,maxIqA:3});
  const visits = [0,1].flatMap(cycle => ['forward','reverse'].map((direction,index) => ({visitId:cycle * 2 + index + 1,nominalDeg:90,targetDeg:90,meanDeg:90 + .1 * index + .01 * cycle,direction})));
  const position = positioning(visits);assert.equal(position.claim,'feedback_only_or_partial_reference');assert.equal(position.externalCoverageComplete,false);assert.ok(Math.abs(position.feedbackWorstRepeatRangeDeg - .01) < 1e-10);
  const points = Array.from({length:11},(_,index) => ({t:index,temperatureC:30,phase:'heating',iq:1,speedRpm:5,torqueNm:1}));
  assert.equal(thermal(points,{thermalWindowS:10,thermalSlopeCPerMin:.1,ambientC:20}).stable,true);
  points[5].iq = 2;assert.equal(thermal(points,{thermalWindowS:10,thermalSlopeCPerMin:.1}).stable,false);
});

test('a reloaded dirty motor journal cannot be cleared without reconnecting the original driver or power',async t => {
  const store = new MemoryStore();await store.put('output-journal',{dirty:true,expected:['current-motor'],motorMayBeOn:true});
  const env = new Bench(noIo,store,() => {});await env.initialize();t.after(() => env.timers.forEach(clearInterval));
  env.beginStop('reconcile');await env.stopTask;
  assert.equal(env.blocked,true);assert.equal(env.motor.run.stopConfirmed,false);assert.equal((await store.get('output-journal')).dirty,true);
  env.device.bus = {closed:false};env.motor.stop = async () => {env.device.state.flags = 0;};
  env.beginStop('reconnected original driver');await env.stopTask;
  assert.equal(env.blocked,false);assert.equal((await store.get('output-journal')).dirty,false);
});

test('losing a power channel with uncertain output invokes the existing unified stop',async () => {
  const env = await bench(), channel = env.channels['motor-supply'];let keys;
  channel.config = {};channel.outputUncertain = true;channel.read = async () => {throw new Error('disconnected');};
  env.beginStop = (_reason,requested) => {keys = requested;};await env.poll();
  assert.deepEqual(keys,['motor-supply','brake-supply']);assert.equal(channel.connected,false);
});

test('recovery never clears an unresolved power journal merely because CAN is already stopped',async t => {
  const store = new MemoryStore();await store.put('output-journal',{dirty:true,expected:['motor-supply'],motorMayBeOn:false});
  const env = new Bench(noIo,store,() => {});await env.initialize();t.after(() => env.timers.forEach(clearInterval));
  env.beginStop('retry motor stop');await env.stopTask;
  assert.equal(env.blocked,true);assert.equal((await store.get('output-journal')).dirty,true);
});

test('maximum-output torque sweep waits for the second click and completes all configured load steps',async t => {
  const env = await bench(), power = {config:{protocol:'ru',voltageDecimals:2,currentDecimals:2},status() {return {connected:true,config:this.config,snapshot:{config:this.config,receivedAt:utc(),status:128,settings:[1,2400,2000],measured:[2400,0]}};},async read() {return this.status();}};
  let current = 0,on = false,starts = 0,stops = 0;
  const channel = {config:{protocol:'ssj',voltageDecimals:2,currentDecimals:3},snapshot:{status:0},status() {return {connected:true,config:this.config,snapshot:{config:this.config,receivedAt:utc(),status:on ? 128 : 0,settings:[1,2400,current * 1000],measured:[2400,0]}};},async output(value) {on = value;this.snapshot.status = on ? 128 : 0;return this.status();},async setValues(value) {current = value.current;},async read() {return this.status();}};
  env.channels = {'motor-supply':power,'brake-supply':channel};
  env.sensor = {status:() => ({connected:true,snapshot:{receivedAt:utc(),torque:.1 + (on ? current * 4 : 0),speed:100,speedDecimals:1}})};
  env.device.bus = {closed:false};env.device.wait = async predicate => {assert.equal(!!predicate(),true);};
  env.motor.status = () => ({connected:true,snapshot:snapshot(),run:env.motor.run});
  env.motor.start = async data => {assert.equal(data.mode,'maximum_output');starts++;env.motor.run = {running:true,phase:'running',mode:'maximum_output',maximumRequested:true,maximumRequestedAt:0};};
  env.motor.stop = async () => {stops++;env.motor.run.running = false;};
  env.brake = {run:{running:false},status:() => ({}),stop:async () => channel.output(false)};
  env.coupled = new CoupledCurrent(env);t.after(() => clearInterval(env.recordings.timer));
  await env.coupled.start({mode:'torque_sweep',motorMode:'maximum_output',brake:{startNm:0,stepNm:.2,maxNm:.4,intervalS:.5}});
  const deadline = Date.now() + 3000;while (env.coupled.run.phase !== 'awaiting_load' && Date.now() < deadline) await new Promise(done => setTimeout(done,10));
  assert.equal(env.coupled.run.phase,'awaiting_load');await new Promise(done => setTimeout(done,100));assert.equal(on,false);assert.equal(starts,1);
  env.coupled.load({id:env.coupled.run.id});await env.coupled.task;
  assert.equal(env.coupled.run.loadSteps,3);assert.match(env.coupled.run.reason,/阶梯完成/);assert.equal(env.coupled.run.stopConfirmed,true);assert.equal(on,false);assert.equal(stops,1);
  const reports = await env.recordings.history();assert.equal(reports.length,1);assert.equal(reports[0].running,false);
});

test('browser specialized recipes finish and retain named geared/bare records, source rows and confirmed stop',async t => {
  for (const suite of ['performance','position','thermal']) await t.test(suite,async t => {
    const env = await bench();let enabled = false,mode = 0,target = 0;
    const motor = (await env.subjects.create('motor','recipe fixture motor')).item;
    const setup = (await env.subjects.saveSetup({motorId:motor.id,name:suite === 'position' ? 'geared' : 'bare',hasGearbox:suite === 'position',ratio:suite === 'position' ? 9 : 1})).item;
    await env.subjects.select({motorId:motor.id,testSetupId:setup.id});
    env.motor.status = () => {const s = snapshot();s.motor.output_on = enabled;s.motor.state = enabled ? 2 : 0;s.motor.mode = mode;s.feedback.position_deg = mode === 4 ? target / 1000000 * 360 : 0;s.feedback.iq_a = mode === 1 ? target / 1000 : 0;s.feedback.id_a = 0;s.feedback.speed_rpm = 0;return {connected:true,snapshot:s,run:env.motor.run};};
    env.motor.healthy = () => {};env.motor.stop = async () => {enabled = false;env.motor.run.running = false;};
    const {O} = await import('../browser/mbp-codec.js');env.device.prepare = async config => {mode = config[O.run_mode];return 1;};
    env.device.start = async () => {enabled = true;};env.device.setTarget = async (_oid,value) => {target = Number(value);return {};};
    env.sensor = {status:() => ({connected:true,snapshot:{receivedAt:utc(),torque:target / 1000 * 2,speed:0}}),batch:() => ({frames:[]})};
    t.after(() => clearInterval(env.recordings.timer));
    const body = {suite,motionConfirmed:true,durationS:.25,windowS:.1,settleS:0,dwellS:.25,coolingS:.1,iqLevelsA:[.5,1,1.5],positionsDeg:[0,90],cycles:1};
    const {current} = await env.specialized.start(body,'automatic');await env.specialized.tasks.automatic;
    const report = await env.store.get('reports:automatic:' + current.id);
    assert.equal(report.state,'completed',report.reason);assert.equal(report.stopConfirmed,true);assert.equal(enabled,false);assert.equal(report.testSubject.motor.name,'recipe fixture motor');assert.equal(report.config.ratio,setup.ratio);
    const file = await env.specialized.file('automatic',current.id,'timeline.jsonl');assert.ok(file.body.includes('source_received_unix_s'));
    if (suite === 'position') {assert.equal(report.positionVisits.length,4);assert.equal(report.positionVisits[1].targetDeg,90);assert.equal(report.positionVisits[1].meanDeg,90);}
    if (suite === 'thermal') assert.ok(report.thermal.count >= 3);
  });
});

test('browser supply limit updates preserve the independent 2.3A brake ceiling',async () => {
  const env = await bench(),channel = env.channels['brake-supply'];channel.config = {maxVoltage:24,maxCurrent:2.3,ratedCurrent:60};
  await assert.rejects(env.request('/api/supplies/brake-supply/limits','POST',{maxVoltage:12,maxCurrent:20},client(env)),/限幅电流/);
  assert.equal(channel.config.maxCurrent,2.3);assert.equal(channel.config.maxVoltage,24);
});

test('failed autonomous stop retains uncertainty and invokes the original power-discharge fallback',async () => {
  const stopped = new BrakeCurrent({output:async () => {},status:() => ({})});stopped.run.running = true;
  assert.equal((await stopped.stop()).run.running,false);
  const brake = new BrakeCurrent({output:async () => {throw new Error('no OFF acknowledgement');},status:() => ({})});
  brake.run.running = true;await assert.rejects(brake.stop(),/no OFF acknowledgement/);
  assert.equal(brake.run.running,false);assert.equal(brake.run.phase,'stopped');assert.equal(brake.run.stopConfirmed,false);
  const env = await bench();let off = 0;
  env.owner = 'active-browser-client';env.motor.run = {running:false,phase:'stopped',stopConfirmed:false};
  env.device.bus = {closed:false};env.motor.stop = async () => {throw new Error('CAN link lost');};
  const channel = env.channels['motor-supply'];channel.config = {voltageDecimals:2};channel.outputUncertain = true;
  channel.output = async enabled => {assert.equal(enabled,false);off++;channel.outputUncertain = false;channel.snapshot = {status:0,measured:[0,0]};};channel.read = async () => channel.status();
  env.publish();await env.stopTask;
  assert.ok(off > 0);assert.equal(env.motorIsolated,true);assert.equal(env.blocked,false);assert.equal(env.motor.run.stopConfirmed,true);assert.equal((await env.store.get('output-journal')).dirty,false);
});
