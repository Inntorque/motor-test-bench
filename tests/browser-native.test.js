import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {concat,crc16,hex,pack,view} from '../browser/bytes.ts';
import {objects,packet,values,IsoTp,SampleAssembler} from '../browser/mbp-codec.ts';
import {zqwlFrame,zqwlConfig,ZqwlDecoder,ModbusRtu} from '../browser/transports.ts';
import {HexParser,supplyConfig} from '../browser/rs485.ts';
import {gsBitTiming} from '../browser/hardware.ts';
const fromHex = value => Uint8Array.from(Buffer.from(value.replaceAll(' ',''),'hex'));

test('browser MBP codec matches the frozen SDK golden vectors including signed 64-bit positions',async () => {
  const golden = JSON.parse(await readFile(new URL('../protocol/golden.json',import.meta.url)));
  assert.equal(hex(packet(1,1)),golden.identify_request);
  assert.equal(hex(objects({4098:-1234})),golden.iq_negative_1234ma);
  assert.equal(hex(objects({4106:-1234567890123n})),golden.position_negative_large);
  assert.equal(values(fromHex(golden.position_negative_large))[4106],-1234567890123n);
  assert.equal(hex(objects({8192:fromHex(golden.nested_stream).slice(5)})),golden.nested_stream);
  assert.throws(() => values(fromHex('01000304000000')),/invalid TLV/);
});

test('browser ISO-TP round trips single, fragmented and block-boundary PDUs',async () => {
  for (const size of [1,7,8,62,256,1024]) {
    let received;
    const receiver = new IsoTp(data => {queueMicrotask(() => sender.receive(data)); return Promise.resolve();},data => {received = data;});
    const sender = new IsoTp(data => {queueMicrotask(() => receiver.receive(data)); return Promise.resolve();},() => {});
    const payload = Uint8Array.from({length:size},(_,i) => i & 255);
    await sender.start(payload);
    await new Promise(resolve => setTimeout(resolve,2));
    assert.deepEqual(received,payload,`PDU length ${size}`);
  }
});

test('coherent samples reject missing, repeated and out-of-order fields without inventing values',() => {
  const assembler = new SampleAssembler();
  const field = (seq,id,value) => assembler.receive(pack('HBBI',seq,id,1,value));
  field(1,0,123); field(1,1,0); field(1,2,0xffffffff);
  assert.deepEqual(field(1,254,7).fields,{0:123,1:0,2:0xffffffff});
  field(2,0,0); assert.equal(field(2,254,3),null);
  field(3,0,0); field(3,0,0); assert.equal(assembler.duplicates,1);
  assert.equal(field(3,254,1),null);
});

test('ZQWL uses vendor configuration and single-shot standard CAN framing; receive handles fragmented streams',() => {
  assert.equal(hex(zqwlConfig(0x42,[0,0,0x25])),'493b425700002500000000000000000000000000452e');
  assert.equal(hex(zqwlFrame(0x401,fromHex('010203'))),'5a034000000401010203a5');
  const received = [], decoder = new ZqwlDecoder((id,data) => received.push([id,hex(data)]));
  const heartbeat = new Uint8Array(17); heartbeat.set([0x5a,0xff]);
  const stream = concat(fromHex('9988'),heartbeat,zqwlFrame(0x441,fromHex('0102030405060708')));
  for (const byte of stream) decoder.push(Uint8Array.of(byte));
  assert.deepEqual(received,[[0x441,'0102030405060708']]); assert.equal(decoder.heartbeats,1); assert.equal(decoder.dropped,2);
});

test('RTU checks CRC, address and exact write acknowledgement; OFF remains a coil OFF',async () => {
  let receive,request,badCrc = false;
  const io = {subscribe:(_,callback) => {receive = callback; return () => {};},call:async(method,args) => {
    if (method !== 'serial-write') return;
    request = args.data;
    const response = request[1] === 3 ? fromHex('01030400010002') : request.slice(0,6);
    const data = concat(response,crc16(response));
    if (badCrc) data[data.length - 1] ^= 1;
    queueMicrotask(() => receive(data));
  }};
  const transport = new ModbusRtu(io); await transport.open({device:'test',address:1,baud:9600,stopbits:1});
  assert.deepEqual(await transport.exchange(3,100,2),[1,2]);
  await transport.exchange(5,133,1,[false]); assert.equal(hex(request),'010500850000dc23');
  badCrc = true; await assert.rejects(transport.exchange(3,100,2),/CRC/);
});

test('browser HEX sensor preserves every signed sample and locks CRC only after a valid streak',() => {
  const parser = new HexParser('hex6');
  const data = fromHex('04d289c4'), frame = concat(data,crc16(data));
  const decoded = [];
  for (const byte of concat(fromHex('6e6f697365'),frame,frame,frame)) decoded.push(...parser.feed(Uint8Array.of(byte)));
  assert.deepEqual(decoded.map(row => row.slice(0,2)),[[-1234,2500],[-1234,2500],[-1234,2500]]);
  assert.equal(parser.profile,'modbus-ffff'); assert.equal(parser.order,'low-first');
  const zeros = new HexParser('hex6'); assert.deepEqual(zeros.feed(new Uint8Array(60)),[]);
  const bulk = new HexParser('hex6'); assert.equal(bulk.feed(concat(...Array.from({length:10000},() => frame))).length,10000);
});

test('RU motor and SSJ brake scaling retain their existing independent settings',() => {
  assert.equal(supplyConfig('motor-supply',{device:'test'}).maxCurrent,20);
  assert.equal(supplyConfig('brake-supply',{device:'test'}).currentDecimals,3);
  assert.throws(() => supplyConfig('motor-supply',{device:'test',protocol:'ssj'}),/万瑞达/);
});

test('gs_usb selects exact 500 kbit/s using the actual adapter clock and timing bounds',() => {
  const capabilities = pack('IIIIIIIIII',0,48000000,1,16,1,8,4,1,1024,1);
  const timing = view(gsBitTiming(capabilities,500000));
  const total = 1 + timing.getUint32(0,true) + timing.getUint32(4,true) + timing.getUint32(8,true);
  assert.equal(48000000 / timing.getUint32(16,true) / total,500000);
});
