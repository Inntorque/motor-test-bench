import test from 'node:test';
import assert from 'node:assert/strict';
import {TORQUE_SWEEP_DEFAULTS,torqueSweepError,torqueSweepRequest} from '../web/src/torqueSweep.js';
import {LoadTrial} from '../web/src/loadTrial.js';
test('safe defaults and torque units, reject blanks and unmeasured targets',()=>{
  assert.equal(torqueSweepError(TORQUE_SWEEP_DEFAULTS),'');
  const request=torqueSweepRequest(TORQUE_SWEEP_DEFAULTS);
  assert.equal(request.motorMode,'maximum_output');assert.equal(request.negativeIdMinA,0);assert.equal(request.maxPhaseA,0);
  assert.equal(torqueSweepRequest({...TORQUE_SWEEP_DEFAULTS,motorMode:'full_current'}).motorMode,'full_current');
  assert.equal(request.mode,'torque_sweep');assert.equal(request.brake.startNm,0);
  assert.equal(torqueSweepError({...TORQUE_SWEEP_DEFAULTS,startNm:'0'},{minTargetNm:0}),'');
  assert.ok(torqueSweepError({...TORQUE_SWEEP_DEFAULTS,startNm:'-0.1'}));
  assert.equal('rampS' in request,false);assert.equal('durationS' in request,false);
  assert.deepEqual(Object.keys(TORQUE_SWEEP_DEFAULTS),['startNm','stepNm','intervalS']);
  assert.equal('currentA' in request.brake,false);
  assert.equal(request.brake.maxNm,99.9);
  for(const override of [{startNm:''},{startNm:'100'},{intervalS:'Infinity'},{stepNm:'.001'}])assert.ok(torqueSweepError({...TORQUE_SWEEP_DEFAULTS,...override}));
  // Ignore obsolete saved motor settings; they cannot alter the new request.
  assert.deepEqual(torqueSweepRequest({...TORQUE_SWEEP_DEFAULTS,rampS:'5',durationS:'10'}),request);
});
test('load button discards earlier spinup from characteristic curve; frozen chart keeps exact cutoff',()=>{
  const trace=new LoadTrial({id:'a',running:true,startedAt:1000});
  const point=t=>({t,receivedAt:new Date(t*1000).toISOString(),torque:1,speed:3});
  trace.append([point(1000),point(1001),point(1002),point(1003)]);
  trace.update({id:'a',running:true,startedAt:1000,curveStartedAt:1002});
  assert.deepEqual(trace.snapshot().map(p=>p.t),[1002,1003]);
  trace.update({id:'a',running:false,startedAt:1000,curveStartedAt:1002,measurementEndedAt:1002.5});
  trace.append([point(1004)]);
  assert.deepEqual(trace.snapshot().map(p=>p.t),[1002]);
});
