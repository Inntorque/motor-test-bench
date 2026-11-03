import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JLink} from '../gateway/ui/bootloader/jlink.js';
import {Swd,frames,decode,put,request} from '../gateway/ui/bootloader/swd.js';
import {BASE,BOOT_SIZE,FLASH_SIZE,Bootloader,identify,identity,replacement,validateImage,sha256} from '../gateway/ui/bootloader/n32.js';
import {VirtualJLink} from './fixtures/jlink.js';
const firmware = new URL('../gateway/ui/bootloader/public/firmware/',import.meta.url);
const image = new Uint8Array(await readFile(new URL('boot.bin',firmware))),manifest = JSON.parse(await readFile(new URL('manifest.json',firmware),'utf8'));
async function target() {const device = new VirtualJLink(),probe = new JLink(device);await probe.open();const core = new Swd(probe);await core.init();return {device,probe,core};}

test('published Bootloader has exact digest, vectors and bounded replacement',async () => {
  await validateImage(image,manifest);
  assert.equal(await sha256(await readFile(new URL('boot.hex',firmware))),manifest.hexSha256);
  const before = new Uint8Array(FLASH_SIZE).fill(0x55),{expected,pages} = replacement(before,image);
  assert.deepEqual(expected.subarray(image.length),before.subarray(image.length));
  assert.deepEqual(expected.subarray(BOOT_SIZE),before.subarray(BOOT_SIZE));
  assert.equal(pages.length,6);assert.ok(pages.every(page => page >= BASE && page < BASE + BOOT_SIZE));
  assert.throws(() => replacement(before.subarray(0,-1),image),/不完整/);
  assert.throws(() => replacement(before,new Uint8Array(BOOT_SIZE + 1)),/越界/);
  const bad = image.slice();bad[100] ^= 1;await assert.rejects(validateImage(bad,manifest),/SHA/);
  for (const field of ['base','bootSize','flashSize','pageSize','stack']) await assert.rejects(validateImage(image,{...manifest,[field]:0}),/不符/);
});

test('N32 DBG_ID model and capacity fields reject other N32/STM32 variants',() => {
  assert.equal(identity(0x410fc241,0x80584501).model,'N32G455');
  for (const id of [0x80284501,0x80784501,0x80544501,0x40584501,0x10016413]) assert.throws(() => identity(0x410fc241,id),/不符/);
});

test('SWD read header, released turnaround, ACK and parity use fixed wire vectors',() => {
  assert.equal(request(0,1,0),0xa5);assert.equal(request(1,1,12),0x9f);
  const ops = [{ap:0,read:1,address:0}],packet = frames(ops);
  assert.equal(packet.data[0],0xa5);assert.equal(packet.count,54);
  assert.equal((packet.direction[1] & 1),0);assert.equal((packet.direction[5] >> 5) & 1,0);
  const response = new Uint8Array(7);put(response,9,1);put(response,12,1);put(response,44,1);
  assert.deepEqual(decode(response,ops),[1]);put(response,13,1);assert.throws(() => decode(response,ops),/奇偶/);
  assert.throws(() => decode(new Uint8Array(7),ops),/ACK/);
  const fault = new Uint8Array(7);put(fault,11,1);
  assert.throws(() => decode(fault,ops),/ACK=4 \(FAULT\).*DP 读寄存器 0x00/);
});

test('J-Link consumes full version/interface/status replies and leaves core running on identify',async () => {
  const {device,probe,core} = await target();
  assert.match(probe.version,/J-Link V9/);assert.equal(probe.millivolts,3287);assert.equal((await identify(core)).model,'N32G455');
  assert.equal(device.responses.length,0);assert.equal(device.writes.length,0);assert.equal(device.halted,false);assert.equal(device.resets,0);
  device.swdStatus = 6;await assert.rejects(core.read32(0xe000ed00),/操作失败/);await probe.close();assert.equal(device.opened,false);
});

test('MEM-AP block reads cross 1 KiB TAR boundaries without duplicated or skipped words',async () => {
  const {device,probe,core} = await target();
  assert.deepEqual(await core.read(BASE + 1016,40),device.flash.slice(1016,1056));await probe.close();
});

test('a failed second open never closes another owner’s J-Link session',async () => {
  const {device,probe} = await target(),other = new JLink(device);
  await assert.rejects(other.open(),/其他程序/);await other.close();
  assert.equal(device.opened,true);assert.equal(device.claimed,true);await probe.close();
});

test('full WebUSB/SWD flash saves backup first, preserves non-Boot bytes and verifies before reset',async () => {
  const {device,probe,core} = await target(),before = device.flash.slice(),stages = [];
  const engine = new Bootloader(core);
  const record = await engine.flash(image,manifest,async record => {
    stages.push(record.stage);assert.deepEqual(record.before,before);
    if (record.stage === 'backup' || record.stage === 'programming') assert.equal(device.erased.length,0);
    if (record.stage === 'verified') assert.equal(device.resets,0);
  });
  assert.deepEqual(stages,['backup','programming','verified','complete']);assert.equal(record.verified,true);
  assert.deepEqual(device.flash.slice(0,image.length),image);assert.deepEqual(device.flash.slice(image.length),before.slice(image.length));
  assert.equal(device.erased.length,6);assert.equal(device.resets,1);assert.equal(device.debug,0x10);await probe.close();
});

test('failed backup persistence prevents any Flash unlock/erase/program/reset',async () => {
  for (const failure of [0,1]) {
    const {device,probe,core} = await target(),engine = new Bootloader(core);let saves = 0;
    await assert.rejects(engine.flash(image,manifest,async () => {if (saves++ === failure) throw new Error('storage failed');}),/storage failed/);
    assert.equal(engine.started,false);assert.equal(device.erased.length,0);assert.equal(device.resets,0);
    assert.equal(device.writes.some(([address]) => address === 0x40022004),false);await probe.close();
  }
});

test('readback mismatch never resets or reports verified success and retains original backup',async () => {
  const {device,probe,core} = await target(),before = device.flash.slice(),engine = new Bootloader(core);let saved;
  device.corruptReadback = true;
  await assert.rejects(engine.flash(image,manifest,async record => {saved = structuredClone(record);}),/回读/);
  assert.equal(engine.started,true);assert.equal(engine.verified,false);assert.equal(device.resets,0);assert.equal(device.halted,true);
  assert.equal(saved.stage,'failed');assert.deepEqual(saved.before,before);await probe.close();
});

test('wrong MCU stops before halt or any Flash access',async () => {
  const {device,probe,core} = await target();device.badId = true;
  await assert.rejects(new Bootloader(core).flash(image,manifest,async () => {}),/不符/);
  assert.deepEqual(device.writes,[]);assert.equal(device.erased.length,0);await probe.close();
});
