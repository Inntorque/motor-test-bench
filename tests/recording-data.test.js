import test from 'node:test';
import assert from 'node:assert/strict';
import {Recordings} from '../browser/storage.ts';
import {alignedColumns, motorColumns, sensorColumns, supplyColumns} from '../browser/recording-data.ts';
import {Bench} from '../browser/bench.ts';
import {shaftPowerKw} from '../web/src/model.ts';

const subject=()=>({motor:null,driverBoard:null,testSetup:{ratio:10}});
async function logger(t,source=()=>({})) {
  const chunks=[];
  const recording=new Recordings({put:async(key,value)=>{if(key.startsWith('chunks:'))chunks.push(...structuredClone(value.rows));}},source,subject,true);
  t.after(()=>clearInterval(recording.timer));await recording.start();
  return {recording,chunks};
}
const supply={receivedAt:100,config:{voltageDecimals:2,currentDecimals:2,measuredCurrentDecimals:3,maxVoltage:24,maxCurrent:20},settings:[1,2400,1000],measured:[2400,2000,48],status:128};

test('source elapsed time follows each received frame, while receive batches remain explicit',async t=>{
  const {recording}=await logger(t), start=recording.live.startedAt;
  recording.context=()=>({runId:'run1',kind:'coupled',phase:'loading'});
  recording.observe('sensor_frames',{streamId:'s1',batchId:7,config:{torqueDecimals:2,speedDecimals:1},frames:[[1,start+.001,200,6000],[2,start+.002,201,6000],[3,start+.002,202,6000]]});
  const rows=recording.buffer.filter(row=>row.source==='sensor_frames');
  assert.ok(Math.abs(rows[0].source_recording_elapsed_s-.001)<1e-6);
  assert.ok(Math.abs(rows[1].elapsed_s-.002)<1e-6);
  assert.equal(rows[2].elapsed_s,rows[1].elapsed_s);
  assert.equal(rows[0].recording_elapsed_s,rows[2].recording_elapsed_s);
  assert.deepEqual(rows.map(r=>r.sensor_frame_index),[0,1,2]);
  assert.ok(rows.every(r=>r.sensor_stream_id==='s1'&&r.sensor_batch_id===7&&r.timestamp_basis==='host_receive_batch'&&r.phase==='loading'&&r.run_id==='run1'));
});

test('shaft power matches the UI and retains a separate signed value; supply units honor readback scaling',()=>{
  assert.equal(sensorColumns(-2,600).shaft_power_W,shaftPowerKw(-2,600)*1000);
  assert.equal(sensorColumns(-2,600).shaft_power_signed_W,-shaftPowerKw(-2,600)*1000);
  assert.equal(sensorColumns(null,600).shaft_power_W,null);
  const result=supplyColumns(supply);
  assert.equal(result.supply_actual_voltage_V,24);assert.equal(result.supply_actual_current_A,2);
  assert.equal(result.supply_actual_power_W,48);assert.equal(result.supply_target_current_A,10);
});

test('feedback columns include commanded current, phases and freshness; brake event fields are populated',async t=>{
  const snapshot={feedback:{iq_a:3,id_a:0,iq_reference_a:4,speed_rpm:1000,position_deg:360,stale:false,position_stale:false},phaseCurrent:{ia_mA:1000,ib_mA:-500,ic_mA:-500,stale:false},control:{iq_target_ma:5000},ntc:{valid:true,temp_c:35}};
  const columns=motorColumns(snapshot,10);
  assert.equal(columns.commanded_iq_A,5);assert.equal(columns.phase_a_A,1);assert.equal(columns.output_speed_rpm,100);
  assert.equal(motorColumns({...snapshot,feedback:{...snapshot.feedback,stale:true}},10).actual_iq_A,null);
  const {recording}=await logger(t);
  recording.observe('torque_feedback',{targetNm:3,measuredNm:2.8,currentA:.4});
  const row=recording.buffer.at(-1);
  assert.equal(row.brake_target_torque_Nm,3);assert.equal(row.brake_feedback_torque_Nm,2.8);assert.equal(row.brake_command_current_A,.4);
});

test('aligned rows distinguish supplies, expose sample age and never reuse stale, future or disconnected data',()=>{
  const devices={'motor-supply':{connected:true,snapshot:supply},'brake-supply':{connected:true,snapshot:{...supply,measured:[1200,500,6]}},'torque-sensor':{connected:true,snapshot:{receivedAt:100,torque:2,speed:600}}};
  const row=alignedColumns(devices,100.05,10,{});
  assert.equal(row.motor_supply_actual_power_W,48);assert.equal(row.brake_supply_actual_power_W,6);
  assert.equal(row.sensor_valid,true);assert.ok(Math.abs(row.sensor_age_s-.05)<1e-10);
  assert.equal(alignedColumns(devices,102,10,{}).torque_Nm,undefined);
  assert.equal(alignedColumns(devices,99,10,{}).sensor_valid,false);
  assert.equal(alignedColumns({...devices,'torque-sensor':{...devices['torque-sensor'],connected:false}},100.05,10,{}).torque_Nm,undefined);
});

test('final desktop close drains received frames before stopping the automatic recorder',async t=>{
  const {recording,chunks}=await logger(t);
  const bench=new Bench({call:async()=>{throw Error('no hardware');}},{put:async()=>{}},()=>{});
  bench.autoRecording=recording;
  bench.sensor.config={torqueDecimals:2,speedDecimals:1};
  bench.sensor.pendingFrames=[[10,Date.now()/1000,100,200],[11,Date.now()/1000,101,201]];
  assert.equal((await bench.request('/api/desktop/close-ready?finish=1','GET',{})).ready,true);
  assert.deepEqual(chunks.filter(r=>r.source==='sensor_frames').map(r=>r.sensor_sequence),[10,11]);
  assert.equal(bench.sensor.pendingFrames.length,0);
});

test('recording stop invokes the receiver drain for non-desktop and trial-owned sessions too',async t=>{
  const {recording,chunks}=await logger(t);
  recording.drain=()=>recording.observe('sensor_frames',{config:{torqueDecimals:2,speedDecimals:1},frames:[[4,Date.now()/1000,10,20]]});
  await recording.stop(null);
  assert.equal(chunks.filter(row=>row.source==='sensor_frames').length,1);
});

test('recorded context distinguishes loading, motor and brake control',()=>{
  const bench=new Bench({}, {}, ()=>{});
  bench.coupled.run={id:'c',running:true,phase:'loading',targetNm:2,measuredNm:1.9,brakeCurrentA:.3};
  assert.deepEqual(bench.recordingContext(),{runId:'c',kind:'coupled',phase:'loading',targetNm:2,measuredNm:1.9,currentA:.3});
  bench.coupled.run.running=false;bench.motor.run={id:'m',running:true,phase:'running'};
  assert.equal(bench.recordingContext().runId,'m');
  bench.motor.run.running=false;bench.brake.run={id:'b',running:true,phase:'running'};
  assert.equal(bench.recordingContext().kind,'brake');
});
