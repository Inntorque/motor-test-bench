import test from 'node:test';
import assert from 'node:assert/strict';
import {CanTransport,SlcanDecoder,slcanFrame,canInterface} from '../browser/transports.ts';
const encode = value => new TextEncoder().encode(value);

test('CANable2 CDC selects SLCAN independently from gs_usb and ZQWL',() => {
  assert.equal(canInterface({kind:'serial',vid:0x16d0,pid:0x117e}),'slcan');
  assert.equal(canInterface({kind:'serial',vid:0x3562,pid:1}),'zqwl');
  assert.equal(canInterface({kind:'usb',vid:0x1d50,pid:0x606f}),'gs_usb');
  assert.equal(canInterface({kind:'serial',vid:0x1a86,pid:0x7523}),null);
});

test('SLCAN Classic CAN frames retain exact IDs and payload through fragmented USB reads',() => {
  const frames = [],decoder = new SlcanDecoder((id,data) => frames.push({id,data:[...data]}));
  const expected = 't61080900000000000000\r';assert.equal(new TextDecoder().decode(slcanFrame(0x610,Uint8Array.of(9,0,0,0,0,0,0,0))),expected);
  for (const char of '16e7497-dirty github.com/normaldotcom/canable2.git\rt590883000000003D0A00\r\nt18180000000000000000FFFF\r') decoder.push(encode(char));
  assert.match(decoder.version,/canable2/);assert.deepEqual(frames,[{id:0x590,data:[0x83,0,0,0,0,0x3d,0x0a,0]},{id:0x181,data:[0,0,0,0,0,0,0,0]}]);
  decoder.push(encode('T0000059080000000000000000\rr5908\rtFFF100\rt590900\rt5902FF\rt5901xx\r'));
  assert.equal(frames.length,2);assert.equal(decoder.dropped,4);
  assert.throws(() => slcanFrame(0x800,new Uint8Array()),/standard frame/);
});

test('CANable2 opens at 500k without waiting for nonexistent command ACKs and never sends motor frames during adapter setup',async () => {
  const writes = [];let callback,closes = 0;
  const io = {subscribe(_id,cb) {callback = cb;return () => {};},async call(method,args) {
    if (method === 'serial-open') {assert.equal(args.baudRate,115200);return;}
    if (method === 'serial-close') {closes++;return;}
    const text = new TextDecoder().decode(args.data);writes.push(text);
    if (text === 'V\r') queueMicrotask(() => callback(encode('16e7497-dirty github.com/normaldotcom/canable2.git\r')));
  }};
  const received = [],bus = new CanTransport(io,(id,data) => received.push([id,[...data]]));
  await bus.open({device:'canable',interface:'slcan',baud:500000});
  assert.deepEqual(writes,['V\r','C\r','S6\r','M0\r','O\r']);
  await bus.send(0x600,Uint8Array.of(1,0,0,0,0,0,0,0));assert.equal(writes.at(-1),'t60080100000000000000\r');
  callback(encode('t580883000000003D0A00\r'));assert.deepEqual(received,[[0x580,[0x83,0,0,0,0,0x3d,0x0a,0]]]);
  await bus.close();assert.equal(writes.at(-1),'C\r');assert.equal(closes,1);
  const occupied = new CanTransport({subscribe:() => () => {},call:async method => {if (method === 'serial-open') throw new Error('occupied');if (method === 'serial-close') closes++;}},() => {});
  await assert.rejects(occupied.open({device:'other',interface:'slcan',baud:500000}),/occupied/);await occupied.close();assert.equal(closes,1);
});
