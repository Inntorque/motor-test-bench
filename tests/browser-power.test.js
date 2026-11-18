import test from 'node:test';
import assert from 'node:assert/strict';
import {Bench} from '../browser/bench.ts';
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
  await assert.rejects(channel.output(true,{voltage:24,current:5}),/读回 24 V \/ 20 A.*24 V \/ 5 A/);
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


test('one-key scan identifies shuffled RS485 ports, refuses ambiguous roles and reconnects during stop recovery',async t => {
  const ru = new SupplyFixture(), ssj = new SupplyFixture('ssj'), duplicate = new SupplyFixture();
  const devices = {brake:ssj,motor:ru,duplicate};
  const opened = new Map(), stream = new Set(), port = name => ({port:name,description:name,kind:'serial',vid:0x345f,pid:0x3020});
  const frame = Uint8Array.of(0,8,0x80,0);
  const connectedPorts = [port('brake'),port('stream'),port('motor')];
  let ports = connectedPorts, stopping = false;
  const io = {
    subscribe(name,callback) {
      if (name !== 'stream') return devices[name].subscribe(name,callback);
      stream.add(callback);return () => stream.delete(callback);
    },
    async call(method,args) {
      if (method === 'ports') return ports;
      if (method === 'can-config') return {};
      const name = args.device;
      if (method === 'serial-open') {
        assert.equal(opened.has(name),false);
        opened.set(name,args.baudRate);
      } else if (method === 'serial-close') {
        assert.equal(opened.delete(name),true);
      } else if (method === 'serial-write') {
        if (!stopping) assert.ok(args.data[1] >= 1 && args.data[1] <= 4);
        if (name !== 'stream' && opened.get(name) === 9600) return devices[name].call(method,args);
        if (name === 'stream') throw new Error('streaming sensor does not answer queries');
      }
    },
  };
  const timer = setInterval(() => {
    if (opened.get('stream') === 115200) for (const callback of stream) callback(concat(frame,crc16(frame)));
  },20);
  t.after(() => clearInterval(timer));
  const scan = async journal => {
    const values = new Map([['output-journal',journal]]);
    const store = {get:async key => values.get(key),put:async (key,value) => {values.set(key,value);},entries:async () => []};
    const bench = new Bench(io,store,() => {});
    t.after(() => clearInterval(bench.sensor.frameTimer));
    await bench.initialize();
    bench.timers.forEach(clearInterval);
    await bench.connectAll({'torque-sensor':{protocol:'modbus',baud:19200,stopbits:2}});
    while (bench.connection.running) await new Promise(resolve => setTimeout(resolve,20));
    return bench;
  };
  const connected = await scan();
  assert.equal(connected.channels['motor-supply'].status().config.device,'motor');
  assert.equal(connected.channels['brake-supply'].status().config.device,'brake');
  assert.equal(connected.sensor.status().config.protocol,'hex6');
  assert.equal(connected.sensor.status().config.device,'stream');
  await connected.sensor.disconnect();
  for (const channel of Object.values(connected.channels)) await channel.disconnect();
  ports = [port('duplicate'),...ports];
  const ambiguous = await scan();
  assert.equal(ambiguous.channels['motor-supply'].status().connected,false);
  assert.ok(ambiguous.connection.report.some(line => /电机电源.*多个匹配/.test(line)));
  assert.equal(ambiguous.channels['brake-supply'].status().connected,true);
  assert.deepEqual([ru.writes,ssj.writes,duplicate.writes],[[],[],[]]);
  await ambiguous.sensor.disconnect();
  for (const channel of Object.values(ambiguous.channels)) await channel.disconnect();
  ports = connectedPorts;
  const journal = {dirty:true,expected:['motor-supply','brake-supply'],motorMayBeOn:false};
  ru.coils.set(133,true);
  ssj.coils.set(133,true);
  ssj.settings = [2400,0];
  const recovery = await scan(journal);
  assert.equal(recovery.channels['motor-supply'].status().config.device,'motor');
  assert.equal(recovery.channels['brake-supply'].status().config.device,'brake');
  assert.equal(recovery.connection.report.filter(line => line.includes('待停止确认')).length,0);
  assert.deepEqual([ru.writes,ssj.writes],[[],[]]);
  assert.equal(recovery.safety().blocked,true);
  assert.deepEqual(await recovery.store.get('output-journal'),journal);

  stopping = true;
  recovery.beginStop('reconcile');
  await recovery.stopTask;
  assert.equal(recovery.safety().blocked,false,recovery.safety().errors.join("；"));
  assert.equal((await recovery.store.get('output-journal')).dirty,false);
  assert.equal(recovery.channels['motor-supply'].snapshot.status & 128,0);
  assert.equal(recovery.channels['brake-supply'].snapshot.status & 128,0);
  assert.deepEqual(ru.writes,[{func:5,register:133,value:0}]);
  assert.deepEqual(ssj.writes,[{func:16,register:2016,words:[0]}]);
  await recovery.sensor.disconnect();
  for (const channel of Object.values(recovery.channels)) await channel.disconnect();
  assert.equal(opened.size,0);
});

test('delayed output feedback is confirmed by reads without repeating the ON command',async()=>{
  const io=new SupplyFixture(),channel=new PowerChannel('motor-supply',io);
  await channel.connect({device:'fixture',protocol:'ru'});
  const read=channel.readRaw.bind(channel);let delayed=0;
  channel.readRaw=async()=>{const snapshot=await read();if(io.writes.length&&delayed++<2)return {...snapshot,status:0};return snapshot;};
  await channel.output(true,{voltage:24,current:20});
  assert.equal(channel.snapshot.status,128);assert.equal(io.writes.length,1);assert.equal(delayed,3);
  channel.error='previous read timeout';await channel.read();assert.equal(channel.status().error,'');
  await channel.output(false);await channel.disconnect();
});

test('a persistent output mismatch remains an error after bounded read-only confirmation',async()=>{
  const io=new SupplyFixture(),channel=new PowerChannel('motor-supply',io);
  await channel.connect({device:'fixture',protocol:'ru'});
  const read=channel.readRaw.bind(channel);
  channel.readRaw=async()=>({...await read(),status:0});
  await assert.rejects(channel.output(true,{voltage:24,current:20}),/输出状态读回与指令不一致/);
  assert.equal(io.writes.length,1);
  channel.readRaw=read;await channel.output(false);await channel.disconnect();
});

test('STOP interrupts output-feedback waiting and never resends ON',async()=>{
  const io=new SupplyFixture(),channel=new PowerChannel('motor-supply',io);
  await channel.connect({device:'fixture',protocol:'ru'});
  const read=channel.readRaw.bind(channel);let release,entered;
  const started=new Promise(resolve=>{entered=resolve;});let blocked=false;
  channel.readRaw=async()=>{const snapshot=await read();if(io.writes.length&&!blocked){blocked=true;entered();await new Promise(resolve=>{release=resolve;});return {...snapshot,status:0};}return snapshot;};
  const enabling=channel.output(true,{voltage:24,current:20});await started;
  const disabling=channel.output(false);release();await assert.rejects(enabling,/停止取消/);await disabling;
  assert.deepEqual(io.writes.map(row=>row.value),[0xff00,0]);assert.equal(channel.outputUncertain,false);await channel.disconnect();
});
