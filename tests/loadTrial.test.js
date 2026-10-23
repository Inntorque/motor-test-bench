import test from 'node:test';
import assert from 'node:assert/strict';
import {LoadTrial,measuredBrakeLoad} from '../web/src/loadTrial.js';
import {CharacteristicWindow} from '../web/src/characteristicData.js';

const point=(t,extra={})=>({t,receivedAt:new Date((1000+t)*1000).toISOString(),torque:2,speed:5,
  loadEnabled:true,brakeSupplyCurrent:.2,brakeSupplyVoltage:3,brakeSupplyReceivedAt:1000+t,...extra});
test('whole trial survives rolling live history and snapshot stays unchanged after stop',()=>{
  const trace=new LoadTrial({id:'a',running:true,startedAt:1000});
  trace.append([point(-1),...Array.from({length:12001},(_,i)=>point(i/100))]);
  const points=trace.snapshot();
  assert.equal(points[0].t,0);assert.equal(points.at(-1).t,120);
  trace.update({id:'a',running:false,startedAt:1000,measurementEndedAt:1110});
  const frozen=trace.snapshot();
  trace.append([point(121)]);
  assert.equal(frozen.at(-1).t,110);assert.equal(trace.snapshot().length,frozen.length);
  const rows=new CharacteristicWindow().update(frozen,Infinity);
  assert.equal(rows.reduce((n,r)=>n+r.count,0),frozen.length);
});
test('trace preserves within-bucket torque/speed extrema and discards stale duplicates',()=>{
  const trace=new LoadTrial({running:true,startedAt:1000});
  trace.append([point(0),point(.001,{torque:8}),point(.002,{speed:12}),point(.003,{torque:-3}),point(.019)]);
  trace.append([point(0),point(.019)]);
  const data=trace.snapshot();
  assert.equal(data.length,5);assert.ok(data.some(p=>p.torque===8));assert.ok(data.some(p=>p.speed===12));
});
test('new recovery epoch clears failed-start data',()=>{
  const trace=new LoadTrial({running:true,startedAt:1000});trace.append([point(0)]);
  trace.update({running:true,startedAt:1002});trace.append([point(1),point(2)]);
  assert.deepEqual(trace.snapshot().map(p=>p.t),[2]);
});
test('stopped trace accepts delayed endpoint once, keeps zero-load background and excludes later coastdown',()=>{
  const trace=new LoadTrial({id:'zero',running:true,startedAt:1000,curveStartedAt:1001});
  trace.append([point(0),point(1,{loadEnabled:false,torque:.18}),point(2),point(5)]);
  trace.update({id:'zero',running:false,startedAt:1000,curveStartedAt:1001,measurementEndedAt:1003});
  assert.equal(trace.append([point(2.5),point(3,{speed:0}),point(4,{speed:0})]),true);
  const frozen=trace.snapshot();
  assert.deepEqual(frozen.map(p=>p.t),[1,2,2.5,3]);
  assert.equal(frozen[0].torque,.18);
  assert.equal(frozen.at(-1).speed,0);
  assert.equal(trace.append([point(3),point(4)]),false);
  assert.deepEqual(trace.snapshot(),frozen);
});
test('empty stopped trace retains final buffered baseline and endpoint instead of falling back to live data',()=>{
  const trace=new LoadTrial({running:true,startedAt:1000});
  trace.update({running:false,startedAt:1000,measurementEndedAt:1002});
  assert.equal(trace.append([point(0,{torque:.18}),point(2,{speed:0}),point(3)]),true);
  assert.deepEqual(trace.snapshot().map(p=>p.t),[0,2]);
});
test('load is measured shaft torque; stale PSU and stopped shaft never imply calibrated capacity',()=>{
  const data=Array.from({length:51},(_,i)=>point(i/50));
  assert.deepEqual(measuredBrakeLoad(data),{torque:2,steady:true,moving:true,current:.2,voltage:3});
  assert.equal(measuredBrakeLoad(data.map(p=>({...p,speed:0}))).steady,false);
  assert.equal(measuredBrakeLoad(data.map(p=>({...p,brakeSupplyReceivedAt:900}))).current,null);
  assert.equal(measuredBrakeLoad(data.map(p=>({...p,loadEnabled:false}))),null);
});
