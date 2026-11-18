import test from 'node:test';
import assert from 'node:assert/strict';
import { CharacteristicWindow, characteristicAutoUpper } from '../web/src/characteristicData.ts';
import {characteristicElectrical} from '../web/src/characteristicData.ts';
import {measuredStopPoint} from '../web/src/characteristicData.ts';

test('measured stop endpoint survives bin averaging without inventing a zero',()=>{
  const run={running:false,measurementEndedAt:1002,stallEndpoint:{receivedAt:1002,torque:2.46,speed:0}};
  assert.deepEqual(measuredStopPoint(run),{torque:2.46,speed:0,power:0,
    voltage:null,current:null,inputPower:null,efficiency:null});
  const powered={...run,stallEndpoint:{...run.stallEndpoint,motorSupplyReceivedAt:1002,
    motorSupplyVoltage:24,motorSupplyCurrent:2}};
  assert.equal(measuredStopPoint(powered).efficiency,0);
  assert.equal(measuredStopPoint(powered).current,2);
  assert.equal(measuredStopPoint({...run,running:true}),null);
  assert.equal(measuredStopPoint({...run,stallEndpoint:{...run.stallEndpoint,speed:120}}),null);
  assert.equal(measuredStopPoint({...run,measurementEndedAt:1001}),null);
  assert.equal(measuredStopPoint({running:false}),null);
});

test('six curves use fresh measured DC bus current, not target Iq or nominal 10 A',()=>{
  const sample={t:0,torque:1,speed:10,shaftPowerKw:.005,current:5,
    receivedAt:'2026-10-20T06:00:00.000Z',motorSupplyReceivedAt:Date.parse('2026-10-20T06:00:00Z')/1000,
    motorSupplyVoltage:23.8,motorSupplyCurrent:1.5,motorSupplyPower:36};
  const electric=characteristicElectrical(sample,5);
  assert.equal(electric.voltage,23.8);assert.equal(electric.current,1.5);
  assert.ok(Math.abs(electric.inputPower-35.7)<1e-9);
  assert.ok(Math.abs(electric.efficiency-5/35.7*100)<1e-9);
  const row=new CharacteristicWindow().update([sample],30)[0];
  assert.equal(row.sums[2],1.5);assert.equal(row.sums[4],23.8);assert.equal(row.sums[5],electric.inputPower);
  const stale={...sample,motorSupplyReceivedAt:sample.motorSupplyReceivedAt-3};
  assert.deepEqual(characteristicElectrical(stale,5),{voltage:null,current:null,inputPower:null,efficiency:null});
  assert.deepEqual(new CharacteristicWindow().update([stale],30)[0].counts,[1,0,0,1,0,0]);
});

test('zero current keeps zero Pi and no efficiency, low power is not rounded to zero',()=>{
  const sample={receivedAt:'2026-10-20T06:00:00Z',motorSupplyReceivedAt:Date.parse('2026-10-20T06:00:00Z')/1000,motorSupplyVoltage:24,motorSupplyCurrent:0};
  assert.deepEqual(characteristicElectrical(sample,0),{voltage:24,current:0,inputPower:0,efficiency:null});
  const low=characteristicElectrical({...sample,motorSupplyCurrent:.02,motorSupplyPower:0},.12);
  assert.equal(low.inputPower,.48);assert.equal(low.efficiency,25);
});

test('incremental characteristic window matches full-window statistics and supports pause rewind', () => {
  const data = Array.from({length:60000}, (_,i) => ({t:i/1000, torque:i%100/100,
    torqueDecimals:2, speed:10, current:1, shaftPowerKw:.001, efficiencyPercent:null}));
  const stats = new CharacteristicWindow();
  function check(points, seconds) {
    const rows = stats.update(points, seconds);
    assert.equal(rows.reduce((n,row)=>n+row.count,0), points.filter(p=>p.t>=points.at(-1).t-seconds).length);
    assert.ok(rows.every(row => row.sums[0] === row.count && row.counts[1] === 0));
  }
  check(data,30);
  const more = Array.from({length:30},(_,i)=>({...data[0],t:60+i/1000}));
  check([...data,...more].slice(-60000),30);
  check(data.slice(0,1000),30);
  check(data,60);
  check(data,60);
});

test('linear trend is a straight fit confined to measured coverage', async () => {
  const {linearCharacteristic} = await import('../web/src/characteristicData.ts');
  assert.deepEqual(linearCharacteristic([{torque:1,power:3},{torque:2,power:5},{torque:3,power:7}],'power'),
    [{torque:1,power:3},{torque:3,power:7}]);
  assert.deepEqual(linearCharacteristic([{torque:0,power:3},{torque:0,power:5}],'power'),[]);
  assert.deepEqual(linearCharacteristic([{torque:1,power:null}],'power'),[]);
});

test('adjustable curve fit follows curvature instead of flattening to a line', async () => {
  const {curveCharacteristic} = await import('../web/src/characteristicData.ts');
  const points = Array.from({length:41},(_,i)=>({torque:i/10,power:(i/10)**2}));
  const fitted = curveCharacteristic(points,'power',70);
  assert.equal(fitted.length,96);
  assert.equal(fitted[0].torque,0);
  assert.ok(Math.abs(fitted.at(-1).torque-4)<1e-9);
  const mid = fitted[Math.floor(fitted.length/2)];
  assert.ok(Math.abs(mid.power-mid.torque**2)<0.05);
  const chord = (fitted[0].power+fitted.at(-1).power)/2;
  assert.ok(Math.abs(chord-mid.torque**2)>1);
  const bumped = points.map(p=>({...p,power:p.torque===2?10:p.torque**2}));
  const near = curve => curve.reduce((best,p)=>Math.abs(p.torque-2)<Math.abs(best.torque-2)?p:best);
  assert.ok(near(curveCharacteristic(bumped,'power',100)).power > near(curveCharacteristic(bumped,'power',0)).power);
  assert.deepEqual(curveCharacteristic([{torque:1,power:3},{torque:2,power:5}],'power').map(p=>p.power),[3,5]);
  assert.deepEqual(curveCharacteristic([{torque:1,power:null}],'power'),[]);
});

test('characteristic axes cover measured shaft speed and supply voltage',()=>{
  const points=[{speed:1139.4,voltage:24.03},{speed:932.1,voltage:24.02},{speed:null,voltage:null}];
  assert.equal(characteristicAutoUpper(points,'speed',100,100,1.05),1200);
  assert.equal(characteristicAutoUpper(points,'voltage',24,2),26);
  assert.equal(characteristicAutoUpper([{speed:60}],'speed',100,100,1.05),100);
  assert.equal(characteristicAutoUpper([],'voltage',24,2),24);
});

test('fitted curves retain the confirmed measured stop and do not invent one',async () => {
  const {fitCharacteristic} = await import('../web/src/characteristicData.ts');
  const points=[{torque:0,speed:1200},{torque:1,speed:800},{torque:2,speed:400}];
  const stop={torque:2,speed:0};
  for (const mode of ['linear','curve']) {
    const withStop=fitCharacteristic(points,'speed',mode,60,stop);
    assert.deepEqual(withStop.at(-1),stop);
    assert.ok(withStop.every(p=>p.torque>=0 && p.torque<=2 && p.speed>=0));
    const withoutStop=fitCharacteristic(points,'speed',mode,60,null);
    assert.ok(withoutStop.at(-1).speed>0);
    const internal=fitCharacteristic(points,'speed',mode,60,{torque:1.5,speed:0});
    assert.ok(internal.some(p=>p.torque===1.5 && p.speed===0));
    assert.equal(internal.at(-1).torque,2);
  }
});

test('only the same frozen run final measured zero is anchored independently of bin means',async ()=>{
  const {measuredZeroTail,fitCharacteristic}=await import('../web/src/characteristicData.ts');
  const sample={receivedAt:new Date(1002*1000).toISOString(),torque:1.7,speed:0};
  const run={running:false,startedAt:1000,measurementEndedAt:1002};
  const zero=measuredZeroTail([{...sample,speed:150},sample],run);
  assert.equal(zero.speed,0);assert.equal(zero.torque,1.7);
  for(const mode of ['linear','curve']) assert.equal(fitCharacteristic([{torque:1,speed:400},{torque:1.7,speed:130}],'speed',mode,60,zero).find(p=>p.torque===1.7).speed,0);
  assert.equal(measuredZeroTail([{...sample,speed:null}],run),null);
  assert.equal(measuredZeroTail([sample],{...run,running:true}),null);
  assert.equal(measuredZeroTail([sample],{...run,measurementEndedAt:1001}),null);
  assert.equal(measuredZeroTail([sample],run),null);
  assert.equal(measuredZeroTail([{...sample,speed:150},sample],{...run,measurementEndedAt:1004}),null);
  assert.equal(measuredZeroTail([sample,{...sample,speed:150}],run),null);
});

test('brake stage coordinates retain previous stages when measured torque falls at zero speed',async ()=>{
  const {loadStageSamples,fitCharacteristic}=await import('../web/src/characteristicData.ts');
  const stages=[{targetNm:.4,startedAt:1001},{targetNm:1.6,startedAt:1002}];
  const point=(time,torque,speed)=>({receivedAt:new Date(time*1000).toISOString(),torque,speed});
  const raw=[point(1001.1,.4,900),point(1002.1,1.6,600),point(1002.2,.42,0)];
  const mapped=loadStageSamples(raw,stages,1001);
  assert.deepEqual(mapped.map(row=>[row.torque,row.measuredTorque,row.speed]),[[.4,.4,900],[1.6,1.6,600],[1.6,.42,0]]);
  assert.equal(raw.at(-1).torque,.42);
  assert.equal(fitCharacteristic([{torque:.4,speed:900},{torque:1.6,speed:600}],'speed','curve',60,{torque:1.6,speed:0}).at(-1).speed,0);
  assert.equal(loadStageSamples(raw,[],1001),null);
  assert.deepEqual(loadStageSamples([point(1000.9,.2,900),...raw],stages,1001).map(row=>row.torque),[.4,1.6,1.6]);
});
