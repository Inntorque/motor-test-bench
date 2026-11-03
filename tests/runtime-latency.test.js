import test from 'node:test';
import assert from 'node:assert/strict';
import {DeviceEvent,eventData} from '../browser/events.ts';
import {Bench} from '../browser/bench.ts';
import {LegacyApp} from '../browser/legacy.ts';
import {utc} from '../browser/bytes.ts';

class MemoryStore {
  data = new Map();
  async get(key) {return structuredClone(this.data.get(key));}
  async put(key,value) {this.data.set(key,structuredClone(value));}
  async entries() {return [];}
}
const noIo = {call:async () => []};

test('local device events preserve the text contract without encoding unread payloads',() => {
  let encoded = 0;
  const payload = {toJSON() {encoded++;return {frames:[[1,2,3,4]]};}};
  const event = new DeviceEvent('sensor_frames',payload);
  const stream = new EventTarget();
  stream.addEventListener('sensor_frames',event => assert.equal(eventData(event),payload));
  stream.dispatchEvent(event);
  assert.equal(encoded,0);
  assert.deepEqual(JSON.parse(event.data),{type:'sensor_frames',data:{frames:[[1,2,3,4]]}});
  assert.equal(event.data,event.data);
  assert.equal(encoded,1);
  const native = new MessageEvent('state',{data:'{"type":"state","data":{"value":42}}'});
  assert.deepEqual(eventData(native),{value:42});
});

test('frame notifications see the full cache before display, unsubscribe, and retain SSE support',async t => {
  const saved = globalThis.EventSource;let source;
  globalThis.EventSource = class {
    listeners = {};
    constructor() {source = this;}
    addEventListener(kind,callback) {this.listeners[kind] = callback;}
  };
  t.after(() => {globalThis.EventSource = saved;});
  const store = await import('../web/src/deviceStore.ts');
  store.startDeviceStore(async () => new Response(JSON.stringify({devices:{'torque-sensor':{connected:true}}})));
  await new Promise(resolve => setTimeout(resolve,0));
  const batches = [],stop = store.subscribeSensorFrames(() => batches.push(store.sensorFrameBatch(0)));
  const config = {torqueDecimals:2,speedDecimals:1};
  source.listeners.sensor_frames(new DeviceEvent('sensor_frames',{config,frames:[[1,10,123,456]]}));
  assert.deepEqual(batches[0].frames,[[1,10,123,456]]);
  assert.deepEqual(batches[0].config,config);
  batches[0].frames[0][2] = 999;
  assert.equal(store.sensorFrameBatch(0).frames[0][2],123);
  stop();
  source.listeners.sensor_frames(new MessageEvent('sensor_frames',{data:JSON.stringify({data:{config,frames:[[2,11,456,789]]}})}));
  assert.equal(batches.length,1);
  assert.deepEqual(store.sensorFrameBatch(1).frames,[[2,11,456,789]]);
});

test('heartbeat, STOP, sensor batches and parameter reads do not prepare unrelated display routes',async () => {
  const env = new Bench(noIo,new MemoryStore(),() => {});
  await env.subjects.initialize();
  const unexpected = () => {throw new Error('unrelated display work');};
  env.routes = env.motor.status = env.specialized.status = unexpected;
  const headers = {'x-motorbench-token':env.token};
  assert.equal((await env.request('/api/control/heartbeat','POST',{},headers)).epoch,0);
  assert.equal((await env.request('/api/sensors/torque-sensor/frames?since=0','GET',{})).frames.length,0);
  assert.equal((await env.request('/api/ports','GET',{})).ports.length,0);
  assert.ok((await env.request('/api/test-subjects','GET',{})).motors.length);
  await assert.rejects(env.request('/api/control/heartbeat','POST',{},{}),/控制令牌无效/);
  let stopped = false;
  env.beginStop = () => {stopped = true;return {stopping:true};};
  assert.equal((await env.request('/api/control/stop','POST',{},headers)).httpStatus,202);
  assert.equal(stopped,true);
});

test('one state publication evaluates motor feedback once and retains every existing display route',async () => {
  let event;
  const env = new Bench(noIo,new MemoryStore(),(kind,data) => {event = {kind,data};});
  await env.subjects.initialize();let calls = 0;
  env.motor.status = () => {calls++;return {connected:true,snapshot:{feedback:{iq_a:12},capabilities:{maximumOutput:true}},run:{running:false}};};
  env.publish();
  assert.equal(event.kind,'state');assert.equal(calls,1);
  const state = event.data,routes = state.routes;
  assert.deepEqual(Object.keys(routes).sort(),['/api/health','/api/control/status','/api/connections/status','/api/motor-current/status','/api/brake-current/status','/api/coupled-current/status','/api/recordings/status','/api/automatic/status','/api/specialized/status','/api/test-subjects'].sort());
  assert.deepEqual(routes['/api/health'].channels,state.devices);
  assert.deepEqual(routes['/api/coupled-current/status'].motor,state.devices['current-motor']);
  assert.deepEqual(routes['/api/motor-current/status'].safety,state.control);
  assert.equal(routes['/api/coupled-current/status'].capabilities.maximumOutput,true);
  assert.equal((await env.request('/api/motor-current/status','GET',{})).snapshot.feedback.iq_a,12);
  assert.equal(calls,2);
});

for (const id of ['sg-6010hb','rm-7010']) test(`${id} telemetry batches retain freshness without building the parameter display`,() => {
  const app = new LegacyApp(id,noIo,new MemoryStore(),() => {});
  const status = app.status.bind(app);
  for (const age of [null,0,1,3]) {
    app.telem.clear();
    if (age !== null) app.telem.append([[1,utc() - age,100,-200,100,240]]);
    const expected = status().telem?.stale || false;
    app.status = () => {throw new Error('parameter display requested by waveform batch');};
    assert.equal(app.batch(0,0).stale,expected);
    assert.deepEqual(app.batch(0,0).samples,age === null ? [] : [[100,-200,100,240]]);
  }
});
