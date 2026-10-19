import test from 'node:test';
import assert from 'node:assert/strict';
import { CharacteristicWindow } from '../web/src/characteristicData.js';

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
  const {linearCharacteristic} = await import('../web/src/characteristicData.js');
  assert.deepEqual(linearCharacteristic([{torque:1,power:3},{torque:2,power:5},{torque:3,power:7}],'power'),
    [{torque:1,power:3},{torque:3,power:7}]);
  assert.deepEqual(linearCharacteristic([{torque:0,power:3},{torque:0,power:5}],'power'),[]);
  assert.deepEqual(linearCharacteristic([{torque:1,power:null}],'power'),[]);
});
