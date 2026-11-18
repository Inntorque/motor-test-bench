import {VirtualBoard} from './fixtures/virtual-mbp.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import {CurrentMotor} from '../browser/control.ts';
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

test('maximum output reads current drive readiness before authorizing a start',async t => {
  const board = new VirtualBoard(), device = new MotorDevice(board), motor = new CurrentMotor(device,{});
  const timer = setInterval(() => board.publish(),50);
  t.after(async () => {clearInterval(timer);await device.close();});
  await motor.connect({device:'virtual-test-only',node:1,interface:'zqwl',baud:500000});

  device.identity[O.readiness_flags] &= ~1;
  device.identity[O.readiness_detail] = 'cached not ready';
  assert.equal(motor.status().snapshot.startBlockReason,'cached not ready');
  await motor.start({mode:'maximum_output',busLimitA:20,busVoltageV:24});
  await device.wait(() => motor.run.phase === 'running',1500,'fixture did not start');
  const opcodes = board.commands.map(command => command.opcode);
  assert.ok(opcodes.includes(OP.read_objects) && opcodes.indexOf(OP.read_objects) < opcodes.indexOf(OP.claim_control));
  await motor.stop();
  await motor.task;

  board.identity[O.readiness_flags] &= ~1;
  board.identity[O.readiness_detail] = 'drive still not ready';
  const before = board.commands.filter(command => command.opcode === OP.prepare_run).length;
  await assert.rejects(motor.start({mode:'maximum_output'}),/drive still not ready/);
  assert.equal(board.commands.filter(command => command.opcode === OP.prepare_run).length,before);
});

test('ZQWL uses normal CAN transmission for multi-frame MBP preparation',async t => {
  const board = new VirtualBoard(), deliver = board.call.bind(board);
  const device = new MotorDevice(board);
  const timer = setInterval(() => board.publish(),50);
  t.after(async () => {clearInterval(timer);await device.close();});
  const modes = [];
  board.call = async (method,args) => {
    if (method === 'serial-write' && args.data[0] === 0x5a) {
      const frame = args.data;
      if (((frame[5] << 8) | frame[6]) === CAN.request + 1) {
        modes.push(frame[2] & 0xc0);
        // Model an arbitration loss that the adapter only recovers in normal mode.
        if (frame[7] === 0x28 && (frame[2] & 0xc0)) return;
      }
    }
    return deliver(method,args);
  };
  await device.connect({device:'virtual-test-only',node:1,interface:'zqwl',baud:500000});
  const prepared = await device.prepare({[O.run_mode]:5,[O.direction]:1,[O.iq_target]:0,
    [O.phase_run_limit]:0,[O.bus_current_budget]:20000,[O.bus_voltage_basis]:24000,
    [O.negative_id_min]:0,[O.speed_soft_limit]:0});
  assert.equal(prepared,88);
  assert.ok(modes.length > 8 && modes.every(mode => mode === 0));
  assert.equal(board.commands.filter(({opcode}) => opcode === OP.prepare_run).length,1);
  assert.equal(device.snapshot().motor.output_on,false);
  assert.ok(!board.commands.some(({opcode}) => opcode === OP.start));
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
