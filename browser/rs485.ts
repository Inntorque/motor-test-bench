import {ModbusRtu} from './transports.ts';
import {bytes, concat, crc16, finite, hex, now, SerialQueue, sleep, utc} from './bytes.ts';
import {FrameBuffer} from '../web/src/frameBuffer.ts';
export function supplyConfig(key, data) {
  const motor = key === 'motor-supply', protocol = motor ? 'ru' : 'ssj';
  if (data.protocol && data.protocol !== protocol) throw new Error(motor ? '电机电源只支持万瑞达 RU' : '制动电源使用 SSJ');
  const config = {...data,protocol,transport:data.transport || 'serial',baud:finite(data.baud || 9600,'baud',1200,128000),address:finite(data.address || 1,'address',1,protocol === 'ru' ? 255 : 247),stopbits:Number(data.stopbits || 1),parity:data.parity || 'N',
    voltageDecimals:Number(data.voltageDecimals ?? 2),currentDecimals:Number(data.currentDecimals ?? (motor ? 2 : 3)),maxVoltage:24,maxCurrent:motor ? 20 : 2.3};
  if (![1,2].includes(config.stopbits) || !['N','E','O'].includes(config.parity) || !Number.isInteger(config.address)) throw new Error('串口格式或地址无效');
  if (motor && (config.stopbits !== 1 || config.parity !== 'N')) throw new Error('RU 要求 8N1');
  if (!motor && (config.voltageDecimals !== 2 || config.currentDecimals !== 3)) throw new Error('SSJ 电压小数位固定 2，电流小数位固定 3');
  for (const key of ['ratedVoltage','ratedCurrent']) config[key] = data[key] === '' || data[key] == null ? null : finite(data[key],key,0,65535);
  config.measuredCurrentDecimals = config.currentDecimals; config.ratingsVerified = false;
  return config;
}
export function powerProblem(key, config, snapshot) {
  const status = snapshot.status, measured = snapshot.measured, settings = snapshot.settings;
  if (config.protocol === 'ssj' ? status & 12 : Object.values(snapshot.ruStatus || {}).slice(2).some(Boolean)) return '设备报告 OVP/OCP/OTP 保护';
  if (!(status & 128)) return '';
  const sv = 10 ** config.voltageDecimals, sa = 10 ** config.currentDecimals, sm = 10 ** config.measuredCurrentDecimals;
  const cap = key === 'motor-supply' ? 20 : 2.3;
  if (Math.abs(settings[1] - 24 * sv) > 1) return '电源设定电压不是 24 V';
  if (settings[2] < 0 || settings[2] / sa > cap + 1 / sa + 1e-9) return '电源设定限流超出工作台限幅';
  if (measured[0] < 0 || measured[1] < 0 || measured[0] / sv > 26.4 || measured[1] / sm > cap * (key === 'motor-supply' ? 1 : 1.1)) return '电源实测读回超出原工作台边界';
  return '';
}
export class PowerChannel {
  declare error;
  declare io;
  declare issue: string | undefined;
  declare key;
  declare powerCheck;
  declare transport: ModbusRtu | undefined;

  config = null; snapshot = null; connected = false; outputUncertain = false; epoch = 0; queue = new SerialQueue();
  constructor(key, io) { this.key = key; this.io = io; }
  status() {
    const stale = Boolean(this.snapshot && utc() - this.snapshot.receivedAt > 3.5);
    return {connected:!!this.connected && !stale,config:this.config,snapshot:this.snapshot,stale,outputUncertain:this.outputUncertain,powerCheck:this.powerCheck || null,error:this.error || ''};
  }
  async connect(data) {
    if (this.outputUncertain) throw new Error('原电源输出关闭未确认，不能更换连接');
    if (this.transport) await this.transport.close();
    this.config = supplyConfig(this.key,data); this.connected = false;
    this.transport = new ModbusRtu(this.io);
    try { await this.transport.open(this.config); await this.read(); this.connected = true; return this.status(); }
    catch (error) { await this.transport.close(); this.config = null; throw error; }
  }
  async readRaw() {
    if (!this.transport || !this.config) throw new Error('电源未连接');
    const ex = this.transport.exchange.bind(this.transport) as ModbusRtu['exchange'], config = this.config;
    let snapshot;
    if (config.protocol === 'ru') {
      if (!config.ratingsVerified) {
        const decimals = await ex(4,106,2), ratings = await ex(4,103,2);
        if (decimals.some(n => n < 0 || n > 4) || ratings.some(n => n <= 0)) throw new Error('RU 返回无效额定值或小数位');
        Object.assign(config,{voltageDecimals:decimals[0],currentDecimals:decimals[1],measuredCurrentDecimals:decimals[1],ratedVoltage:ratings[0] / 10 ** decimals[0],ratedCurrent:ratings[1] / 10 ** decimals[1],ratingsVerified:true});
        if (config.maxVoltage > config.ratedVoltage || config.maxCurrent > config.ratedCurrent) throw new Error('工作台限幅超过 RU 额定值');
      }
      const measured = await ex(4,100,2), [output] = await ex(1,133), [cc,cv] = await ex(2,116,2), [ovp,ocp,otp] = await ex(2,120,3);
      const settings = await ex(3,149,2), protection = await ex(3,157,2), enabled = await ex(1,136,2);
      const sv = 10 ** config.voltageDecimals, sa = 10 ** config.currentDecimals;
      snapshot = {measured:[...measured,measured[0] / sv * measured[1] / sa],powerCalculated:true,status:output ? 128 : 0,settings:[config.address,...settings],ruStatus:{cc,cv,ovp,ocp,otp},protection:{voltage:protection[0] / sv,current:protection[1] / sa,ovpEnabled:enabled[0],ocpEnabled:enabled[1]}};
    } else {
      const measured = await ex(4,1000,3), [status] = await ex(4,1007), settings = await ex(3,2000,6);
      snapshot = {measured,status,settings,calculatedPowerW:null,powerCalculated:false};
    }
    return {...snapshot,connected:true,config:{...config},logs:[],receivedAt:utc()};
  }
  read() {
    return this.queue.run(async () => {
      const initial = await this.readRaw(); let issue = powerProblem(this.key,this.config,initial), snapshot = initial;
      if (issue) {
        snapshot = await this.readRaw(); const confirmed = powerProblem(this.key,this.config,snapshot);
        this.powerCheck = {state:confirmed ? 'confirmed' : 'recovered',first:initial,second:snapshot,issue:confirmed ? {message:confirmed} : null,firstIssue:{message:issue}}; issue = confirmed;
      }
      this.connected = true; this.snapshot = snapshot; this.outputUncertain = Boolean(snapshot.status & 128); this.issue = issue;
      return this.status();
    });
  }
  validateValues(voltage, current) {
    const c = this.config;
    voltage = finite(voltage,'电压',0,Math.min(c.maxVoltage,c.ratedVoltage ?? Infinity));
    current = finite(current,'电流',0,Math.min(c.maxCurrent,c.ratedCurrent ?? Infinity));
    const raw = [Math.round(voltage * 10 ** c.voltageDecimals),Math.round(current * 10 ** c.currentDecimals)];
    if (raw.some(n => n > 65535) || raw[0] / 10 ** c.voltageDecimals > Math.min(c.maxVoltage,c.ratedVoltage ?? Infinity) || raw[1] / 10 ** c.currentDecimals > Math.min(c.maxCurrent,c.ratedCurrent ?? Infinity)) throw new Error('数值按电源精度取整后超过限幅');
    return raw;
  }
  async writeValues(voltage, current) {
    const raw = this.validateValues(voltage,current), register = this.config.protocol === 'ru' ? 149 : 2001;
    await this.transport.exchange(16,register,raw.length,raw);
    const actual = await this.transport.exchange(3,register,2);
    if (actual.some((value,index) => value !== raw[index])) throw new Error('V/A 读回与设定不一致');
  }
  setValues(data) {
    const epoch = this.epoch;
    return this.queue.run(async () => {
      if (epoch !== this.epoch) throw new Error('操作已被停止取消');
      await this.writeValues(data.voltage,data.current);
      this.snapshot = await this.readRaw(); return this.snapshot;
    });
  }
  output(enabled, data: {voltage?: number; current?: number} = {}) {
    if (typeof enabled !== 'boolean') throw new Error('输出开关参数无效');
    if (!enabled) this.epoch++;
    const epoch = this.epoch;
    return this.queue.run(async () => {
      if (epoch !== this.epoch) throw new Error('操作已被停止取消');
      if (enabled) {
        const fresh = this.snapshot = await this.readRaw(), c = this.config;
        if (powerProblem(this.key,c,fresh)) throw new Error(powerProblem(this.key,c,fresh));
        const raw = this.validateValues(data.voltage,data.current);
        if (this.key === 'motor-supply') {
          if (fresh.settings.slice(1,3).some((value,index) => Math.abs(value - raw[index]) > 1)) throw new Error(`电机电源预设不匹配，请在面板设为 ${data.voltage} V / ${data.current} A；软件未改写预设`);
        } else await this.writeValues(data.voltage,data.current);
        if (epoch !== this.epoch) throw new Error('开启已被停止取消');
        this.outputUncertain = true;
      }
      if (this.config.protocol === 'ru') await this.transport.exchange(5,133,1,[enabled]);
      else await this.transport.exchange(16,2016,1,[enabled ? 65535 : 0]);
      this.snapshot = await this.readRaw(); this.outputUncertain = Boolean(this.snapshot.status & 128);
      if (this.outputUncertain !== enabled) throw new Error('电源输出状态读回与指令不一致');
      return this.snapshot;
    });
  }
  async disconnect() {
    if (this.outputUncertain) throw new Error('请先关闭电源输出并确认');
    await this.transport?.close(); this.connected = false; this.config = null; this.snapshot = null; return this.status();
  }
}
const crc0 = data => {
  let crc = 0;
  for (const byte of data) { crc ^= byte; for (let i = 0; i < 8; i++) crc = crc >> 1 ^ (crc & 1 ? 0xa001 : 0); }
  return Uint8Array.of(crc & 255,crc >> 8);
};
export class HexParser {
  declare order;
  declare pending;
  declare profile;
  declare size: number;

  buffer = new Uint8Array(); skipped = 0; streak = 0;
  constructor(protocol) { this.size = protocol === 'hex6' ? 6 : 8; }
  feed(chunk) {
    this.buffer = concat(this.buffer,chunk); const result: [number, number, string][] = [];
    while (this.buffer.length >= this.size) {
      const frame = this.buffer.slice(0,this.size); let signature;
      for (const [profile,checksum] of [['modbus-ffff',crc16],['ibm-0000',crc0]] as const) {
        if (this.profile && this.profile !== profile || profile === 'ibm-0000' && !this.profile && !frame.slice(0,-2).some(Boolean)) continue;
        const crc = checksum(frame.slice(0,-2)), order = hex(crc) === hex(frame.slice(-2)) ? 'low-first' : hex(crc.slice().reverse()) === hex(frame.slice(-2)) ? 'high-first' : null;
        if (order && (!this.order || this.order === order)) { signature = `${profile}/${order}`; break; }
      }
      if (!signature) { this.buffer = this.buffer.slice(1); this.skipped++; this.streak = 0; this.pending = null; continue; }
      this.buffer = this.buffer.slice(this.size);
      if (this.pending !== signature) this.streak = 0;
      this.pending = signature; this.streak++;
      if (this.streak >= 3) [this.profile,this.order] = signature.split('/');
      let torque, speed;
      if (this.size === 6) { torque = (frame[0] << 8 | frame[1]) * (frame[2] & 128 ? -1 : 1); speed = (frame[2] & 127) << 8 | frame[3]; }
      else { torque = frame[0] << 16 | frame[1] << 8 | frame[2]; if (torque & 0x800000) torque -= 0x1000000; speed = frame[3] << 16 | frame[4] << 8 | frame[5]; }
      result.push([torque,speed,hex(frame)]);
    }
    return result;
  }
}
export class TorqueSensor {
  declare error;
  declare frameTimer: number | undefined;
  declare io;
  declare observe;
  declare opened: boolean | undefined;
  declare parser: HexParser | null | undefined;
  declare pendingFrames: [number, number, number, number][] | undefined;
  declare rate: number | undefined;
  declare rateAt: number | undefined;
  declare rateCount: number | undefined;
  declare remove;
  declare transport: ModbusRtu | undefined;

  config = null; snapshot = null; connected = false; frames = new FrameBuffer(60000); sequence = 0;
  constructor(io, observe) { this.io = io; this.observe = observe; }
  status() { return {connected:this.connected && !!this.snapshot && utc() - this.snapshot.receivedAt < (this.parser ? 1 : 3.5),config:this.config,snapshot:this.snapshot,error:this.error || ''}; }
  async connect(data) {
    await this.disconnect();
    this.config = {...data,transport:'serial',protocol:data.protocol || 'modbus',baud:finite(data.baud || 19200,'baud',1200,115200),stopbits:Number(data.stopbits || 2),address:Number(data.address || 1),parity:'N'};
    if (!this.config.device || ![1,2].includes(this.config.stopbits) || !['modbus','hex6','hex8'].includes(this.config.protocol) || !Number.isInteger(this.config.address) || this.config.address < 1 || this.config.address > 120) throw new Error('传感器串口参数无效');
    this.frames.clear(); this.sequence = 0;this.pendingFrames = [];
    try {
      if (['hex6','hex8'].includes(this.config.protocol)) {
        this.config.torqueDecimals = finite(data.torqueDecimals ?? 2,'扭矩小数位',0,4); this.config.speedDecimals = finite(data.speedDecimals ?? 1,'转速小数位',0,3);
        if (!Number.isInteger(this.config.torqueDecimals) || !Number.isInteger(this.config.speedDecimals)) throw new Error('传感器小数位必须为整数');
        this.parser = new HexParser(this.config.protocol); this.rateAt = now(); this.rateCount = 0; this.rate = 0;
        this.remove = this.io.subscribe(data.device,chunk => this.receive(bytes(chunk)));
        await this.io.call('serial-open',{device:data.device,baudRate:this.config.baud,dataBits:8,stopBits:this.config.stopbits,parity:'none'});
        this.opened = true;
        this.observe?.('sensor_stream',{config:this.config,reset:true});
        this.frameTimer = setInterval(() => this.flushFrames(),20);
        const deadline = now() + 30;
        while (this.parser.streak < 3 && now() < deadline) await sleep(20);
        if (this.parser.streak < 3) throw new Error('未收到连续有效 HEX 帧');
      } else {
        this.parser = null; this.transport = new ModbusRtu(this.io); await this.transport.open(this.config);
        const integer = async register => { const words = await this.transport.exchange(3,register,2); return (words[0] << 16 | words[1]) | 0; };
        if (await integer(0x1c) !== 1) throw new Error('传感器当前不是 Modbus RTU 模式');
        if (await integer(0x18) !== (this.config.stopbits === 2 ? 0 : 1)) throw new Error('传感器停止位读回不匹配');
        this.config.torqueDecimals = await integer(0x08); this.config.speedDecimals = await integer(0x20);
        if (this.config.torqueDecimals < 0 || this.config.torqueDecimals > 4 || this.config.speedDecimals < 0 || this.config.speedDecimals > 3) throw new Error('传感器缩放参数无效');
        await this.read();
      }
      this.connected = true;this.error = ''; return this.status();
    } catch (error) {
      await this.disconnect(); this.error = error.message; throw error;
    }
  }
  receive(chunk) {
    const decoded = this.parser.feed(chunk), timestamp = utc();
    if (!decoded.length) return;
    const frames: [number, number, number, number][] = decoded.map(([torque,speed]) => [++this.sequence,timestamp,torque,speed]);
    this.frames.append(frames);
    this.rateCount += decoded.length;
    if (now() - this.rateAt >= 1) { this.rate = this.rateCount / (now() - this.rateAt); this.rateAt = now(); this.rateCount = 0; }
    const [torque,speed,raw] = decoded.at(-1);
    this.snapshot = {rawTorque:torque,rawSpeed:speed,torque:torque / 10 ** this.config.torqueDecimals,speed:speed / 10 ** this.config.speedDecimals,torqueDecimals:this.config.torqueDecimals,speedDecimals:this.config.speedDecimals,receivedAt:timestamp,config:{...this.config},framesReceived:this.sequence,frameRateHz:this.rate,targetRateHz:1000,crcOrder:this.parser.order,crcProfile:this.parser.profile,resyncBytes:this.parser.skipped,bufferedFrames:this.frames.length,logs:[{tx:'',rx:raw}]};
    this.pendingFrames.push(...frames);
  }
  flushFrames() {
    if (this.pendingFrames?.length) this.observe?.('sensor_frames',{config:this.config,frames:this.pendingFrames.splice(0)});
  }
  async read() {
    if (this.parser) return this.status();
    const words = await this.transport.exchange(3,0,4), torque = (words[0] << 16 | words[1]) | 0, speed = (words[2] << 16 | words[3]) | 0;
    this.snapshot = {rawTorque:torque,rawSpeed:speed,torque:torque / 10 ** this.config.torqueDecimals,speed:speed / 10 ** this.config.speedDecimals,torqueDecimals:this.config.torqueDecimals,speedDecimals:this.config.speedDecimals,receivedAt:utc(),config:{...this.config},logs:[]};
    return this.status();
  }
  batch(since) {
    const reset = !!this.frames.length && since > this.frames.at(this.frames.length - 1)[0], cursor = reset ? 0 : since;
    return {...this.status(),frames:this.frames.after(cursor),reset,dropped:cursor && this.frames.length ? Math.max(0,this.frames.at(0)[0] - cursor - 1) : 0};
  }
  async disconnect() {
    clearInterval(this.frameTimer);this.flushFrames();
    this.remove?.(); this.remove = null;
    if (this.opened) {await this.io.call('serial-close',{device:this.config.device});this.opened = false;}
    if (this.transport) await this.transport.close();
    this.transport = null; this.parser = null; this.connected = false; this.snapshot = null; this.config = null;
    return this.status();
  }
}
