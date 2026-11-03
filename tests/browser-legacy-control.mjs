import test from 'node:test';
import assert from 'node:assert/strict';
import {LegacyApp} from '../browser/legacy.ts';
import {sleep} from '../browser/bytes.ts';
class Store {
  data = new Map();
  async get(key) {return structuredClone(this.data.get(key));}
  async put(key,value) {this.data.set(key,structuredClone(value));}
  async entries(prefix) {return [...this.data].filter(([key]) => key.startsWith(prefix)).sort().map(([,value]) => structuredClone(value));}
}
function virtualCan() {
  let callback;const sent = [],board = {on:false,mode:0};
  return {sent,board,subscribe(_id,cb) {callback = cb;return () => {};},async call(method,args) {
    if (method === 'can-config') return {device:'can-test',node:0};
    if (method === 'ports') return [{port:'can-test',kind:'usb',description:'virtual'}];
    if (method !== 'usb-send') return;
    const data = args.data,op = data[0];sent.push([...data]);let frame;
    if (args.id === 0x600) frame = op === 1 ? [0x83,0,0,0,0,0x3c,0x0a,0] : [0x87,0,0,0,0,0,0,0];
    else {
      if (op === 8) {board.on = !!data[1];board.mode = data[2];}
      if (op === 9) frame = [0x86,board.mode,board.on ? 2 : 0,0,0,0,0,board.on ? 17 : 16];
      else if (op === 3) frame = [0x84,0,0,0,0,0,0,0];
      else if (op === 0x14) frame = [0xa5,board.mode,board.on ? 2 : 0,3,100,0,0,0];
      else if (op === 0x16) frame = [0xa6,0,0,10,0,20,0,0];
      else if (op === 0x0d) frame = data[1] === 5 ? [0x91,5,0,0,40,0,0,0] : [0x91,data[2],1,0,0,0,0,0];
      else frame = [0x81,op,0,0,0,0,0,0];
    }
    queueMicrotask(() => callback({id:args.id === 0x600 ? 0x580 : 0x590,data:Uint8Array.from(frame)}));
  }};
}
for (const app of ['sg-6010hb','rm-7010']) test(`${app} direct CAN keeps explicit start, confirmed stop, persistent recording and reconnect journal`,async t => {
  const io = virtualCan(),store = new Store(),engine = new LegacyApp(app,io,store,() => {});
  await engine.initialize();t.after(() => {clearInterval(engine.pollTimer);clearInterval(engine.heartbeatTimer);clearInterval(engine.diagnosticTimer);clearInterval(engine.recording.timer);});
  assert.equal(io.sent.length,0);await engine.connect();assert.equal(io.sent.some(row => row[0] === 8 && row[1] === 1),false);
  const mode = app === 'sg-6010hb' ? 7 : 2;
  const headers = () => ({'x-control-token':engine.token,'x-command-epoch':String(engine.epoch)});
  await engine.request(`/api/motor?enable=1&mode=${mode}`,'POST',{},headers());
  assert.equal(io.board.on,true);assert.equal((await store.get('output-journal')).dirty,true);
  await sleep(60);assert.equal(io.sent.filter(row => row[0] === 8 && row[1] === 1).length,1);
  const stop = await engine.request('/api/motor?enable=0','POST',{},headers());assert.equal(stop.stop_confirmed,true);assert.equal(io.board.on,false);
  assert.equal((await store.get('output-journal')).dirty,false);
  const records = await engine.recording.history();assert.equal(records.length,1);assert.equal(records[0].mode,mode);
  const jsonl = await engine.recording.file(records[0].id,'timeline.jsonl');assert.ok(jsonl.body.includes('can_rx'));assert.ok(jsonl.body.includes('sample'));
  const csv = await engine.recording.file(records[0].id,'run.csv');assert.ok(csv.body.includes('speed_pll_rpm'));
  await store.put('output-journal',{dirty:true});
  const reloaded = new LegacyApp(app,io,store,() => {});await reloaded.initialize();t.after(() => {clearInterval(reloaded.pollTimer);clearInterval(reloaded.heartbeatTimer);clearInterval(reloaded.diagnosticTimer);});
  await assert.rejects(reloaded.request(`/api/motor?enable=1&mode=${mode}`,'POST',{},{'x-control-token':reloaded.token,'x-command-epoch':'0'}),/未确认关闭/);
});
