import {controlCapabilities} from '../protocol/capabilities.ts';
import {CAN, OP, O, schema, objects, packet, unpack, decodeTlvs, values, tlv, IsoTp, SampleAssembler} from './mbp-codec.ts';
import {finite, concat, crc32, hex, jsonValue, now, pack, random32, random64, SerialQueue, sleep, utc, view} from './bytes.ts';
import {CanTransport} from './transports.ts';
import {FocLink, focProfiles, focRunning, focValue} from './foc-protocol.ts';
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
  private nativeLink?: FocLink;
  private nativeQueue = new SerialQueue();
  private nativeGeneration = 0;
  private nativeMode?: 'dyno' | 'current_loop';
  private nativeTimer?: ReturnType<typeof setInterval>;
  private nativePolling = false;
  private nativeSnapshot?: ReturnType<typeof import('./foc-protocol.ts').decodeFocSnapshot>;
  private nativeSnapshotAt = 0;
  private nativeSnapshotUtc = 0;
  private nativeTargetA = 0;
  private nativeStartSent = false;

  identity = {}; sample: {fields?: Record<number, number>; flags?: Record<number, number>; seq?: number; receivedMonotonic?: number; receivedAt?: number; bootId?: string; deviceUid?: string; recordSchema?: number; runGeneration?: number; sessionId?: string} = {}; state: {state?: number; mode?: number; flags?: number; lastStopId?: number} = {}; stateAt = 0; sampleAt = 0; bootId = 0n; session = 0n; generation = 0; sequence = 0;
  pending = new Map(); requestId = random32(); assembler = new SampleAssembler(); queues = [new SerialQueue(),new SerialQueue()];
  constructor(io, observe: (kind: string, data: unknown) => void = () => {}) { this.io = io; this.observe = observe; }
  event(kind, data) { this.observe(kind,jsonValue({bootId:this.bootId,deviceUid:hex(this.identity[O.device_uid] || []),receivedMonotonic:now(),...data})); }
  fail(error) {
    this.error = error.message || String(error); this.session = 0n;
    clearInterval(this.nativeTimer); this.nativeGeneration++; this.nativeLink?.fail(new Error(this.error));
    for (const pending of this.pending.values()) pending.reject(new Error(this.error));
    this.pending.clear();
  }
  receive(id, data) {
    try {
      this.nativeLink?.receiveFrame(id,data);
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
      const identity = response.opcode === OP.identify ? result : this.identity;
      const legacyRead = stage === 1 && this.node === 1 && identity[O.hardware_identity] === '455V1.3 + 5014' &&
        identity[O.sdk_version] === '1.0.0' && [OP.identify, OP.read_objects, OP.read_state, OP.read_diagnostic, OP.read_transfer].includes(response.opcode);
      if (rc > 1) pending.reject(new Error(`驱动板拒绝：${results[rc]}`));
      else if(rc===1||stage!==2&&!legacyRead)this.event('motor_command_progress',{requestId:response.request,result:results[rc],stage});
      else pending.resolve({rc,stage,result});
    } catch (error) { this.event('motor_protocol_rejected',{channel,error:error.message}); }
  }
  request(opcode, body = new Uint8Array(), {mutation = false, channel = 0} = {}) {
    return this.queues[channel].run(async () => {
      if (this.nativeMode) throw new Error('原生控制运行期间不发送 MBP 请求');
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
  private connectQueue = new SerialQueue();
  connect(config) {return this.connectQueue.run(() => this.identifyConnection(config));}

  private async identifyConnection(config) {
    const opening = !this.bus || this.bus.closed || !!this.error;
    if (opening) {
      if (this.bus) await this.bus.close();
      this.node = Number(config.node ?? 1);
      if (!Number.isInteger(this.node) || this.node < 1 || this.node > 63) throw new Error('MBP 节点必须为 1–63');
      this.identity = {}; this.state = {}; this.sample = {}; this.stateAt = this.sampleAt = 0;
      this.bootId = 0n; this.bootTag = undefined; this.wire = undefined;
      this.stateUtc = this.sampleUtc = undefined;
      this.maximum = this.lastFaultEvent = undefined;
      this.assembler = new SampleAssembler();
      this.bus = new CanTransport(this.io,(id,data) => this.receive(id,data));
      // Let the CAN controller retry ISO-TP frames that lose bus arbitration.
      this.channels = [0,1].map(channel => new IsoTp(
        data => this.bus.send((channel ? CAN.diagnostic_request : CAN.request) + this.node,data,false),
        data => this.response(channel,data),channel ? 256 : 1024));
    }
    try {
      if (opening) await this.bus.open(config);
      return await this.identify();
    } catch (error) {
      if (!opening) throw error;
      const message = error.message === 'ISO-TP flow control timeout'
        ? `USB-CAN 已打开，节点 ${this.node} 未回应 MBP/1 握手。请确认驱动板已上电，并核对 CAN 接线和节点。`
        : error.message;
      try {await this.close();} finally {this.error = message;}
      throw new Error(message,{cause:error});
    }
  }
  private async identify() {
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
    if (this.nativeMode) return;
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
  async refreshReadiness() {
    this.snapshot();
    if (this.wire[0] !== schema.wire[0]) throw new Error('协议主版本不兼容，保持只读');
    const readiness = await this.request(OP.read_objects);
    if (readiness[O.readiness_flags] === undefined) throw new Error('驱动板未返回就绪状态');
    Object.assign(this.identity,readiness);
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
  async startControl(mode: 'dyno' | 'current_loop', targetA: number, checkCancelled: () => void) {
    const snapshot=this.snapshot();
    if(snapshot.capabilities.nativeControl)return this.startNative(mode,targetA,checkCancelled);
    if(!snapshot.capabilities[mode==='dyno'?'dyno':'currentLoop'])throw new Error('驱动板未声明所选模式的完整能力');
    checkCancelled();
    const config={[O.run_mode]:mode==='dyno'?5:1,[O.direction]:1,[O.iq_target]:Math.round(targetA*1000),
      [O.phase_run_limit]:Math.min(Math.round(snapshot.limitA*1000),Number(snapshot.limits[O.phase_run_limit_max])),[O.bus_current_budget]:0,[O.bus_voltage_basis]:0,[O.negative_id_min]:0,[O.speed_soft_limit]:0};
    const prepared=await this.prepare(config);
    checkCancelled();
    await this.start(prepared);
    checkCancelled();
    if(mode==='current_loop')await this.wait(()=>this.snapshot().control.iq_target_ma===Math.round(targetA*1000),2000,'启动目标电流未获回读确认');
    checkCancelled();
  }
  async setControlCurrent(targetA: number) {
    const snapshot=this.snapshot(),caps=snapshot.capabilities;
    finite(targetA,'目标 Iq',caps.currentLoopMinA,caps.currentLoopMaxA);
    if(caps.nativeControl)return this.setNativeCurrent(targetA);
    if(!caps.currentLoop || !this.session || snapshot.motor.mode!==1 || !snapshot.motor.output_on)throw new Error('电流环尚未运行');
    const threshold=now();
    await this.setTarget(O.iq_target,Math.round(targetA*1000));
    await this.wait(()=>this.sampleAt>threshold && this.snapshot().control.iq_target_ma===Math.round(targetA*1000),2000,'目标电流未获新鲜回读确认');
  }
  async startNative(mode: 'dyno' | 'current_loop', targetA: number, checkCancelled: () => void) {
    if (!this.snapshot().capabilities.nativeControl || this.session || this.nativeLink) throw new Error('当前驱动不支持 5014 原生控制，或控制会话仍在使用');
    const value = mode === 'current_loop' ? focValue(focProfiles['foc-5014'],8,targetA) : 0;
    const generation = ++this.nativeGeneration, link = new FocLink(this.io,0,focProfiles['foc-5014'],this.bus);
    this.nativeLink = link; this.nativeMode = mode; this.nativeSnapshot = undefined; this.nativeTargetA = targetA; this.nativeStartSent = false;
    const check = () => {if(this.error)throw new Error(this.error);checkCancelled();if(generation !== this.nativeGeneration)throw new Error('启动已被停止取消');};
    // The firmware's native lease excludes MBP requests; finish existing reads before taking it.
    await Promise.all(this.queues.map(queue => queue.tail));
    await this.nativeQueue.run(async () => {
      check(); await link.command(0);
      const meta = await link.diagnostic(23,1000), uid = this.identity[O.device_uid] as Uint8Array;
      if (!(uid instanceof Uint8Array) || uid.length !== 16 || !uid.slice(0,12).every((byte,i) => byte === meta[128+i])) throw new Error('原生接口与当前驱动 UID 不一致，未启动');
      check();
      const before = await link.snapshot();
      if (before.state !== 8 || before.fault || !before.calibrated || !before.encoder_ok) throw new Error('原生接口未确认电机就绪');
      check();
      const action = mode === 'dyno' ? 19 : 8;
      this.event('motor_native_command',{action,value,mode});
      this.nativeStartSent = true;
      await link.command(action,value);
      check();
    });
    const poll = async () => {
      if (this.nativePolling || generation !== this.nativeGeneration) return;
      this.nativePolling = true;
      try {
        await this.nativeQueue.run(async () => {
          check(); await link.command(6); check();
          const snapshot = await link.snapshot(); check();
          this.nativeSnapshot = snapshot; this.nativeSnapshotAt = now(); this.nativeSnapshotUtc = utc();
          this.event('motor_native_snapshot',{...snapshot,receivedAt:this.nativeSnapshotUtc,mode});
        });
      } catch(error) {if(generation === this.nativeGeneration)this.fail(error);}
      finally {this.nativePolling = false;}
    };
    this.nativeTimer = setInterval(() => {void poll();},150);
    await poll(); check();
    await this.wait(() => {
      check();
      if(this.nativeSnapshot?.fault)throw new Error(`原生控制启动故障 ${this.nativeSnapshot.fault}`);
      return this.nativeSnapshot?.state === (mode === 'dyno' ? 17 : 11) && now()-this.nativeSnapshotAt <= 1.5;
    },2000,'未收到原生模式与输出开启确认');
  }
  async setNativeCurrent(targetA: number) {
    const value = focValue(focProfiles['foc-5014'],9,targetA), generation = this.nativeGeneration;
    return this.nativeQueue.run(async () => {
      if(this.error || this.nativeMode !== 'current_loop' || !this.nativeLink || generation !== this.nativeGeneration)throw new Error('电流环未运行');
      this.event('motor_native_command',{action:9,value,mode:this.nativeMode});
      await this.nativeLink.command(9,value); this.nativeTargetA = targetA;
    });
  }
  async clearKnownFault(checkStopped: () => void) {
    if (!this.bus || this.bus.closed || this.node !== 1 || this.session || this.nativeLink) throw new Error('驱动未连接、节点不匹配或控制会话仍在使用');
    const uid = this.identity[O.device_uid] as Uint8Array, hardware = this.identity[O.hardware_identity], version = this.identity[O.firmware_version];
    const model = hardware === '455V1.3 + 5014' && version === '0x0914' ? 'foc-5014' : hardware === '455V1.3 + 8513' && version === '0x0916' ? 'foc-8513' : null;
    if (!model || !(uid instanceof Uint8Array) || uid.length !== 16) throw new Error('当前驱动身份或固件不支持此清故障方式');
    const link = new FocLink(this.io,0,focProfiles[model],this.bus);
    this.nativeLink = link;
    try {
      checkStopped();
      const meta = await link.diagnostic(23,1000);
      if (!uid.slice(0,12).every((byte,i) => byte === meta[128+i])) throw new Error('原生接口与当前驱动 UID 不一致，未清除故障');
      checkStopped();
      await link.command(20);
      const threshold = now();
      await this.wait(() => this.stateAt > threshold && !(this.state.flags & 1),2000,'清除后未收到新鲜输出关闭状态');
      await this.refreshReadiness();
      const snapshot = this.snapshot();
      if (this.session || snapshot.motor.output_on || snapshot.motor.fault || snapshot.motor.state === 4 || !(snapshot.readiness.flags & 1)) throw new Error(`设备仍未就绪：${snapshot.readiness.detail || '状态未解除'}`);
      return snapshot;
    } finally { this.nativeLink = undefined; }
  }
  async stop() {
    if (!this.bus || this.bus.closed) throw new Error('CAN 未连接，不能确认电机停止');
    this.nativeGeneration++; clearInterval(this.nativeTimer);
    let nativeError;
    if(this.nativeMode && this.nativeLink) {
      const link = this.nativeLink;
      link.fail(new Error('控制已被停止取消'));
      try {await this.nativeQueue.run(async () => {if(this.nativeStartSent){this.event('motor_native_command',{action:5,value:0});await link.command(5);}});}
      catch(error) {nativeError = error;}
      finally {this.nativeLink = undefined; this.nativeMode = undefined; this.nativeSnapshot = undefined; this.nativeStartSent = false;}
    }
    this.session = 0n;
    for (const channel of this.channels) channel.reset();
    for (const pending of this.pending.values()) pending.reject(new Error('控制已被停止取消'));
    this.pending.clear();
    const id = random32(), threshold = now();
    await this.bus.send(CAN.stop + this.node,concat(new TextEncoder().encode('STOP'),pack('I',id)));
    await this.wait(() => this.stateAt > threshold && this.state.lastStopId === id && !(this.state.flags & 1) && [0,4].includes(this.state.state),2000,'STOP 未获新鲜输出关闭确认');
    if(nativeError)throw nativeError;
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
    const native = this.nativeMode && this.nativeSnapshot;
    if (native ? now()-this.nativeSnapshotAt > 1.5 : now()-this.stateAt > 1.5)
      throw new Error(native ? '原生控制状态反馈过期' : 'MBP 状态反馈过期');
    const f = this.sample.fields || {}, flags = this.sample.flags || {}, age = now() - this.sampleAt;
    const valid = id => f[id] !== undefined && flags[id] & 1 && age <= 1.5;
    const number = (id,scale = 1000) => valid(id) ? (f[id] | 0) / scale : null;
    const position = BigInt.asIntN(64,BigInt(f[7] || 0) << 32n | BigInt(f[6] || 0));
    const positionValid = Boolean(valid(6) && valid(7)), readiness = this.identity[O.readiness_flags] || 0;
    const modes = this.identity[O.control_modes], features = Number(this.identity[O.optional_features] || 0), fields = Number(this.identity[O.feedback_fields]);
    const iqMax = this.identity[O.iq_max], limit = this.identity[O.phase_protection_current], state = this.state;
    const nativeControl = this.node === 1 && this.identity[O.hardware_identity] === '455V1.3 + 5014';
    const raw = Object.fromEntries(Object.entries((this.maximum?.window || {}) as Record<string, [number, unknown]>).map(([id,[,value]]) => [id,value])) as Record<number, unknown> & {[K in 7 | 8 | 9 | 10 | 11]?: number};
    const snapshot = {motorProfile:profile.motor,profileProvenance:profile.provenance,wireProtocol:this.wire,sdkVersion:this.identity[O.sdk_version],version:this.identity[O.firmware_version],deviceUid:hex(this.identity[O.device_uid]),bootId:this.bootId.toString(),node:this.node,
      capabilities:{modes,fields,features,nativeControl,...controlCapabilities(this.identity,nativeControl?focProfiles['foc-5014'].current:null),maximumOutput:Boolean(modes & 1 << 5 && features & 1 && this.identity[O.available_iq] !== undefined && this.identity[O.active_limits] !== undefined),coherentSample:Boolean(features & 4)},
      dynamicLimits:{availableIqA:(this.identity[O.available_iq] || 0) / 1000,activeLimits:this.identity[O.active_limits]},limits:Object.fromEntries(Object.entries(this.identity).filter(([id]) => Number(id) >= 0x200 && Number(id) < 0x300)),
      readiness:{flags:readiness,detail:this.identity[O.readiness_detail],faultCategory:this.identity[O.fault_category],vendorCode:this.identity[O.vendor_fault_code],lastFaultEvent:this.lastFaultEvent},
      motor:{available:true,mode:state.mode,state:state.state,state_name:['DISABLED','STARTING','RUNNING','STOPPING','FAULT'][state.state],output_on:Boolean(state.flags & 1),fault:Boolean(state.flags & 4),fault_reason:this.identity[O.vendor_fault_code],updated_at:this.stateUtc,age_s:now() - this.stateAt},
      phaseCurrent:{ia_mA:number(16,1),ib_mA:number(17,1),ic_mA:number(18,1),stale:![16,17,18].every(valid),updated_at:this.sampleUtc},
      encoder:{valid:positionValid,stale:!positionValid,electrical_calibrated:Boolean(readiness & 4),electrical_angle_valid:Boolean(readiness & 32)},
      ntc:{temp_c:number(10),valid:Boolean(valid(10)),sourceDeviceTimeUs:valid(21) && valid(22) ? (BigInt(f[22]) << 32n | BigInt(f[21])).toString() : null,limit_c:this.identity[O.motor_temperature_trip] / 1000 || null,over:Boolean(readiness & 8),fault:!valid(10),tripped_latched:Boolean(readiness & 16 && this.identity[O.fault_category] === 3),age_s:age},
      feedback:{iq_a:number(2),id_a:number(4),iq_reference_a:number(3),speed_rpm:number(5),position_deg:positionValid ? Number(position) / 1000000 * 360 : null,positionMicroturns:positionValid ? position.toString() : null,deviceTimeUs:(BigInt(f[1] || 0) << 32n | BigInt(f[0] || 0)).toString(),sampleSeq:this.sample.seq,stale:!valid(2),position_stale:!positionValid,updated_at:this.sampleUtc,position_at:this.sampleUtc},
      control:{mode:state.mode,state:state.state,iq_target_ma:number(12,1),updated_at:this.sampleUtc},receivedAt:this.sampleUtc,limitA:limit ? limit / 1000 : null,supportedMaxA:iqMax ? iqMax / 1000 : null,
      maximumOutput:this.maximum ? {state:this.maximum.state,receivedAt:this.maximum.updated_at,updated_at:this.maximum.updated_at,schema:raw[1],windowId:raw[2],mcuTimeUs:raw[3],iqReferenceA:(raw[7] || 0) / 1000,idReferenceA:(raw[8] || 0) / 1000,operatingCurrentA:(raw[9] || 0) / 1000,voltageLimitV:this.identity[O.dq_voltage_limit] / 1000,flags:raw[6],iqMeanA:(raw[10] || 0) / 1000,idMeanA:(raw[11] || 0) / 1000,raw} : null};
    if (!native) return jsonValue(snapshot);
    const stamp = this.nativeSnapshotUtc, nativeAge = now()-this.nativeSnapshotAt;
    const fault = !!native.fault || native.state === 10, on = focRunning(native.state);
    const nativeState = fault ? 4 : on ? 2 : 0, mode = native.state === 17 ? 5 : native.state === 11 ? 1 : 0;
    const nativePositionValid = native.encoder_ok && native.calibrated;
    const readyFlags = (native.state === 8 && !fault && nativePositionValid && native.ntc_valid ? 1 : 0) |
      (native.encoder_ok ? 2 : 0) | (native.calibrated ? 4 : 0) | (nativePositionValid ? 32 : 0);
    const target = this.nativeMode === 'dyno' ? native.dyno_iq_target : this.nativeTargetA;
    return jsonValue({...snapshot,feedbackSource:'foc-5014',dynamicLimits:null,maximumOutput:null,
      readiness:{flags:readyFlags,detail:fault ? `原生驱动故障 ${native.fault}` : on ? '运行中' : '输出已关闭',faultCategory:null,vendorCode:native.fault,lastFaultEvent:null},
      motor:{available:true,mode,state:nativeState,state_name:fault ? 'FAULT' : on ? 'RUNNING' : 'DISABLED',output_on:on,fault,fault_reason:native.fault,updated_at:stamp,age_s:nativeAge},
      phaseCurrent:{ia_mA:null,ib_mA:null,ic_mA:null,stale:true,updated_at:null},
      encoder:{valid:nativePositionValid,stale:false,electrical_calibrated:native.calibrated,electrical_angle_valid:nativePositionValid},
      ntc:{temp_c:native.temperature,valid:native.ntc_valid,sourceDeviceTimeUs:null,limit_c:native.temperature_limit,over:native.temperature != null && native.temperature >= native.temperature_limit,fault:!native.ntc_valid,tripped_latched:false,age_s:nativeAge},
      feedback:{iq_a:native.iq,id_a:native.id,iq_reference_a:this.nativeMode === 'dyno' ? native.dyno_iq_target : null,speed_rpm:native.rpm,position_deg:nativePositionValid ? native.position_deg : null,positionMicroturns:null,deviceTimeUs:null,sampleSeq:null,stale:false,position_stale:!nativePositionValid,updated_at:stamp,position_at:stamp,vbus_v:native.vbus_v},
      control:{mode,state:nativeState,iq_target_ma:target*1000,updated_at:stamp},receivedAt:stamp});
  }
  async close() { clearInterval(this.heartbeatTimer); clearInterval(this.diagnosticTimer); this.fail(new Error('CAN connection closed')); if (this.bus) await this.bus.close(); }
}
