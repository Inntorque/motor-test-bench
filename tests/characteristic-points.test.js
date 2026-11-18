import test from 'node:test';
import assert from 'node:assert/strict';
import {StagePointCollector,characteristicObservation,isOutputPoint,CHARACTERISTIC_INTERVAL_SECONDS} from '../browser/characteristic-points.ts';
const sensor=(receivedAt,torque=.15,speed=100)=>({receivedAt,torque,speed});
const supply=stamp=>({connected:true,config:{voltageDecimals:2,currentDecimals:3},snapshot:{receivedAt:stamp,measured:[2400,1000]}});
const feed=(collector,start,end,torque=.15,speed=100,electrical=supply)=>{
 for(let i=Math.round(start*20);i<=Math.round(end*20);i++)collector.update(sensor(i/20,torque,speed),electrical(i/20-.01));
};
const stage=(target=.15,index=1)=>new StagePointCollector('run',index,target,10,CHARACTERISTIC_INTERVAL_SECONDS);
test('one point on the fixed 2-second deadline uses only the final measurement window',()=>{
 const c=stage();
 feed(c,9,11.45,.8,200);
 feed(c,11.5,12);
 for(let i=0;i<20;i++)c.update(sensor(12),supply(11.99));
 c.update(sensor(12.05,.8,200),supply(12.04));
 const p=c.finish();
 assert.equal(p.torque,.15);assert.equal(p.stage,1);assert.equal(p.sampleCount,11);
 assert.equal(p.startedAt,11.5);assert.equal(p.receivedAt,12);assert.equal(p.scheduledAt,12);
 assert.throws(()=>c.finish(),/已完成/);
 assert.ok(Math.abs(p.power-.15*100/9550*1000)<1e-10);assert.equal(p.voltage,24);assert.equal(p.inputPower,24);
 assert.equal(p.efficiency,p.power/24*100);
});
test('stable speed preserves a measured torque below the open-loop target',()=>{
 const c=stage(.2);feed(c,11.5,12,.14);
 const point=c.finish();
 assert.equal(point.torque,.2);
 assert.ok(Math.abs(point.measuredTorque-.14)<1e-10);
});
test('fluctuating readings at one output produce only one point when the stage completes',()=>{
 const c=stage(.3);
 for(const [stamp,torque,rpm] of [[9.99,2,150],[10,.4,120],[11.5,.42,100],[11.7,.07,90],[11.9,.42,80]])
   assert.equal(c.update(sensor(stamp,torque,rpm),supply(stamp-.01)),undefined);
 const p=c.finish();
 assert.equal(p.kind,'stage');assert.equal(p.torque,.3);
 assert.equal(p.sampleCount,3);assert.equal(p.speed,90);
 assert.ok(Math.abs(p.power-(.42*100+.07*90+.42*80)/3/9550*1000)<1e-10);
 assert.throws(()=>c.finish(),/已完成/);
});
test('low-rate sampling keeps fresh final readings and rejects an empty final window',()=>{
 const slow=stage();slow.update(sensor(11.73),supply(11.7));assert.equal(slow.finish().sampleCount,1);
 const stale=stage();feed(stale,10,11.3);assert.throws(()=>stale.finish(),/无有效/);
});
test('mechanical sampling keeps unavailable electrical data blank',()=>{
 const c=stage();feed(c,11.5,12,.15,100,()=>supply(20));
 const p=c.finish();assert.equal(p.current,null);assert.equal(p.efficiency,null);assert.equal(p.electricalCount,0);
 assert.equal(characteristicObservation(sensor(10),supply(7)).voltage,null);
});
test('zero-load residual torque is measured only after loading and is not relabelled as zero',()=>{
 const c=stage(0,0);feed(c,11.5,12,.11);
 const p=c.finish();assert.equal(p.torque,0);assert.ok(Math.abs(p.measuredTorque-.11)<1e-10);assert.ok(p.power>0);
});
test('powered standstill is a separate real endpoint, not a fabricated periodic sample',()=>{
 const c=stage(.45,3);
 c.update(sensor(10.3,.4,0),supply(10.2));
 assert.equal(c.stall(sensor(10.4,.4,2),supply(10.3)),null);
 const p=c.stall(sensor(10.5,.4,0),supply(10.4));assert.equal(p.kind,'stall');assert.equal(p.torque,.45);assert.equal(p.measuredTorque,.4);assert.equal(p.speed,0);assert.equal(p.power,0);assert.equal(p.efficiency,0);
 assert.equal(p.scheduledAt,undefined);assert.equal(p.receivedAt,10.5);
 assert.equal(c.stall(sensor(10.6,.4,0),supply(10.5)),null);
});

test('characteristic exports accept only completed output settings with an explicit torque',()=>{
 const rows=[{kind:'stage',torque:0},{kind:'sweep',torque:.15},{kind:'stall',torque:.30},{kind:'stage',measuredTorque:.45}];
 assert.deepEqual(rows.filter(isOutputPoint).map(p=>p.torque),[0,.30]);
});
