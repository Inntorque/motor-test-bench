import test from 'node:test';
import assert from 'node:assert/strict';
import {Bench} from '../browser/bench.ts';

function fixture() {
  const writes = [];
  const bench = new Bench({call:async () => { throw new Error('Close check must not access hardware'); }},
    {put:async (key, value) => writes.push([key, value])}, () => {});
  return {bench, writes};
}

test('desktop close accepts active acquisition, unconfirmed outputs and stopping', async () => {
  const {bench} = fixture();
  assert.equal((await bench.request('/api/desktop/close-ready', 'GET', {})).ready, true);
  bench.recordings.live = {running:true};
  assert.equal((await bench.request('/api/desktop/close-ready', 'GET', {})).ready, true);
  bench.recordings.live = null;
  bench.channels['brake-supply'].outputUncertain = true;
  assert.equal((await bench.request('/api/desktop/close-ready', 'GET', {})).ready, true);
  bench.channels['brake-supply'].outputUncertain = false;
  bench.stopping = true;
  assert.equal((await bench.request('/api/desktop/close-ready', 'GET', {})).ready, true);
  bench.stopping = false; bench.blocked = true;
  assert.equal((await bench.request('/api/desktop/close-ready', 'GET', {})).ready, true);
});

test('desktop close flushes rows without waiting for a running device task', {timeout:1000}, async () => {
  const {bench, writes} = fixture();
  bench.coupled.task = new Promise(() => {});
  bench.coupled.run.running = true;
  bench.recordings.live = {id:'test',running:false,state:'complete'};
  bench.recordings.buffer = [{source:'sensor',sensor_speed_rpm:0}];
  assert.equal((await bench.request('/api/desktop/close-ready?finish=1', 'GET', {})).ready, true);
  assert.equal(writes[0][1].rows[0].sensor_speed_rpm, 0);
  assert.equal(bench.recordings.buffer.length, 0);
});

test('desktop close reports a final disk write failure instead of discarding the buffer', async () => {
  const {bench} = fixture();
  bench.recordings.live = {id:'test',running:false};
  bench.recordings.buffer = [{source:'sensor'}];
  bench.recordings.store.put = async () => { throw new Error('disk full'); };
  await assert.rejects(bench.request('/api/desktop/close-ready', 'GET', {}), /disk full/);
  assert.equal(bench.recordings.buffer.length, 1);
});

test('continuous saving does not lock configuration or closing; final close flushes it', async () => {
  const {bench} = fixture();
  let flushed=0, stopped=0;
  bench.autoRecording={live:{running:true},flush:async()=>{flushed++;},stop:async()=>{stopped++;}};
  assert.equal(bench.busy(),false);
  assert.equal((await bench.request('/api/desktop/close-ready','GET',{})).ready,true);
  assert.equal(flushed,1); assert.equal(stopped,0);
  assert.equal((await bench.request('/api/desktop/close-ready?finish=1','GET',{})).ready,true);
  assert.equal(stopped,1);
});

test('final close completes a manual recording even when device state remains blocked', async () => {
  const {bench, writes} = fixture();
  bench.blocked = true;
  bench.recordings.live = {id:'manual',running:true,state:'recording',counts:{}};
  bench.recordings.source = () => ({});
  bench.recordings.buffer = [{source:'sensor',sensor_speed_rpm:0}];
  assert.equal((await bench.request('/api/desktop/close-ready?finish=1','GET',{})).ready,true);
  assert.equal(bench.recordings.live.running,false);
  assert.equal(writes.at(-1)[1].state,'complete');
  assert.equal(writes[0][1].rows[0].sensor_speed_rpm,0);
});
