import {VirtualBoard} from './fixtures/virtual-mbp.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import {MotorDevice} from '../browser/motor.ts';
import {CAN,OP,O,schema,IsoTp,packet,unpack,objects,values,tlv} from '../browser/mbp-codec.ts';
import {concat,crc32,pack,view,sleep,hex} from '../browser/bytes.ts';
import {zqwlFrame} from '../browser/transports.ts';

test('an unpowered drive releases its CAN port and can reconnect once powered',async t => {
  const board = new VirtualBoard(), io = board.call.bind(board);
  let powered = false, open = false;
  board.call = async (method,args) => {
    if (method === 'serial-open') {assert.equal(open,false);open = true;}
    if (method === 'serial-close') {assert.equal(open,true);open = false;}
    if (method === 'serial-write' && args.data[0] === 0x5a && !powered) return;
    return io(method,args);
  };
  const device = new MotorDevice(board);
  t.after(() => device.close());
  const config = {device:'virtual-test-only',node:1,interface:'zqwl',baud:500000};
  await assert.rejects(device.connect(config),/节点 1 未回应 MBP\/1 握手/);
  assert.equal(open,false);
  assert.equal(board.listeners.size,0);
  assert.equal(device.pending.size,0);
  powered = true;
  const timer = setInterval(() => board.publish(),50);
  t.after(() => clearInterval(timer));
  const status = await device.connect(config);
  assert.equal(status.motor.output_on,false);
  assert.deepEqual(board.commands.map(({opcode}) => opcode),[OP.identify,OP.read_state]);
});

test('browser USB-CAN -> ISO-TP -> MBP client identifies, prepares, starts maximum Iq, reads frozen data and confirms STOP',async t => {
  const board = new VirtualBoard(), events = [], device = new MotorDevice(board,(kind,data) => events.push([kind,data]));
  t.after(async () => {await device.close();clearInterval(board.timer);});
  board.timer = setInterval(() => board.publish(),50);
  const initial = await device.connect({device:'virtual-test-only',node:1,interface:'zqwl',baud:500000});
  assert.equal(initial.deviceUid,'000102030405060708090a0b0c0d0e0f');assert.equal(initial.bootId,'1234605616436508552');assert.equal(initial.capabilities.maximumOutput,true);
  assert.equal(initial.feedback.positionMicroturns,'-1234567890123');assert.equal(initial.motor.output_on,false);
  const prepared = await device.prepare({[O.run_mode]:5,[O.direction]:1,[O.iq_target]:0,[O.phase_run_limit]:0,[O.bus_current_budget]:20000,[O.bus_voltage_basis]:24000,[O.negative_id_min]:0,[O.speed_soft_limit]:0});
  assert.equal(prepared,88);await device.start(prepared);
  const active = device.snapshot();assert.equal(active.motor.output_on,true);assert.equal(active.feedback.iq_reference_a,27);assert.equal(active.feedback.iq_a,26.8);assert.equal(active.feedback.id_a,-.4);assert.equal(active.ntc.temp_c,25);
  const claim = board.commands.find(request => request.opcode === OP.claim_control);assert.equal(view(claim.body).getBigUint64(0,true),board.boot);assert.equal(view(claim.body).getUint32(16,true),1);
  const frozen = await device.readFrozen();assert.deepEqual(frozen.data,board.trace);
  const stopped = await device.stop();assert.equal(stopped.deviceDisabled,true);assert.equal(device.snapshot().motor.output_on,false);assert.equal(device.session,0n);assert.equal(device.pending.size,0);
  assert.equal(events.some(([kind]) => kind === 'motor_protocol_rejected'),false);
});
