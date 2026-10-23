import test from 'node:test';
import assert from 'node:assert/strict';
import {supplyMeasurements, supplyPoint, supplyReadbackPoint, supplyOperatingMode} from '../web/src/supplyPolicy.js';

test('actual CC/CV follows SSJ status bits and never claims constant current while off or disconnected', () => {
  const device=status=>({connected:true,config:{protocol:'ssj'},snapshot:{status}});
  assert.equal(supplyOperatingMode(device(0xB0)),'cc');
  assert.equal(supplyOperatingMode(device(0xD0)),'cv');
  assert.equal(supplyOperatingMode(device(0x30)),'off');
  assert.equal(supplyOperatingMode(device(0xF0)),'unknown');
  assert.equal(supplyOperatingMode({...device(0xB0),connected:false}),'unknown');
  assert.equal(supplyOperatingMode({connected:true,config:{protocol:'ru'},snapshot:{status:128,ruStatus:{cc:true,cv:false}}}),'cc');
});

test('motor supply uses the fixed panel 24 V and 20 A preset regardless of old presets', () => {
  assert.deepEqual(supplyPoint('motor-supply', {voltage:'48',current:'10'}), {voltage:'24',current:'20'});
  assert.deepEqual(supplyReadbackPoint('motor-supply', {}, [1,2399,5013], {currentDecimals:2}), {voltage:'24',current:'20'});
});

test('brake current initializes from actual settings and keeps an unapplied draft on polling', () => {
  const initial=supplyReadbackPoint('brake-supply', {current:''}, [1,2400,500], {currentDecimals:2});
  assert.deepEqual(initial, {voltage:'24',current:'5.00'});
  assert.deepEqual(supplyReadbackPoint('brake-supply', {...initial,current:'1.23'}, [1,2400,500], {currentDecimals:2}), {voltage:'24',current:'1.23'});
  assert.deepEqual(supplyReadbackPoint('brake-supply', {current:'1.234'}, [1,2400,123], {currentDecimals:2}, true), {voltage:'24',current:'1.23'});
});

test('motor measured raw 600 is 0.600 A; calculated power replaces unverified device power',()=>{
  const device={connected:true,config:{voltageDecimals:2,currentDecimals:2,measuredCurrentDecimals:3},
    snapshot:{status:16,measured:[2400,600,144],calculatedPowerW:14.4}};
  const v=supplyMeasurements(device);
  assert.equal(v.current,0.6);assert.equal(v.power,14.4);assert.equal(v.currentDecimals,3);
  assert.deepEqual(supplyReadbackPoint('motor-supply',{},[1,2400,2000],device.config),{voltage:'24',current:'20'});
  assert.equal(supplyMeasurements({connected:false}),null);
});
