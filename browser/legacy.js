import metadata from '../.runtime/browser-native-data/legacy.json' with {type:'json'};
import {CanTransport,canInterface} from './transports.js';
import {bytes,concat,finite,now,pack,SerialQueue,sleep,utc,view} from './bytes.js';
import {FrameBuffer} from '../web/src/frameBuffer.js';
import {LegacyRecording} from './legacy-recording.js';
const s16 = n => n & 32768 ? n - 65536 : n;
const mod = (n,d) => (n % d + d) % d;
export class LegacyApp {
  epoch = 0; node = 0; lastRx = 0; tx = 0; rx = 0; errors = []; params = {}; queue = new SerialQueue(); telem = new FrameBuffer(4000); encoders = new FrameBuffer(4000); telemSeq = 0; encoderSeq = 0; samplesAt = []; pending = new Set();
  constructor(id,io,store,emit) {
    this.id = id; this.io = io; this.store = store; this.emit = emit; this.meta = metadata[id]; this.c = this.meta.constants;
    this.data = structuredClone(this.meta.emptyStatus); this.token = 'browser-direct'; this.profile = this.meta.profileDefault;
    this.recording = new LegacyRecording(store,() => this.status());
  }
  async initialize() {
    const selected = await this.store.get('motor-profile'); if (this.meta.profiles[selected]) this.profile = selected;
    this.outputUncertain = !!(await this.store.get('output-journal'))?.dirty;
    this.recoveryPending = this.outputUncertain;
    await this.recording.initialize();
    this.pollTimer = setInterval(() => {if (!this.polling && this.bus && !this.bus.closed && this.alive()) void this.poll();},1000);
    this.heartbeatTimer = setInterval(() => {if (this.armed && this.bus && !this.bus.closed) void this.bus.send(0x610 + this.node,Uint8Array.of(0x17,0,0,0,0,0,0,0)).catch(error => this.ioError(this.bus.device,error.message));},250);
    this.diagnosticTimer = setInterval(() => {
      if (this.armed && this.data.motor.mode === 7 && !this.maximumBusy) {
        this.maximumBusy = true;
        void this.maximumStatus().catch(error => {this.maximumError = error.message;}).finally(() => {this.maximumBusy = false;});
      }
    },250);
  }
  alive() { return this.lastIdentityAt && utc() - this.lastIdentityAt < this.c.DEVICE_SILENT_AFTER; }
  ioError(device,error) {if (device === this.bus?.device) {this.errors.push(error);this.armed = false;for (const item of this.pending) item.reject(new Error(error));this.pending.clear();}}
  receive(id,data) {
    if (id !== 0x590 + this.node && id !== 0x580 + this.node || !data.length) return;
    const stamp = utc(), source = view(data), code = data[0]; this.lastRx = stamp; this.rx++;
    this.recording.event('can_rx',{id,data:[...data]});
    if (id === 0x580 + this.node && data.length === 8) {
      if (code === 0x83) {Object.assign(this.data,{active_slot_raw:data[1],active_slot:['A','B'][data[1]] || '?',state:this.meta.otaStateNames[data[3]] || '?',try_left:data[4],version_raw:source.getUint16(5,true),version:'0x' + source.getUint16(5,true).toString(16).padStart(4,'0').toUpperCase()});this.lastIdentityAt = stamp;}
      if (code === 0x85) {Object.assign(this.data,{active_slot_raw:data[3],active_slot:['A','B'][data[3]] || '?',state:this.meta.otaStateNames[data[2]] || '?',try_left:data[6]});this.lastIdentityAt = stamp;}
      if (code === 0x87) {this.data.foc_loops = source.getUint32(1,true);this.data.foc_init_rc = source.getInt8(5);}
    } else if (id === 0x590 + this.node && data.length === 8) {
      if (code === 0x83) this.params[data[1]] = {value:source.getUint32(2,true),flags:data[6],t:stamp};
      else if (code === 0x86) {
        const state = data[2], flags = data[7], faultReport = this.id === 'sg-6010hb' && state === 3 && !!(flags & 32);
        this.data.motor = {available:true,mode:data[1],state,state_name:this.meta.motorStateNames[state] || 'unknown',step:faultReport ? null : data[3],step_ms:faultReport ? null : source.getUint16(4,true),fault_reason:faultReport ? data[3] : null,fault_peak_ma:faultReport ? source.getUint16(4,true) : null,duty_permille:data[6] * 10,output_on:!!(flags & 1),fault:!!(flags & 2),host_timeout:!!(flags & 8),supervised:!!(flags & 16),updated_at:stamp};
        if (this.id === 'rm-7010') {delete this.data.motor.fault_reason;delete this.data.motor.fault_peak_ma;}
        if (!this.data.motor.output_on) this.armed = false;
      } else if (code === 0xa5) this.data.control = {mode:data[1],state:data[2],calibration_stage:data[3],calibration_progress:data[4],iq_target_ma:source.getInt16(5,true),calibration_error:data[7],updated_at:stamp};
      else if (code === 0xa6) Object.assign(this.data.feedback,{id_a:source.getInt16(1,true) / 1000,iq_a:source.getInt16(3,true) / 1000,speed_rpm:source.getInt16(5,true) / (this.id === 'sg-6010hb' && data[7] & 128 ? 100 : 1),state:this.id === 'sg-6010hb' ? data[7] & 127 : data[7],updated_at:stamp});
      else if (code === 0xa7) Object.assign(this.data.feedback,{position_deg:source.getInt32(1,true) / 1000,position_at:stamp});
      else if (code === 0xa1) {
        this.telem.append([[++this.telemSeq,stamp,source.getInt16(1,true),source.getInt16(3,true),source.getInt16(5,true),data[7]]]);
        this.samplesAt.push(stamp);while (this.samplesAt.length && this.samplesAt[0] < stamp - this.c.TELEM_RATE_WINDOW) this.samplesAt.shift();
      } else if (code === 0xa2) {
        const temperature = source.getInt16(1,true), flags = data[5], valid = !!(flags & 1) && temperature !== -32768;
        this.ntc = {temp_c:valid ? temperature / 10 : null,temp_c_x10:temperature,raw:source.getUint16(3,true),valid,over:!!(flags & 2),tripped_latched:!!(flags & 4),fault:!!(flags & 8),limit_c:data[6],t:stamp};
        if (valid && !(flags & 2)) this.ntcClearAt ??= stamp;else this.ntcClearAt = null;
      } else if (code === 0xa4) this.decodeEncoder(data,stamp);
      else if (code === 0x90) this.diagStat = {rate_hz:source.getUint16(1,true),dropped:source.getUint16(3,true),overrun:source.getUint16(5,true),armed:!!(data[7] & 1),output_on:!!(data[7] & 2),sync_locked:!!(data[7] & 4),sync_fallback:!!(data[7] & 8),sync_code:data[7] >> 4,updated_at:stamp};
    }
    for (const item of this.pending) if (item.replyId === id && item.want.includes(code) && (!item.match || item.match(data))) {
      if (code === 0x82) item.reject(new Error(`驱动板拒绝命令：错误码 ${data[5]}，原因 ${data[6]}`));else item.resolve(data);
    }
  }
  decodeEncoder(data,stamp) {
    const source = view(data), raw = source.getUint16(1,true), angle = source.getUint16(3,true), flags = data[5], valid = !!(flags & 1) && raw < 16384 && angle < 36000;
    if (valid) {
      if (!this.encoderAnchor || now() - this.encoderAnchor.t > 1.5) {this.encoderAnchor = {raw,t:now()};this.encoderSpeed = null;}
      else if (now() - this.encoderAnchor.t >= .1) {this.encoderSpeed = (mod(raw - this.encoderAnchor.raw + 8192,16384) - 8192) * 60 / (16384 * (now() - this.encoderAnchor.t));this.encoderAnchor = {raw,t:now()};}
    } else {this.encoderAnchor = null;this.encoderSpeed = null;}
    this.data.encoder = {received:true,valid,raw:raw < 16384 ? raw : null,angle_cdeg:angle < 36000 ? angle : null,angle_deg:valid ? angle / 100 : null,speed_rpm:valid ? this.encoderSpeed : null,status:flags,status_text:valid ? '数据有效' : flags & 2 ? '奇偶校验错误' : flags & 4 ? '传感器错误' : flags & 8 ? 'SPI 超时' : '编码器数据无效',error_count:source.getUint16(6,true),updated_at:stamp};
    this.encoders.append([[++this.encoderSeq,stamp,valid ? raw : null,valid,this.data.control?.calibration_stage === 3,source.getUint16(6,true)]]);
  }
  exchange(data,want,{ota = false,match,timeout = 1500} = {}) {
    if (!this.bus || this.bus.closed) return Promise.reject(new Error('请先授权 USB-CAN 并连接'));
    const payload = new Uint8Array(8);payload.set(data);
    return new Promise((resolve,reject) => {
      const item = {replyId:(ota ? 0x580 : 0x590) + this.node,want,match,resolve,reject};this.pending.add(item);
      const timer = setTimeout(() => reject(new Error(`节点 ${this.node} 应答超时；动作不自动重发`)),timeout);
      item.resolve = value => {clearTimeout(timer);this.pending.delete(item);resolve(value);};item.reject = error => {clearTimeout(timer);this.pending.delete(item);reject(error);};
      this.tx++;void this.bus.send((ota ? 0x600 : 0x610) + this.node,payload).catch(item.reject);
    });
  }
  command(data,want = [0x81,0x82],options = {}) {
    const epoch = this.epoch;
    return this.queue.run(() => {if (epoch !== this.epoch) throw new Error('指令已被停止取消');return this.exchange(data,want,options);});
  }
  async connect() {
    if (this.data.motor.output_on) throw new Error('请先停止电机');
    if (this.bus && !this.bus.closed) await this.bus.close();
    const config = await this.io.call('can-config',{}), ports = await this.io.call('ports',{});
    const port = ports.find(port => port.port === config.device) || ports.find(canInterface);
    if (!port) throw new Error('请先授权 USB-CAN 适配器');
    if (!canInterface(port)) throw new Error('所选接口不是已支持的 CAN 适配器');
    this.node = finite(config.node ?? 0,'节点',0,63);this.lastIdentityAt = 0;
    if (!Number.isInteger(this.node)) throw new Error('CAN 节点必须为整数');
    this.bus = new CanTransport(this.io,(id,data) => this.receive(id,data));
    await this.bus.open({device:port.port,interface:canInterface(port),baud:500000});
    await this.command(Uint8Array.of(1),[0x83,0x82],{ota:true});
    await this.command(Uint8Array.of(3),[0x84,0x82]); await this.poll(); return {ok:true,channel:port.description};
  }
  async poll() {
    this.polling = true;
    try {
      await this.command(Uint8Array.of(1),[0x83,0x82],{ota:true});
      await this.command(Uint8Array.of(0x0c),[0x87,0x82],{ota:true});
      await this.command(Uint8Array.of(9),[0x86,0x82]);
      if (this.data.version_raw >= 0x0906) {await this.command(Uint8Array.of(0x14),[0xa5,0x82]);await this.command(Uint8Array.of(0x16,1),[0xa6,0x82]);}
      if (this.recording.live?.running && this.recording.live.outputSeen && !this.data.motor.output_on) await this.finishRecording('设备已结束输出');
    } catch (error) {this.errors.push(error.message);if (this.errors.length > 12) this.errors.shift();}
    finally {this.polling = false;}
  }
  status() {
    const status = structuredClone(this.data), timestamp = utc(), motor = status.motor;
    status.connected = !!this.bus && !this.bus.closed && !!this.alive();status.reason = status.connected ? `device answering on node ${this.node}` : '尚未连接设备';status.error = this.errors.at(-1) || null;
    status.adapter = {open:!!this.bus && !this.bus.closed,channel:this.bus?.device || null,bitrate:this.bus && !this.bus.closed ? 500000 : null,debug:''};
    status.node = this.node;status.host_id = '0x' + (0x600 + this.node).toString(16);status.device_id = '0x' + (0x580 + this.node).toString(16);status.last_rx_age = this.lastRx ? timestamp - this.lastRx : null;status.traffic = {tx:this.tx,rx:this.rx};
    if (motor.updated_at) {motor.age_s = timestamp - motor.updated_at;motor.available = motor.age_s < 5;}
    Object.assign(status.feedback,{stale:!status.feedback.updated_at || timestamp - status.feedback.updated_at > 1,position_stale:!status.feedback.position_at || timestamp - status.feedback.position_at > 1});
    status.params = this.meta.paramTable.map(spec => {
      const value = this.params[spec.id], row = {id:spec.id,name:spec.name,unit:spec.unit,value:value?.value ?? null,saved:!!(value?.flags & 1),is_default:!!(value?.flags & 2),min:spec.min,max:spec.max,default:spec.def,known:!!value};
      if (this.id === 'sg-6010hb' && (!status.version_raw || status.version_raw < 0x0a35)) {if (spec.id === 12) {row.max = 8000;row.default = 8000;}if (spec.id === 17) {row.max = 10000;row.default = 275;row.unit = 'mOhm equivalent';}if (spec.id === 18) {row.default = 600;row.unit = 'uH; provisional';}}
      if (this.id === 'sg-6010hb' && status.version_raw && status.version_raw < 0x0a36 && spec.id === 16) row.default = 200;
      return row;
    });
    status.motor_profile = {sel:this.profile,default:this.meta.profileDefault,options:Object.entries(this.meta.profiles).map(([key,value]) => ({key,label:value.label})),data:this.meta.profiles[this.profile]};
    if (this.ntc) {const {t,...ntc} = this.ntc,recovered = this.ntcClearAt == null ? null : timestamp - this.ntcClearAt;status.ntc = {...ntc,age_s:timestamp - t,recovered_s:recovered,tripped:ntc.tripped_latched && (recovered == null || recovered < 5)};}
    if (status.encoder) {
      const encoder = status.encoder;encoder.age_s = timestamp - encoder.updated_at;encoder.stale = encoder.age_s > .25;if (encoder.stale) encoder.speed_rpm = null;
      const pp = this.params[0]?.value, zero = this.params[1]?.value, direction = this.params[2]?.value;
      const calibrated = Number.isInteger(pp) && pp >= 1 && pp <= 64 && Number.isInteger(zero) && zero >= 0 && zero < 16384 && [1,2].includes(direction) && status.control?.calibration_stage === 3 && encoder.valid && !encoder.stale;
      Object.assign(encoder,{electrical_angle_valid:calibrated,electrical_calibrated:calibrated,pole_pairs:pp ?? null,electrical_angle_deg:calibrated ? mod((mod(encoder.raw - zero + 8192,16384) - 8192) * 360 / 16384 * pp * (direction === 2 ? -1 : 1),360) : null});
      if (this.id === 'rm-7010') delete encoder.pole_pairs;
      else if (!encoder.valid || encoder.stale) encoder.pole_pairs = null;
      if (encoder.valid && !encoder.stale && !calibrated) encoder.electrical_angle_note = '等待有效的编码器校准参数';
    }
    if (this.telem.length) {
      const newest = this.telem.at(this.telem.length - 1);status.telem = {ia_mA:newest[2],ib_mA:newest[3],ic_mA:newest[4],vbus_dV:newest[5],age_s:timestamp - newest[1],rate_hz:this.rate(),count:this.telemSeq,enable:status.telem_enable,period_ms:status.telem_period_ms,supported:true,stale:timestamp - newest[1] > this.c.TELEM_FRESH_AFTER,vbus_not_fitted:true,error:''};
    }
    status.ota = {state:'idle',error:null,output:[]};return status;
  }
  rate() {const stamps = this.samplesAt;if (stamps.length < 3 || stamps.at(-1) - stamps[0] < this.c.TELEM_RATE_MIN_SPAN) return null;return Math.round((stamps.length - 1) / (stamps.at(-1) - stamps[0]) * 10) / 10;}
  batch(since,encoderSince) {
    const cursor = since == null, rows = cursor ? this.telem.length ? [this.telem.at(this.telem.length - 1)] : [] : this.telem.after(since);
    const history = encoderSince == null || encoderSince > this.encoderSeq ? this.encoders.length ? [this.encoders.at(this.encoders.length - 1)] : [] : this.encoders.after(encoderSince);
    const pp = this.params[0]?.value, zero = this.params[1]?.value, direction = this.params[2]?.value, stage = this.data.control?.calibration_stage;
    const calibrated = Number.isInteger(pp) && pp >= 1 && pp <= 64 && Number.isInteger(zero) && zero >= 0 && zero < 16384 && [1,2].includes(direction) && stage === 3;
    return {ok:true,seq:this.telemSeq,dropped:since != null && !!this.telem.length && since + 1 < this.telem.at(0)[0],rate_hz:this.rate(),vbus_not_fitted:true,enable:this.data.telem_enable,period_ms:this.data.telem_period_ms,stale:this.status().telem?.stale || false,count:this.telemSeq,samples:rows.map(row => row.slice(2)),...(cursor ? {cursor:true} : {}),encoder_seq:this.encoderSeq,encoder_samples:history.map(([seq,t,raw,valid,wasCalibrated,errors]) => [seq,t,calibrated && wasCalibrated && valid ? mod((mod(raw - zero + 8192,16384) - 8192) * 360 / 16384 * pp * (direction === 2 ? -1 : 1),360) : null,raw,valid,errors]),encoder_dropped:encoderSince != null && !!this.encoders.length && encoderSince < this.encoders.at(0)[0] - 1,encoder_calibration_key:[pp,zero,direction,stage],encoder_time_source:'host CAN receive time; separate from current telemetry'};
  }
  async stopped() {
    const frame = await this.command(Uint8Array.of(9),[0x86,0x82]);
    if (frame[7] & 1) throw new Error('请先停止输出再修改或保存控制参数');
  }
  async confirmOutput(on,mode) {
    const began = utc(), deadline = now() + 2;
    while (now() < deadline) {
      await this.command(Uint8Array.of(9),[0x86,0x82]);
      const motor = this.data.motor;
      if (motor.updated_at >= began && motor.output_on === on && (!on || motor.mode === mode && [1,2].includes(motor.state))) return;
      if (on && motor.fault) throw new Error('设备启动触发保护');await sleep(50);
    }
    throw new Error('没有新鲜的电机输出状态确认');
  }
  async stop() {
    this.epoch++;this.armed = false;
    for (const item of this.pending) item.reject(new Error('控制已被停止取消'));this.pending.clear();
    if (this.stopTask) return this.stopTask;
    this.stopTask = (async () => {
      try {
        await this.exchange(Uint8Array.of(8,0,0,0,0,0,0,0),[0x81,0x82]);await this.confirmOutput(false);
        await this.store.put('output-journal',{dirty:false});this.outputUncertain = false;this.recoveryPending = false;this.emit('activity',{active:false});
        await this.finishRecording('操作者停止，设备关闭已确认');return {ok:true,enable:0,stop_confirmed:true};
      }
      finally {this.stopTask = null;}
    })();return this.stopTask;
  }
  async controlSet(name,value) {
    const spec = this.meta.controlFields[name];if (!spec) throw new Error('未知控制字段');
    let scaled = finite(value,name,spec[2],spec[3]);
    if (name === 'position_deg') scaled *= this.meta.profiles[this.profile].nameplate.reduction_ratio || 1;
    const raw = Math.round(scaled * spec[1]);finite(raw,name,-2147483648,2147483647);
    await this.command(concat(Uint8Array.of(0x12,spec[0]),pack('i',raw),Uint8Array.of(0,0)));
    return {ok:true,field:name,value,raw};
  }
  async banks(first,last) {
    const words = [];
    for (let bank = first; bank <= last; bank++) {const reply = await this.command(Uint8Array.of(0x0d,4,bank,0,0),[0x91,0x82],{match:data => data[0] === 0x82 || data[1] === bank});words.push(...[2,4,6].map(offset => view(reply).getUint16(offset,true)));}
    return words;
  }
  assist(w) {return {stage:w[0],stage_name:['IDLE','FORCE','BLEND','DONE'][w[0]] || '?',travel_counts:s16(w[1]),flags:w[2],voltage_saturated:!!(w[2] & 1),iq_limited:!!(w[2] & 2),speed_loop_saturated:!!(w[2] & 4),position_loop_saturated:!!(w[2] & 8),theta_deg:w[3] / 10,speed_ref_rpm:s16(w[4]),speed_integral_a:s16(w[5]) / 1000};}
  async request(path,method,body,headers = {}) {
    const url = new URL(path,'https://browser.invalid'), route = url.pathname, q = Object.fromEntries(url.searchParams);
    if (route === '/api/session') return {token:this.token,command_epoch:this.epoch};
    if (route === '/api/status') return this.status();
    if (route === '/api/alive') return {ok:true};
    if (route === '/api/telem/batch') return this.batch(q.since == null ? null : Number(q.since),q.encoder_since == null ? null : Number(q.encoder_since));
    if (route === '/api/browser-recordings') return {records:await this.recording.history()};
    if (route === '/api/browser-recordings/file') return this.recording.file(q.id,q.format);
    if (this.meta.mutatingRoutes.includes(route)) {
      if (method !== 'POST' || headers['x-control-token'] !== this.token) throw new Error('控制令牌或请求方法无效');
      if (route === '/api/shutdown') {
        if (this.bus && !this.bus.closed) {await this.stop();await this.bus.close();}
        return {ok:true,stop_confirmed:true};
      }
      if (route === '/api/motor' && Number(q.enable) === 0 || route === '/api/control/calibrate' && q.op === 'abort') return this.stop();
      if (Number(headers['x-command-epoch']) !== this.epoch) throw new Error('指令已被停止取消，请重新操作');
    }
    const enables = route === '/api/motor' && Number(q.enable) === 1 || route === '/api/phase' || route === '/api/control/calibrate';
    if (enables) {
      if (this.recoveryPending) throw new Error('上次输出尚未确认关闭，请连接原驱动板并停止确认');
      const epoch = this.epoch;
      await this.store.put('output-journal',{dirty:true});
      if (epoch !== this.epoch) throw new Error('开启已被停止取消');
      this.outputUncertain = true;this.emit('activity',{active:true});
    }
    this.recording.event('request',{route,q});
    if (route === '/api/reconnect') return this.connect();
    if (route === '/api/motor_profile') {
      if (q.sel != null) {if (!this.meta.profiles[q.sel]) throw new Error('未知电机档案');this.profile = q.sel;await this.store.put('motor-profile',this.profile);}
      return {ok:true,...this.status().motor_profile};
    }
    if (route === '/api/ota/status') return {state:'idle',error:null,output:[]};
    if (route === '/api/ota/onekey') throw new Error('纯网页不执行本机编译工具链；本次未进行固件升级');
    if (route === '/api/getall') {await this.command(Uint8Array.of(3),[0x84,0x82]);return {...this.status(),ok:true};}
    if (route === '/api/get') {
      const id = Number(q.id);await this.command(Uint8Array.of(1,id),[0x83,0x82],{match:data => data[0] === 0x82 || data[1] === id});
      return {ok:true,...this.status().params.find(param => param.id === id)};
    }
    if (['/api/set','/api/save','/api/load'].includes(route)) {
      await this.stopped();
      if (route === '/api/set') {
        const id = Number(q.id), spec = this.status().params.find(param => param.id === id);if (!spec) throw new Error('未知参数');
        const value = finite(q.value,spec.name,spec.min,spec.max);if (!Number.isInteger(value)) throw new Error('参数值必须为整数');
        await this.command(concat(Uint8Array.of(2,id),pack('I',value),Uint8Array.of(0,0)),[0x83,0x82],{match:data => data[0] === 0x82 || data[1] === id});
        return {ok:true,...this.status().params.find(param => param.id === id)};
      }
      await this.command(Uint8Array.of(route === '/api/save' ? 4 : 5));if (route === '/api/save') await this.command(Uint8Array.of(3),[0x84,0x82]);return {ok:true};
    }
    if (route === '/api/control/set') return this.controlSet(q.field,Number(q.value));
    if (route === '/api/control/status') {await this.command(Uint8Array.of(9),[0x86,0x82]);await this.command(Uint8Array.of(0x14),[0xa5,0x82]);if (this.data.version_raw >= 0x0906) await this.command(Uint8Array.of(0x16,1),[0xa6,0x82]);return {ok:true,control:this.data.control,motor:this.status().motor,feedback:this.status().feedback,error:null};}
    if (route === '/api/control/calibrate') {if (!this.alive()) throw new Error('驱动板未应答');await this.command(Uint8Array.of(0x13,1));await this.confirmOutput(true,254);this.armed = true;return {ok:true};}
    if (route === '/api/telem') {const period = Math.round(finite(q.period_ms ?? this.data.telem_period_ms,'遥测周期',this.c.TELEM_PERIOD_MIN,this.c.TELEM_PERIOD_MAX)), enable = Number(q.enable) ? 1 : 0;await this.command(concat(Uint8Array.of(6,enable),pack('H',period),new Uint8Array(4)));this.data.telem_enable = enable;this.data.telem_period_ms = period;return {ok:true,enable,period_ms:period};}
    if (route === '/api/cur') {const ma = Math.min(this.meta.profiles[this.profile].safe_cur_ma_max,20000,finite(q.ma,'电流',0)), seconds = finite(q.seconds || 0,'时间',0,255);await this.command(concat(Uint8Array.of(0x0b),pack('H',ma),Uint8Array.of(seconds,0,0,0,0)));return {ok:true,cur_ma:ma,seconds};}
    if (route === '/api/phase') {const sa = finite(q.sa,'sa',0,2),sb = finite(q.sb,'sb',0,2),sc = finite(q.sc,'sc',0,2),duty = Math.round(finite(q.duty || 60,'duty',0,1000)),seconds = finite(q.seconds || 1,'时间',1,10);await this.command(concat(Uint8Array.of(0x0a,sa,sb,sc),pack('H',duty),Uint8Array.of(seconds,0)));await this.confirmOutput(true,255);return {ok:true,sa,sb,sc,duty_permille:duty,seconds};}
    if (route === '/api/brake') {await this.command(Uint8Array.of(0x0c,Number(q.release) ? 1 : 0));return {ok:true};}
    if (route === '/api/motor') return this.startMotor(q);
    if (route === '/api/diag/assist') return {ok:true,...this.assist(await this.banks(19,20))};
    if (route === '/api/diag/raw') return this.rawDiagnostics();
    if (route === '/api/max-output/status') return q.cached === '1' ? {ok:true,maximumOutput:this.maximum || null,diagnosticError:this.maximumError || null} : {ok:true,maximumOutput:await this.maximumStatus()};
    if (['/api/diag/stat','/api/diag/arm','/api/diag/off'].includes(route)) {const op = route.endsWith('/stat') ? 3 : route.endsWith('/arm') ? 1 : 0,n = Math.round(finite(q.n || 1024,'样本数',1,1024)),decim = Math.round(finite(q.decim || 50,'抽取',4,200));await this.command(Uint8Array.of(0x0d,op,n & 255,n >> 8,decim),[0x81,0x82,0x90]);return {ok:true,stat:this.diagStat || {},armed:n,decim};}
    if (route === '/api/diag/dump') {if (!this.dumping) this.dumping = this.dump().finally(() => {this.dumping = null;});return {ok:true,dump:this.diagDump || {state:'reading'}};}
    throw new Error('未知接口：' + route);
  }
  async startMotor(q) {
    if (!this.alive()) throw new Error('驱动板没有新鲜应答，未发送启动');
    const epoch = this.epoch, mode = finite(q.mode ?? this.c.RPM_MODE_DEFAULT,'模式',0,this.c.CONTROL_MODE_MAX_WEB), seconds = finite(q.seconds || 0,'时间',0,255), profile = this.meta.profiles[this.profile];
    let step = 0,duty = 0,volt = 0,cur = 0,rpm = 0,vbus = 0;
    if (!mode) {
      vbus = finite(q.vbus ?? 48,'母线电压',1,60);step = Math.round(Number(q.step_ms || 500));
      const asked = q.rpm ? Math.max(this.c.RPM_MIN,Math.min(this.c.RPM_MAX_SAFE,Number(q.rpm))) : 0, pp = profile.nameplate.pole_pairs || 10;
      if (asked) step = Math.max(5,Math.min(5000,Math.round(10000 / (asked * pp))));finite(step,'开环步进时间',5,5000);
      rpm = Math.round(10000 / (step * pp));volt = finite(q.volt ?? (asked ? profile.safe_volt : this.motorVoltage || 1),'电压',.1,this.c.MOTOR_VOLT_MAX);
      cur = q.cur_ma ? Math.min(Number(q.cur_ma),profile.safe_cur_ma_max) : asked ? profile.safe_cur_ma_default : 0;
      duty = Math.max(1,Math.min(this.c.MOTOR_DUTY_MAX,Math.round(volt / vbus * 1000)));
    }
    try {
      await this.recording.start(mode,seconds);
      this.runGeneration = (this.runGeneration || 0) + 1;
      await this.command(concat(Uint8Array.of(8,1,mode),pack('HH',step,duty),Uint8Array.of(seconds)));
      if (epoch !== this.epoch) throw new Error('启动已被停止取消');
      await this.confirmOutput(true,mode);
      this.recording.live.outputSeen = true;
      if (cur > 0) await this.command(concat(Uint8Array.of(0x0b),pack('H',Math.min(cur,20000)),Uint8Array.of(seconds,0,0,0,0)));
      if (epoch !== this.epoch) throw new Error('启动已被停止取消');
      this.armed = true;this.maximum = null;if (duty) this.motorVoltage = duty / 1000 * vbus;
      return {ok:true,enable:1,mode,seconds,volt:duty / 1000 * vbus,vbus,duty_permille:duty,step_ms:step,rpm,cur_ma:cur,rpm_max_safe:this.c.RPM_MAX_SAFE};
    } catch (error) {try {await this.stop();} catch {}throw error;}
  }
  async rawDiagnostics() {
    const w = await this.banks(0,23), u32 = i => (w[i] | w[i + 1] << 16) >>> 0;
    return {ok:true,raw:w.slice(0,4),offset:w.slice(4,8),dma_observed:w.slice(8,12),seq:u32(12),acquisition_irq_count:u32(14),dma_remaining:w[16],adc_dat:w[17],adc_ctrl2:u32(18),sync_mode:w[20],ntc_raw_min:w[21],ntc_raw_max:w[22],ntc_rail_count_mod65536:w[23],arm_failure_registers:w.slice(24,33),launch_cnt:w[33],publication_cnt:w[34],launch_down:!!w[35],motor_fault_reason:w[36],motor_fault_peak_ma:w[37],motor_ocp_ma:w[38],pwm:{ccen:w[39],bkdt:w[40],ccmod1:w[41],ccmod2:w[42],ccr:w.slice(43,46),remap:u32(46),gpioa_pl:u32(48),gpioa_ph:u32(50),gpiob_pl:u32(52),gpioa_input:w[54],gpiob_input:w[55],cnt:w[56]},assist:this.assist(w.slice(57,63)),control_diag:{updates:u32(63),overruns:w[65],last_cycles:w[66],max_cycles:w[67],last_us:w[66] / 128,max_us:w[67] / 128,pll_rpm:s16(w[68]),id_ma:s16(w[69]),iq_ma:s16(w[70]),meas_rpm:s16(w[71])}};
  }
  async finishRecording(reason) {
    const report = this.recording.live;
    if (!report?.running) return;
    await this.recording.finish(reason);
    if (report.mode === 7 && this.data.version_raw >= 0x0a3c) {
      const generation = this.runGeneration;
      void this.exportTrace(report.id,generation).catch(error => {this.errors.push('冻结轨迹保存失败：' + error.message);});
    }
  }
  async exportTrace(id,generation) {
    const trace = {schema:1,rows:[],complete:false,sampleRateHz:2000,note:'MCU frozen data; last 128 ms at most'}, request = async (op,a = 0,b = 0) => {
      if (generation !== this.runGeneration) throw new Error('新试验已开始，旧轨迹导出停止');
      return this.command(Uint8Array.of(0x0d,op,a,b,0),[0x91,0x82]);
    };
    try {
      const header = await request(5);
      if (header[1] !== 5 || header[4] !== 40) throw new Error('冻结轨迹格式不匹配');
      trace.expectedRows = Math.min(256,view(header).getUint16(2,true));
      for (let index = 0; index < trace.expectedRows; index++) {
        const selected = await request(6,index & 255,index >> 8);
        if (selected[1] !== 6 || view(selected).getUint16(2,true) !== index) throw new Error('冻结轨迹索引不匹配');
        const fragments = [];
        for (let chunk = 0; chunk < 7; chunk++) {
          const reply = await request(7,chunk);
          if (reply[1] !== chunk) throw new Error('冻结轨迹分片不匹配');
          fragments.push(reply.slice(2));
        }
        const source = view(concat(...fragments)),w = Array.from({length:20},(_,i) => source.getUint16(i * 2,true));
        trace.rows.push([(w[0] | w[1] << 16) >>> 0,...w.slice(2,6),...w.slice(6,12).map(n => s16(n) / 1000),(w[12] | w[13] << 16) >>> 0,w[14],s16(w[15]),(w[16] | w[17] << 16) >>> 0,w[18],w[19]]);
      }
      trace.complete = true;
    } catch (error) {trace.error = error.message;}
    await this.store.put('trace:legacy:' + id,trace);
  }
  async maximumStatus() {
    if (this.maximum && utc() - this.maximum.receivedAt < .75) return this.maximum;
    if ((this.data.version_raw || 0) < 0x0a3c) throw new Error('最大输出模式需要 0x0A3C 或更新固件');
    const w = await this.banks(39,47);if (w[0] !== 1) throw new Error('maximum-output diagnostic schema unavailable');
    const flags = w[1], result = {schema:1,flags,limits:Object.entries({1:'current',2:'voltage',4:'source_estimate',8:'speed',16:'adc_range'}).filter(([bit]) => flags & Number(bit)).map(([,label]) => label),quality:Object.entries({256:'magnetic_diagnostic_missing',512:'magnetic_diagnostic_flags',1024:'sampling_window_unverified',2048:'adc_generation_gap'}).filter(([bit]) => flags & Number(bit)).map(([,label]) => label),samples:w[2],windowMs:w[2] / 10,mcuTimeUs:(w[3] | w[4] << 16) >>> 0,idReferenceA:s16(w[5]) / 1000,iqReferenceA:s16(w[6]) / 1000,operatingCurrentA:w[7] / 1000,busLimitA:w[8] / 1000,voltageLimitV:w[9] / 1000,iqMeanA:s16(w[10]) / 1000,idMeanA:s16(w[11]) / 1000,iqErrorRmsA:w[12] / 1000,idErrorRmsA:w[13] / 1000,peakPhaseA:w[14] / 1000,saturatedFraction:w[15] / 1000,launchCnt:w[16],windowCcrA:w[17],sampleMarginTicks:s16(w[18]),pwmGeneration:(w[19] | w[20] << 16) >>> 0,magneticWord:w[21],magneticMagnitude:w[22],magneticReadErrors:w[23],minAdcRaw:w[24],estimatedBusA:w[25] / 1000,windowId:w[26],receivedAt:utc(),boundaryMeaning:'configured observable operating boundary; physical rating/calibration not certified'};
    const tolerance = Math.max(.3,.05 * Math.max(Math.abs(result.iqReferenceA),Math.abs(result.idReferenceA)));
    const tracking = result.iqErrorRmsA <= tolerance && result.idErrorRmsA <= tolerance && Math.abs(result.iqMeanA - result.iqReferenceA) <= tolerance;
    this.maximum = {...result,trackingToleranceA:tolerance,trackingWithinTolerance:tracking,configuredBoundaryObserved:!!(flags & 15) && tracking && !result.quality.length};
    this.maximumError = ''; return this.maximum;
  }
  async dump() {
    const chunks = [];
    try {
      for (let fragment = 0; fragment < 40000; fragment++) {
        let reply;try {reply = await this.command(Uint8Array.of(0x0e,fragment & 255,fragment >> 8),[0x8f,0x82]);} catch (error) {if (error.message.includes('错误码')) break;throw error;}
        if (view(reply).getUint16(1,true) !== fragment) throw new Error('诊断分片序号不匹配');chunks.push(reply.slice(3));
      }
      const data = concat(...chunks), rows = [];
      for (let offset = 0; offset + 32 <= data.length; offset += 32) {
        const source = view(data.slice(offset,offset + 32));let at = 0;
        const formats = ['I','I','I','h','h','h','H','H','H','H','B','B','H','H'];
        rows.push(formats.map(type => {const value = type === 'I' ? source.getUint32(at,true) : type === 'H' ? source.getUint16(at,true) : type === 'h' ? source.getInt16(at,true) : source.getUint8(at);at += type === 'I' ? 4 : type === 'B' ? 1 : 2;return value;}));
      }
      const body = 'seq,t_us,seq_acq,ia,ib,ic,ca,cb,cc,cnt1,flags,vsrc,vbus_dv,step_ms\n' + rows.map(row => row.join(',')).join('\n');
      await this.store.put('diag-capture',{body});this.diagDump = {ok:true,records:rows.length,frags:chunks.length,path:'browser IndexedDB / diag_capture.csv'};
      this.emit('download',{name:'diag_capture.csv',type:'text/csv',body});
    } catch (error) {this.diagDump = {ok:false,error:error.message};}
  }
}
