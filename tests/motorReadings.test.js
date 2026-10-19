import test from 'node:test';
import assert from 'node:assert/strict';
import {motorTargetCurrentA, motorOutputFeedback, motorDeviceState, motorStepInputError} from '../web/src/model.js';

test('step inputs explain missing and invalid values instead of silently blocking the button', () => {
  assert.match(motorStepInputError({}), /电流步长、步进间隔、持续时间/);
  const form={stepA:'0.1',intervalS:'1',durationS:'30'};
  assert.equal(motorStepInputError(form,5),'');
  assert.match(motorStepInputError({...form,stepA:'6'},5), /电流步长/);
  assert.match(motorStepInputError({...form,intervalS:'0'}), /步进间隔/);
  assert.match(motorStepInputError({...form,durationS:''}), /持续时间/);
  assert.match(motorStepInputError({...form,durationS:'256'}), /持续时间/);
});

test('motor state follows fresh device feedback independently of the step task', () => {
  const device={connected:true,run:{running:false},snapshot:{motor:{available:true,age_s:0.1,state:2,output_on:true}}};
  assert.equal(motorDeviceState(device),'运行中 · 输出开启');
  assert.equal(motorDeviceState({...device,connected:false}),'状态未知');
  device.snapshot.motor.age_s=3;
  assert.equal(motorDeviceState(device),'状态未知');
  Object.assign(device.snapshot.motor,{age_s:0,state:0,output_on:false});
  assert.equal(motorDeviceState(device),'已停止 · 输出关闭');
});

test('idle target uses fresh controller readback and never invents a zero from idle run state', () => {
  const device={connected:true, run:{phase:'idle', targetA:0}, snapshot:{receivedAt:100, control:{updated_at:99.9, iq_target_ma:1250}}};
  assert.equal(motorTargetCurrentA(device),1.25);
  assert.equal(motorTargetCurrentA({...device, connected:false}),null);
  assert.equal(motorTargetCurrentA({...device, snapshot:{...device.snapshot, receivedAt:102}}),null);
  assert.equal(motorTargetCurrentA({...device, run:{running:true, targetA:2}}),2);
});

test('encoder output readings use the reference profile ratio and leave stale readings empty', () => {
  const device={connected:true, snapshot:{motorProfile:{nameplate:{reduction_ratio:9.67}},feedback:{position_deg:96.7,speed_rpm:193.4,position_stale:false,stale:false}}};
  const values=motorOutputFeedback(device);
  assert.equal(values.positionDeg,10);
  assert.equal(values.speedRpm,20);
  assert.deepEqual(motorOutputFeedback({...device,connected:false}),{positionDeg:null,speedRpm:null});
  assert.deepEqual(motorOutputFeedback({...device,snapshot:{...device.snapshot,feedback:{...device.snapshot.feedback,position_stale:true}}}),{positionDeg:null,speedRpm:null});
});
