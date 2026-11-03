import {CAN, OP, O, schema, objects, packet, unpack, decodeTlvs, values, tlv, IsoTp, SampleAssembler} from './mbp-codec.ts';
import {concat, crc32, hex, jsonValue, now, pack, random32, random64, SerialQueue, sleep, utc, view} from './bytes.ts';
import {CanTransport} from './transports.ts';
import profile from '../profiles/bench.json' with {type:'json'};
const results = Object.keys(schema.enums.RESULT);
const objectSpecs = Object.fromEntries(Object.values(schema.objects).map(spec => [spec.id,spec]));
export class MotorDevice {
  declare bootTag;
  declare bus: CanTransport | undefined;
  declare channels: IsoTp[] | undefined;
  declare diagnosing: boolean | undefined;
  declare diagnosticTimer: number | undefined;
  declare error;
  declare heartbeatTimer: number | undefined;
  declare identifiedAt: number | undefined;
  declare identifiedUtc: number | undefined;
  declare io;
  declare lastFaultEvent: { code: number; severity: number; source: number; deviceMs: number; receivedAt: number; } | undefined;
  declare maximum: { state: number; window: Record<number, [number, unknown]>; updated_at: number; } | undefined;
  declare node: number | undefined;
  declare observe: (kind: string, data: unknown) => void;
  declare sampleUtc: number | undefined;
  declare stateUtc: number | undefined;
  declare uptime: number | undefined;
  declare wire: [number, number] | undefined;

  identity = {}; sample: {fields?: Record<number, number>; flags?: Record<number, number>; seq?: number; receivedMonotonic?: number; receivedAt?: number; bootId?: string; deviceUid?: string; recordSchema?: number; runGeneration?: number; sessionId?: string} = {}; state: {state?: number; mode?: number; flags?: number; lastStopId?: number} = {}; stateAt = 0; sampleAt = 0; bootId = 0n; session = 0n; generation = 0; sequence = 0;
  pending = new Map(); requestId = random32(); assembler = new SampleAssembler(); queues = [new SerialQueue(),new SerialQueue()];
  constructor(io, observe: (kind: string, data: unknown) => void = () => {}) { this.io = io; this.observe = observe; }
  event(kind, data) { this.observe(kind,jsonValue({bootId:this.bootId,deviceUid:hex(this.identity[O.device_uid] || []),receivedMonotonic:now(),...data})); }
  fail(error) {
    this.error = error.message || String(error); this.session = 0n;
    for (const pending of this.pending.values()) pending.reject(new Error(this.error));
    this.pending.clear();
  }
  receive(id, data) {
    try {
      const base = id - this.node, source = view(data);
      if (base === CAN.response || base === CAN.diagnostic_response) return this.channels[base === CAN.diagnostic_response ? 1 : 0].receive(data);
      if (base === CAN.status && data.length === 8) {
        if (this.wire && this.wire[0] !== schema.wire[0]) return;
        const flags = source.getUint16(2,true);
        if (data[0] > 4 || data[1] > 5 || flags & ~63) return;
        this.state = {state:data[0],mode:data[1],flags,lastStopId:source.getUint32(4,true)};
        this.stateAt = now(); this.stateUtc = utc();
      } else if (base === CAN.device_heartbeat && data.length === 8) {
        const tag = source.getUint32(0,true), uptime = source.getUint32(4,true);
        if (this.bootTag !== undefined && (tag !== this.bootTag || ((uptime - this.uptime) >>> 0) > 0x80000000)) {
          this.identity = {}; this.state = {}; this.sample = {}; this.stateAt = 0; this.sampleAt = 0; this.bootId = 0n;
          this.fail(new Error('驱动板已重启，请重新连接识别'));
        }
        this.bootTag = tag; this.uptime = uptime;
      } else if (base === CAN.sample) {
        const sample = this.assembler.receive(data);
        if (sample) {
          this.sample = {...sample,receivedMonotonic:now(),receivedAt:utc(),bootId:this.bootId.toString(),deviceUid:hex(this.identity[O.device_uid] || []),recordSchema:2,runGeneration:this.generation,sessionId:this.session.toString()};
          this.sampleAt = now(); this.sampleUtc = utc(); this.event('motor_snapshot',this.sample);
        }
      } else if (base === CAN.event && data.length === 8) {
        const event = {code:source.getUint16(0,true),severity:data[2],source:data[3],deviceMs:source.getUint32(4,true),receivedAt:utc()};
        if (event.severity === 2) { this.lastFaultEvent = event; if (event.source === 1) this.identity[O.vendor_fault_code] = event.code; }
        this.event('motor_event',event);
      }
    } catch (error) { this.event('motor_protocol_rejected',{id,data:hex(data),error:error.message}); }
  }
  response(channel, data) {
    try {
      const response = unpack(data), pending = this.pending.get(response.request);
      if (!pending || pending.channel !== channel || pending.opcode !== response.opcode) return;
      if (!(response.flags & 1) || response.body.length < 4) throw new Error('invalid response');
      const rc = view(response.body).getUint16(0,true), stage = response.body[2];
      if (response.body[3] || stage > 2 || rc >= results.length) throw new Error('invalid result');
      const decoded = decodeTlvs(response.body.slice(4));
      const dictionary = response.opcode === OP.read_transfer ? schema.compounds.transfer_response : objectSpecs;
      for (const [id,[type,value]] of Object.entries(decoded)) {
        if (response.major !== schema.wire[0] && Number(id) > 5) continue;
        const spec = dictionary[id];
        if (spec && type !== schema.types[Array.isArray(spec) ? spec[1] : spec.type]) throw new Error('response object type');
        if (type === 10 && response.opcode !== OP.read_transfer && spec) {
          const name = Object.keys(schema.objects).find(name => schema.objects[name].id === Number(id));
          const fields = schema.compounds[name] || {};
          for (const [field,[fieldType]] of Object.entries(value as Record<number, [number, unknown]>)) if (fields[field] && fieldType !== schema.types[fields[field][1]]) throw new Error('compound field type');
        }
      }
      this.wire = [response.major,response.minor];
      const result = Object.fromEntries(Object.entries(decoded).map(([id,[,value]]) => [id,value]));
      if (rc > 1) pending.reject(new Error(`驱动板拒绝：${results[rc]}`));
      else pending.resolve({rc,stage,result});
    } catch (error) { this.event('motor_protocol_rejected',{channel,error:error.message}); }
  }
  request(opcode, body = new Uint8Array(), {mutation = false, channel = 0} = {}) {
    return this.queues[channel].run(async () => {
      if (mutation) {
        if (!this.session) throw new Error('没有 MBP 控制会话');
        this.sequence++;
        body = concat(pack('QQII',this.bootId,this.session,this.generation,this.sequence),body);
      }
      const request = this.requestId = (this.requestId + 1) >>> 0 || 1;
      this.event('motor_command_request',{requestId:request,opcode,body:hex(body),sessionId:this.session.toString(),generation:this.generation,commandSeq:this.sequence});
      let timer;
      const reply = new Promise<{rc: number; stage: number; result: ReturnType<typeof values>}>((resolve,reject) => {
        this.pending.set(request,{channel,opcode,resolve,reject});
        timer = setTimeout(() => reject(new Error(`MBP ${opcode} 应答超时；动作不自动重发`)),3000);
      });
      const sending = this.channels[channel].start(packet(opcode,request,body));
      try {
        const response = await Promise.all([reply,sending]).then(([response]) => response);
        this.event('motor_command_response',{requestId:request,result:results[response.rc],stage:response.stage,objects:response.result});
        return response.result;
      } finally { clearTimeout(timer); this.pending.delete(request); }
    });
  }
  async connect(config) {
    if (!this.bus || this.bus.closed || this.error) {
      if (this.bus) await this.bus.close();
      this.node = Number(config.node ?? 1);
      if (!Number.isInteger(this.node) || this.node < 1 || this.node > 63) throw new Error('MBP 节点必须为 1–63');
      this.bus = new CanTransport(this.io,(id,data) => this.receive(id,data));
      this.channels = [0,1].map(channel => new IsoTp(data => this.bus.send((channel ? CAN.diagnostic_request : CAN.request) + this.node,data),data => this.response(channel,data),channel ? 256 : 1024));
      await this.bus.open(config);
    }
    this.error = '';
    const identity = await this.request(OP.identify);
    const required = [O.device_uid,O.boot_id,...(this.wire[0] === schema.wire[0] ? [O.control_modes,O.feedback_fields,O.dq_current_convention] : [])];
    if (required.some(id => identity[id] === undefined) || (identity[O.device_uid] as Uint8Array)?.length !== 16) throw new Error('缺少有效 MBP 核心身份／能力对象');
    this.identity = identity; this.bootId = identity[O.boot_id] as bigint; this.identifiedAt = now(); this.identifiedUtc = utc();
    if (this.wire[0] === schema.wire[0]) {
      await this.request(OP.read_state);
      await this.wait(() => this.stateAt > 0,1000,'没有新鲜 STATUS');
    }
    clearInterval(this.heartbeatTimer); clearInterval(this.diagnosticTimer);
    this.heartbeatTimer = setInterval(() => { if (this.session) void this.bus.send(CAN.control_heartbeat + this.node,pack('Q',this.session)).catch(error => this.fail(error)); },250);
    this.diagnosticTimer = setInterval(() => { if (!this.diagnosing) void this.diagnostics(); },1000);
    return this.snapshot();
  }
  async diagnostics() {
    this.diagnosing = true;
    try {
      if (this.wire[0] !== schema.wire[0]) { this.identity = await this.request(OP.identify); this.identifiedAt = now(); this.identifiedUtc = utc(); return; }
      Object.assign(this.identity,await this.request(OP.read_objects));
      if (Number(this.identity[O.optional_features] || 0) & 8) {
        const data = await this.request(OP.read_diagnostic,pack('H',1),{channel:1});
        this.maximum = {state:(data[O.maximum_state] as number) ?? 3,window:(data[O.maximum_window] as Record<number, [number, unknown]>) || {},updated_at:utc()};
        this.event('maximum_output_window',this.maximum);
      }
    } catch (error) { this.event('motor_diagnostic_error',{error:error.message}); }
    finally { this.diagnosing = false; }
  }
  async wait(condition, timeout, message) {
    const deadline = now() + timeout / 1000;
    while (!condition()) { if (now() >= deadline) throw new Error(message); await sleep(10); }
  }
  async claim() {
    if (this.wire?.[0] !== schema.wire[0] || this.identity[O.dq_current_convention] !== 1) throw new Error('协议或 dq 电流定义不兼容，保持只读');
    const state = await this.request(OP.read_state), session = random64(), generation = Number(state[O.run_generation]) + 1;
    await this.request(OP.claim_control,pack('QQII',this.bootId,session,generation,1));
    this.session = session; this.generation = generation; this.sequence = 1;
  }
  async prepare(config): Promise<number> {
    if (!this.session) await this.claim();
    const result = await this.request(OP.prepare_run,objects(config),{mutation:true});
    for (const [id,value] of Object.entries(config)) if (result[id] !== value) throw new Error(`运行配置读回不一致：${id}`);
    if (!result[O.prepared_id]) throw new Error('缺少 prepared_id');
    Object.assign(this.identity,await this.request(OP.read_objects,pack('H',O.phase_protection_current)));
    return result[O.prepared_id] as number;
  }
  async start(prepared) {
    const threshold = now();
    await this.request(OP.start,objects({[O.prepared_id]:prepared}),{mutation:true});
    await this.wait(() => {
      if (this.stateAt > threshold && this.state.state === 4) throw new Error('设备启动触发保护');
      return this.stateAt > threshold && this.state.state === 2 && this.state.flags & 1 && this.sampleAt > threshold && this.sample.flags?.[2] & 1 && this.sample.flags?.[4] & 1;
    },2000,'没有新鲜 RUNNING/output_on 确认');
  }
  setTarget(oid,value) { return this.request(OP.set_target,objects({[oid]:value}),{mutation:true}); }
  async stop() {
    if (!this.bus || this.bus.closed) throw new Error('CAN 未连接，不能确认电机停止');
    this.session = 0n;
    for (const channel of this.channels) channel.reset();
    for (const pending of this.pending.values()) pending.reject(new Error('控制已被停止取消'));
    this.pending.clear();
    const id = random32(), threshold = now();
    await this.bus.send(CAN.stop + this.node,concat(new TextEncoder().encode('STOP'),pack('I',id)));
    await this.wait(() => this.stateAt > threshold && this.state.lastStopId === id && !(this.state.flags & 1) && [0,4].includes(this.state.state),2000,'STOP 未获新鲜输出关闭确认');
    return {lastStopId:id,deviceDisabled:true};
  }
  async readFrozen() {
    const result = await this.request(OP.read_diagnostic,pack('H',3),{channel:1});
    const descriptor = result[O.frozen_transfer_descriptor], get = id => descriptor[id][1];
    const dataset = get(1), total = get(5), crc = get(6);
    let data = new Uint8Array();
    while (data.length < total) {
      const part = await this.request(OP.read_transfer,concat(tlv(1,3,dataset),tlv(2,3,data.length),tlv(3,2,Math.min(160,total - data.length))),{channel:1}) as {1: number; 2: number; 3: Uint8Array; 4: number};
      if (part[1] !== dataset || part[2] !== data.length || !part[3]?.length || crc32(part[3]) !== part[4]) throw new Error('冻结数据块校验失败');
      data = concat(data,part[3]);
    }
    if (data.length !== total || crc32(data) !== crc) throw new Error('冻结数据集不完整');
    return {descriptor,data};
  }
  snapshot() {
    if (!Object.keys(this.identity).length || this.error) throw new Error(this.error || '电机未识别');
    if (this.wire[0] !== schema.wire[0]) {
      if (now() - this.identifiedAt > 3) throw new Error('只读身份反馈过期');
      return {wireProtocol:this.wire,deviceUid:hex(this.identity[O.device_uid]),bootId:this.bootId.toString(),version:this.identity[O.firmware_version],sdkVersion:this.identity[O.sdk_version],capabilities:{modes:0,fields:0,features:0,maximumOutput:false},readiness:{flags:0,detail:'协议主版本不兼容，仅允许身份读取与 STOP'},motor:{available:false,output_on:null},feedback:{stale:true,position_stale:true},ntc:{valid:false}};
    }
    if (now() - this.stateAt > 1.5) throw new Error('MBP 状态反馈过期');
    const f = this.sample.fields || {}, flags = this.sample.flags || {}, age = now() - this.sampleAt;
    const valid = id => f[id] !== undefined && flags[id] & 1 && age <= 1.5;
    const number = (id,scale = 1000) => valid(id) ? (f[id] | 0) / scale : null;
    const position = BigInt.asIntN(64,BigInt(f[7] || 0) << 32n | BigInt(f[6] || 0));
    const positionValid = Boolean(valid(6) && valid(7)), readiness = this.identity[O.readiness_flags] || 0;
    const modes = this.identity[O.control_modes], features = Number(this.identity[O.optional_features] || 0), fields = Number(this.identity[O.feedback_fields]);
    const iqMax = this.identity[O.iq_max], limit = this.identity[O.phase_protection_current], state = this.state;
    const raw = Object.fromEntries(Object.entries((this.maximum?.window || {}) as Record<string, [number, unknown]>).map(([id,[,value]]) => [id,value])) as Record<number, unknown> & {[K in 7 | 8 | 9 | 10 | 11]?: number};
    return jsonValue({motorProfile:profile.motor,profileProvenance:profile.provenance,wireProtocol:this.wire,sdkVersion:this.identity[O.sdk_version],version:this.identity[O.firmware_version],deviceUid:hex(this.identity[O.device_uid]),bootId:this.bootId.toString(),node:this.node,
      capabilities:{modes,fields,features,maximumOutput:Boolean(modes & 1 << 5 && features & 1 && this.identity[O.available_iq] !== undefined && this.identity[O.active_limits] !== undefined),coherentSample:Boolean(features & 4)},
      dynamicLimits:{availableIqA:(this.identity[O.available_iq] || 0) / 1000,activeLimits:this.identity[O.active_limits]},limits:Object.fromEntries(Object.entries(this.identity).filter(([id]) => Number(id) >= 0x200 && Number(id) < 0x300)),
      readiness:{flags:readiness,detail:this.identity[O.readiness_detail],faultCategory:this.identity[O.fault_category],vendorCode:this.identity[O.vendor_fault_code],lastFaultEvent:this.lastFaultEvent},
      motor:{available:true,mode:state.mode,state:state.state,state_name:['DISABLED','STARTING','RUNNING','STOPPING','FAULT'][state.state],output_on:Boolean(state.flags & 1),fault:Boolean(state.flags & 4),fault_reason:this.identity[O.vendor_fault_code],updated_at:this.stateUtc,age_s:now() - this.stateAt},
      phaseCurrent:{ia_mA:number(16,1),ib_mA:number(17,1),ic_mA:number(18,1),stale:![16,17,18].every(valid),updated_at:this.sampleUtc},
      encoder:{valid:positionValid,stale:!positionValid,electrical_calibrated:Boolean(readiness & 4),electrical_angle_valid:Boolean(readiness & 32)},
      ntc:{temp_c:number(10),valid:Boolean(valid(10)),sourceDeviceTimeUs:valid(21) && valid(22) ? (BigInt(f[22]) << 32n | BigInt(f[21])).toString() : null,limit_c:this.identity[O.motor_temperature_trip] / 1000 || null,over:Boolean(readiness & 8),fault:!valid(10),tripped_latched:Boolean(readiness & 16 && this.identity[O.fault_category] === 3),age_s:age},
      feedback:{iq_a:number(2),id_a:number(4),iq_reference_a:number(3),speed_rpm:number(5),position_deg:positionValid ? Number(position) / 1000000 * 360 : null,positionMicroturns:positionValid ? position.toString() : null,deviceTimeUs:(BigInt(f[1] || 0) << 32n | BigInt(f[0] || 0)).toString(),sampleSeq:this.sample.seq,stale:!valid(2),position_stale:!positionValid,updated_at:this.sampleUtc,position_at:this.sampleUtc},
      control:{mode:state.mode,state:state.state,iq_target_ma:number(12,1),updated_at:this.sampleUtc},receivedAt:this.sampleUtc,limitA:limit ? limit / 1000 : null,supportedMaxA:iqMax ? iqMax / 1000 : null,fullTargetA:iqMax ? Math.min(10,iqMax / 1000) : null,
      maximumOutput:this.maximum ? {state:this.maximum.state,receivedAt:this.maximum.updated_at,updated_at:this.maximum.updated_at,schema:raw[1],windowId:raw[2],mcuTimeUs:raw[3],iqReferenceA:(raw[7] || 0) / 1000,idReferenceA:(raw[8] || 0) / 1000,operatingCurrentA:(raw[9] || 0) / 1000,voltageLimitV:this.identity[O.dq_voltage_limit] / 1000,flags:raw[6],iqMeanA:(raw[10] || 0) / 1000,idMeanA:(raw[11] || 0) / 1000,raw} : null});
  }
  async close() { clearInterval(this.heartbeatTimer); clearInterval(this.diagnosticTimer); this.fail(new Error('CAN connection closed')); if (this.bus) await this.bus.close(); }
}
