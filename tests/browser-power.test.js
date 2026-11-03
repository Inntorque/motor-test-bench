import test from 'node:test';
import assert from 'node:assert/strict';
import {PowerChannel} from '../browser/rs485.ts';
import {concat,crc16,view} from '../browser/bytes.ts';
class SupplyFixture {
  writes = []; coils = new Map([[133,false],[136,true],[137,true]]); settings = [2400,2000];
  constructor(protocol = 'ru') {this.protocol = protocol;this.listeners = new Set();}
  subscribe(_,callback) {this.listeners.add(callback);return () => this.listeners.delete(callback);}
  async call(method,args) {
    if (method === 'serial-open') {this.options = args;return;}
    if (method === 'serial-close') return;
    const request = args.data, source = view(request), func = request[1], register = source.getUint16(2,false), count = source.getUint16(4,false);
    let body;
    if (func === 5) {this.writes.push({func,register,value:count});this.coils.set(register,count === 0xff00);body = request.slice(0,6);}
    else if (func === 16) {const words = Array.from({length:count},(_,i) => source.getUint16(7 + i * 2,false));this.writes.push({func,register,words});if ([149,2001].includes(register)) this.settings = words;if (register === 2016) this.coils.set(133,!!words[0]);body = request.slice(0,6);}
    else if (func === 1 || func === 2) {
      const bits = func === 1 ? Array.from({length:count},(_,i) => this.coils.get(register + i) || false) : register === 116 ? [false,true] : Array(count).fill(false);
      const data = new Uint8Array(Math.ceil(count / 8));bits.forEach((bit,i) => {if (bit) data[i >> 3] |= 1 << i % 8;});body = concat(Uint8Array.of(1,func,data.length),data);
    } else {
      const mapping = this.protocol === 'ru' ? {106:[2,2],103:[3600,6000],100:[this.coils.get(133) ? 2400 : 0,0],149:this.settings,157:[3600,6000]}
        : {1000:[this.coils.get(133) ? 2400 : 0,0,0],1007:[this.coils.get(133) ? 128 : 0],2000:[1,...this.settings,3600,2300,0],2001:this.settings};
      const words = mapping[register];if (!words || words.length < count) throw new Error(`fixture register not supported: ${register}/${count}`);
      body = Uint8Array.of(1,func,count * 2,...words.slice(0,count).flatMap(word => [word >> 8,word & 255]));
      if (this.block && register === 100) {this.block = false;this.entered();await this.release;}
    }
    const reply = concat(body,crc16(body));queueMicrotask(() => {for (const callback of this.listeners) callback(reply);});
  }
}

test('RU browser connect is read-only and ON preserves the confirmed 24V/20A panel preset',async () => {
  const io = new SupplyFixture(), channel = new PowerChannel('motor-supply',io);
  const connected = await channel.connect({device:'fixture',protocol:'ru'});
  assert.equal(connected.connected,true);assert.equal(connected.config.currentDecimals,2);assert.equal(connected.config.ratedCurrent,60);assert.deepEqual(io.writes,[]);
  const result = await channel.output(true,{voltage:24,current:20});assert.equal(result.status,128);
  assert.deepEqual(io.writes,[{func:5,register:133,value:0xff00}],'ON must not overwrite the panel current register');
  await channel.output(false);assert.equal(channel.snapshot.status,0);assert.equal(channel.outputUncertain,false);
  await assert.rejects(channel.output(true,{voltage:24,current:5}),/预设不匹配/);
  assert.equal(io.writes.filter(write => write.func === 16).length,0);await channel.disconnect();
});

test('STOP during RU preflight never permits the delayed ON coil write',async () => {
  const io = new SupplyFixture(), channel = new PowerChannel('motor-supply',io);await channel.connect({device:'fixture',protocol:'ru'});
  let release,entered;const started = new Promise(resolve => {entered = resolve;});io.entered = entered;io.release = new Promise(resolve => {release = resolve;});io.block = true;
  const on = channel.output(true,{voltage:24,current:20});await started;
  const off = channel.output(false);release();await assert.rejects(on,/停止取消/);await off;
  assert.equal(io.writes.some(write => write.func === 5 && write.value === 0xff00),false);assert.equal(channel.snapshot.status,0);await channel.disconnect();
});

test('SSJ brake writes retain 0.001A units and independent output register 2016',async () => {
  const io = new SupplyFixture('ssj'), channel = new PowerChannel('brake-supply',io);io.settings = [2400,0];
  await channel.connect({device:'fixture',protocol:'ssj',currentDecimals:3,voltageDecimals:2});await channel.setValues({voltage:24,current:1.999});
  assert.deepEqual(io.writes[0],{func:16,register:2001,words:[2400,1999]});
  await channel.output(true,{voltage:24,current:1.999});assert.equal(io.writes.some(write => write.register === 2016 && write.words[0] === 65535),true);
  await channel.output(false);assert.equal(channel.snapshot.status,0);await channel.disconnect();
});

test('a mistaken duplicate port selection never closes the device already owned by another channel',async () => {
  const {CanTransport,ModbusRtu} = await import('../browser/transports.ts');
  const {TorqueSensor} = await import('../browser/rs485.ts');let closes = 0;
  const io = {subscribe:() => () => {},call:async method => {if (method === 'serial-open') throw new Error('already in use');if (method === 'serial-close') closes++;}};
  const can = new CanTransport(io,() => {});await assert.rejects(can.open({device:'owned'}),/already in use/);
  const modbus = new ModbusRtu(io);await assert.rejects(modbus.open({device:'owned'}),/already in use/);await modbus.close();
  const power = new PowerChannel('motor-supply',io);await assert.rejects(power.connect({device:'owned'}),/already in use/);
  const sensor = new TorqueSensor(io);await assert.rejects(sensor.connect({device:'owned',protocol:'hex6'}),/already in use/);
  assert.equal(closes,0);
});
