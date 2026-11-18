import test from 'node:test';
import assert from 'node:assert/strict';
import {CoupledCurrent} from '../browser/control.ts';

function fixture(speed = 300) {
  const events = [], calls = [];
  let connected = true, sequence = 0;
  const recordings = {live:{id:'record-1',running:true},async stop(id) { calls.push(`record-stop:${id}`); this.live.running=false; }};
  const sensor = {status() {return {connected,snapshot:{receivedAt:Date.now()/1000,framesReceived:++sequence,speed,torque:.42}};}};
  const env = {recordings,sensor,observe:(kind,data)=>events.push({kind,data}),
    motor:{run:{running:false},device:{bus:{closed:false}},status:()=>({}),async stop(){calls.push('motor-stop');}},
    brake:{run:{running:false},status:()=>({}),async stop(){calls.push('brake-stop');}},
    channels:{'brake-supply':{config:{}}},safety:()=>({})};
  const runner = new CoupledCurrent(env);
  runner.run = {id:'run-1',running:true,startedAt:Date.now()/1000-1};
  return {runner,recordings,events,calls,disconnect:()=>{connected=false;}};
}

test('stop commands finish before post-stop observation and a fresh real zero is retained',async()=>{
  const f=fixture(0);
  await f.runner.stop();
  assert.deepEqual(f.calls,['motor-stop','brake-stop']);
  await f.runner.collectStopTail('record-1');
  assert.equal(f.runner.run.postStopTailState,'zero');
  assert.equal(f.runner.run.postStopZero.speed,0);
  assert.equal(f.runner.run.postStopZero.torque,.42);
  assert.ok(f.runner.run.postStopZero.receivedAt>f.runner.run.measurementEndedAt);
});

test('disconnect ends observation without inventing a zero',async()=>{
  const f=fixture(); await f.runner.stop(); f.disconnect();
  await f.runner.collectStopTail('record-1');
  assert.equal(f.runner.run.postStopTailState,'disconnected');
  assert.equal(f.runner.run.postStopZero,undefined);
});

test('next trial cancels and closes the preceding owned recording first',async()=>{
  const f=fixture(); await f.runner.stop();
  f.runner.task=(async()=>{await f.runner.collectStopTail('record-1');await f.recordings.stop('record-1');})();
  await assert.rejects(f.runner.start({mode:'torque_sweep',brake:{}}));
  assert.equal(f.recordings.live.running,false);
  assert.ok(f.calls.includes('record-stop:record-1'));
  assert.equal(f.runner.run.postStopZero,undefined);
});
