import test from 'node:test';
import assert from 'node:assert/strict';
import {StandstillConfirmation} from '../browser/control.ts';

function rig(t) {
  let time=1000;
  t.mock.method(Date,'now',()=>time*1000);
  const detector=new StandstillConfirmation();
  const observe=(seconds,change=()=>{},eligible=true)=>{
    time=1000+seconds;
    const measured={speed:0,torque:.26,speedDecimals:1,receivedAt:time};
    const snapshot={motor:{state:2,output_on:true,fault:false,updated_at:time},encoder:{valid:true,stale:false},feedback:{speed_rpm:0,position_deg:1,updated_at:time,position_at:time}};
    change(measured,snapshot);
    return detector.update(measured,{snapshot},eligible);
  };
  return {detector,observe};
}

test('powered standstill requires five continuous seconds of independent fresh observations',t=>{
  const {detector,observe}=rig(t);
  for(let i=0;i<50;i++)assert.equal(observe(i/10),false);
  assert.equal(observe(5),true);
  assert.equal(detector.evidence.durationS,5);
});

test('rotation, loss of load eligibility and invalid feedback reset the five-second confirmation',t=>{
  for(const change of [
    m=>{m.speed=.1;},(_,s)=>{s.feedback.speed_rpm=.2;},(_,s)=>{s.feedback.position_deg=2;},
    (_,s)=>{s.motor.output_on=false;},(_,s)=>{s.motor.fault=true;},(_,s)=>{s.encoder.valid=false;},
    (_,s)=>{s.feedback.position_at-=2;},m=>{m.torque=NaN;},m=>{m.speed=NaN;},
  ]){
    const {detector,observe}=rig(t);
    for(let i=0;i<=8;i++)assert.equal(observe(i/2),false);
    assert.equal(observe(4.5,change),false);assert.equal(detector.pending,false);
    for(let i=0;i<10;i++)assert.equal(observe(5+i/2),false);
    assert.equal(observe(10),true);
  }
  const {detector,observe}=rig(t);
  observe(0);observe(.5);observe(1,()=>{},false);
  assert.equal(detector.pending,false);assert.equal(observe(1.5),false);assert.equal(detector.evidence.durationS,0);
});

test('cached sensor or encoder frames and feedback gaps cannot count toward a continuous stop',t=>{
  for(const change of [m=>{m.receivedAt=1000;},(_,s)=>{s.feedback.position_at=1000;}]){
    const {detector,observe}=rig(t);
    observe(0);
    for(let i=1;i<=10;i++)assert.equal(observe(i/10,change),false);
    assert.equal(detector.samples.length,1);
    assert.equal(observe(1.1,change),false);assert.equal(detector.pending,false);
  }
  const {detector,observe}=rig(t);
  observe(0);observe(.5);observe(2);
  assert.equal(detector.evidence.durationS,0);
  for(let i=1;i<10;i++)assert.equal(observe(2+i/2),false);
  assert.equal(observe(7),true);
});
