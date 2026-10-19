import test from 'node:test';
import assert from 'node:assert/strict';
import {supplyPoint, supplyReadbackPoint, supplyOperatingMode} from '../web/src/supplyPolicy.js';

test('actual CC/CV follows SSJ status bits and never claims constant current while off or disconnected', () => {
  const device=status=>({connected:true,config:{protocol:'ssj'},snapshot:{status}});
  assert.equal(supplyOperatingMode(device(0xB0)),'cc');
  assert.equal(supplyOperatingMode(device(0xD0)),'cv');
  assert.equal(supplyOperatingMode(device(0x30)),'off');
  assert.equal(supplyOperatingMode(device(0xF0)),'unknown');
  assert.equal(supplyOperatingMode({...device(0xB0),connected:false}),'unknown');
  assert.equal(supplyOperatingMode({connected:true,config:{protocol:'ru'},snapshot:{status:128,ruStatus:{cc:true,cv:false}}}),'cc');
});

test('motor supply fixes 24 V and 10 A regardless of old presets', () => {
  assert.deepEqual(supplyPoint('motor-supply', {voltage:'48',current:'5'}), {voltage:'24',current:'10'});
  assert.deepEqual(supplyReadbackPoint('motor-supply', {}, [1,2399,5013], {currentDecimals:2}), {voltage:'24',current:'10'});
});

test('brake current initializes from actual settings and keeps an unapplied draft on polling', () => {
  const initial=supplyReadbackPoint('brake-supply', {current:''}, [1,2400,500], {currentDecimals:2});
  assert.deepEqual(initial, {voltage:'24',current:'5.00'});
  assert.deepEqual(supplyReadbackPoint('brake-supply', {...initial,current:'1.23'}, [1,2400,500], {currentDecimals:2}), {voltage:'24',current:'1.23'});
  assert.deepEqual(supplyReadbackPoint('brake-supply', {current:'1.234'}, [1,2400,123], {currentDecimals:2}, true), {voltage:'24',current:'1.23'});
});
