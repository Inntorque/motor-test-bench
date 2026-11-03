import {CAN,OP,O,IsoTp,packet,unpack,objects,values,tlv} from '../../browser/mbp-codec.js';
import {concat,crc32,pack,view,hex} from '../../browser/bytes.js';
import {zqwlFrame} from '../../browser/transports.js';
const assert = {equal(actual,expected) {if (actual !== expected) throw new Error(`Virtual board protocol mismatch: ${actual} !== ${expected}`);}};
export class VirtualBoard {
  constructor() {
    this.listeners = new Set();this.commands = [];this.boot = 0x1122334455667788n;this.session = 0n;this.generation = 0;this.sequence = 0;this.state = 0;this.mode = 0;this.sampleSeq = 0;this.iq = 0;this.stopId = 0;this.trace = Uint8Array.from({length:120},(_,index) => index & 255);
    this.identity = {[O.device_uid]:Uint8Array.from({length:16},(_,i) => i),[O.hardware_identity]:'test-fixture-only',[O.firmware_version]:'fixture',[O.sdk_version]:'1.0.0',[O.boot_id]:this.boot,[O.control_modes]:58,[O.feedback_fields]:(1n << 24n) - 1n,[O.optional_features]:31n,[O.dq_current_convention]:1,[O.iq_min]:-30000,[O.iq_max]:30000,[O.phase_protection_current]:30000,[O.phase_run_limit_max]:30000,[O.allowed_negative_id_min]:-30000,[O.dq_voltage_limit]:13856,[O.rotor_speed_abs_max]:20000000,[O.motor_temperature_trip]:45000,[O.device_watchdog_ms]:1000,[O.readiness_flags]:37,[O.fault_category]:0,[O.vendor_fault_code]:0,[O.readiness_detail]:'fixture ready',[O.available_iq]:27000,[O.active_limits]:1};
    this.channels = [0,1].map(channel => new IsoTp(data => {this.toHost((channel ? CAN.diagnostic_response : CAN.response) + 1,data);return Promise.resolve();},data => {void this.request(channel,data);},channel ? 256 : 1024));
  }
  toHost(id,data) {const frame = zqwlFrame(id,data);queueMicrotask(() => {for (const listener of this.listeners) listener(frame);});}
  subscribe(_,listener) {this.listeners.add(listener);return () => this.listeners.delete(listener);}
  async call(method,args) {
    if (method === 'serial-open' || method === 'serial-close') return;
    if (method !== 'serial-write') throw new Error('unexpected fixture IO');
    const frame = args.data;
    if (frame[0] === 0x49) {
      if (frame[2] === 0x44) {const heartbeat = new Uint8Array(17);heartbeat.set([0x5a,0xff]);queueMicrotask(() => {for (const listener of this.listeners) {listener(heartbeat);listener(heartbeat);}});}
      return;
    }
    const id = frame[5] << 8 | frame[6], data = frame.slice(7,-1);
    if (id === CAN.request + 1 || id === CAN.diagnostic_request + 1) this.channels[id === CAN.request + 1 ? 0 : 1].receive(data);
    else if (id === CAN.stop + 1) {
      assert.equal(hex(data.slice(0,4)),'53544f50');this.stopId = view(data).getUint32(4,true);this.state = 0;this.session = 0n;this.publish();
    } else if (id === CAN.control_heartbeat + 1) assert.equal(view(data).getBigUint64(0,true),this.session);
  }
  stateObjects() {return {[O.state]:this.state,[O.mode]:this.mode,[O.status_flags]:this.state === 2 ? 19 : 2,[O.session_id]:this.session,[O.run_generation]:this.generation,[O.command_seq]:this.sequence,[O.last_stop_id]:this.stopId};}
  async request(channel,raw) {
    const request = unpack(raw);this.commands.push(request);let body = request.body,result = {},rc = 0;
    if ([OP.prepare_run,OP.start,OP.set_target,OP.clear_fault,OP.set_streams].includes(request.opcode)) {
      const envelope = view(body);
      if (envelope.getBigUint64(0,true) !== this.boot || envelope.getBigUint64(8,true) !== this.session) rc = 7;
      else if (envelope.getUint32(16,true) !== this.generation) rc = 8;
      this.sequence = envelope.getUint32(20,true);body = body.slice(24);
    }
    if (!rc) {
      if (request.opcode === OP.identify || request.opcode === OP.read_objects) result = this.identity;
      if (request.opcode === OP.read_state) result = this.stateObjects();
      if (request.opcode === OP.claim_control) {const source = view(body);assert.equal(source.getBigUint64(0,true),this.boot);this.session = source.getBigUint64(8,true);this.generation = source.getUint32(16,true);this.sequence = source.getUint32(20,true);}
      if (request.opcode === OP.prepare_run) {this.config = values(body);this.mode = this.config[O.run_mode];result = {...this.config,[O.prepared_id]:88};}
      if (request.opcode === OP.start) {assert.equal(values(body)[O.prepared_id],88);this.state = 2;}
      if (request.opcode === OP.set_target) this.iq = values(body)[O.iq_target];
      if (request.opcode === OP.read_diagnostic && view(body).getUint16(0,true) === 3) result = {[O.frozen_transfer_descriptor]:concat(tlv(1,3,77),tlv(2,2,1),tlv(3,3,3),tlv(4,2,40),tlv(5,3,this.trace.length),tlv(6,3,crc32(this.trace)),tlv(7,3,500))};
      if (request.opcode === OP.read_transfer) {
        const args = values(body),data = this.trace.slice(args[2],args[2] + args[3]);
        const encoded = concat(tlv(1,3,77),tlv(2,3,args[2]),tlv(3,9,data),tlv(4,3,crc32(data)));
        await this.channels[channel].start(packet(request.opcode,request.request,concat(pack('HBB',0,2,0),encoded),1));return;
      }
    }
    await this.channels[channel].start(packet(request.opcode,request.request,concat(pack('HBB',rc,2,0),objects(result)),1));
    if ([OP.read_state,OP.start,OP.set_target].includes(request.opcode)) this.publish();
  }
  publish() {
    this.toHost(CAN.status + 1,pack('BBHI',this.state,this.mode,this.state === 2 ? 19 : 2,this.stopId));
    const seq = ++this.sampleSeq & 65535, stamp = BigInt(Math.round(performance.now() * 1000)),position = BigInt.asUintN(64,-1234567890123n),running = this.state === 2;
    const f = {0:Number(stamp & 0xffffffffn),1:Number(stamp >> 32n),2:running ? this.mode === 5 ? 26800 : this.iq || 0 : 0,3:running ? this.mode === 5 ? 27000 : this.iq || 0 : 0,4:running ? -400 : 0,5:running ? 120000 : 0,6:Number(position & 0xffffffffn),7:Number(position >> 32n),8:24000,9:5000,10:25000,11:26000,12:this.iq || 0,13:13856,14:5000,16:1000,17:-500,18:-500,19:seq,20:seq,21:Number(stamp & 0xffffffffn),22:Number(stamp >> 32n),23:10000};
    let mask = 0;
    for (const [id,value] of Object.entries(f)) {mask = (mask | 1 << Number(id)) >>> 0;this.toHost(CAN.sample + 1,pack('HBBI',seq,Number(id),1,value));}
    this.toHost(CAN.sample + 1,pack('HBBI',seq,254,0,mask));
  }
}
