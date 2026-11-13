import {finite, view} from './bytes.ts';
import type {DeviceIo, FocAppId} from './messages.ts';
import {CanTransport} from './transports.ts';

export const focProfiles = {
  'foc-7010': {model:'7010', counts:16384, speed:3000, current:10, torque:.7, interval:20},
  'foc-5014': {model:'5014', counts:65536, speed:3276.7, current:5.37, torque:.5, interval:50},
  'foc-8513': {model:'8513', counts:65536, speed:3000, current:15.5, torque:1.78, interval:50},
} as const;
export type FocProfile = typeof focProfiles[FocAppId];
export const focRunning = (state: number) => state > 0 && state !== 8 && state !== 10;

export function focValue(profile: FocProfile, action: number, value: unknown) {
  const number = Number(value);
  if (value == null || typeof value === 'boolean' || typeof value === 'string' && !value.trim() || !Number.isFinite(number))
    throw new Error('请输入有限数值');
  switch (action) {
    case 3: case 4: return Math.round(finite(number,'速度',-profile.speed,profile.speed) * 10);
    case 8: case 9: return Math.round(finite(number,'电流',-profile.current,profile.current) * 1000);
    case 10: case 11: case 17: case 18: return Math.round(finite(number,'位置',0,360) * 10);
    case 12: case 13: return Math.round(finite(number,'力矩',-profile.torque,profile.torque) * 1000);
    case 14: return Math.round(finite(number,'MIT 力矩',0,profile.torque) * 1000);
    case 15: return Math.round(finite(number,'MIT 速度',.1,profile.speed) * 10);
    case 16:
      if (number !== -1 && number !== 1) throw new Error('MIT 方向须为 ±1');
      return number;
    case 7: return Math.round(finite(number,'温度门限',0,150) * 100);
    default:
      if (number !== 0) throw new Error('此操作的参数须为 0');
      return 0;
  }
}

export function decodeFocSnapshot(profile: FocProfile, packets: Map<number, Uint8Array>) {
  const h = packets.get(0xd2);
  if (!h) return;
  const codes = [0xd2,0xd3,0xd4,0xd5,0xd6,0xd7];
  if (profile.model !== '7010') {
    if (h[7] & 1) codes.push(0xd8);
    if (h[7] & 2) codes.push(0xd9,0xda);
  }
  if (!codes.every(code => packets.has(code))) return;
  const get = (code: number) => view(packets.get(code));
  const motion = get(0xd3), sensor = get(0xd4), params = get(0xd5), thermal = get(0xd6), position = get(0xd7);
  const adc = sensor.getUint16(2,true), raw = get(0xd2).getUint16(5,true), temperature = thermal.getInt16(2,true);
  const vbus = codes.includes(0xd8) ? get(0xd8).getUint16(2,true) : 0xffff;
  let dyno_stop = null;
  if (codes.includes(0xd9)) {
    const summary = packets.get(0xd9), detail = get(0xda), stopVbus = detail.getUint16(6,true);
    dyno_stop = {source:summary[2],state:summary[3],fault:summary[4],feasible:!!(summary[5] & 1),
      bus_valid:!!(summary[5] & 2),reverse:!!(summary[5] & 4),limits:summary[6],sample_age_ms:summary[7],
      id_target:detail.getInt16(2,true) / 1000,iq_target:detail.getInt16(4,true) / 1000,vbus_v:stopVbus === 0xffff ? null : stopVbus / 100};
  }
  return {state:h[2],fault:h[3],calibrated:!!(h[4] & 1),encoder_ok:!!(h[4] & 2),ntc_valid:!!(h[4] & 4),
    calibration_saved:!!(h[4] & 8),calibration_pending:!!(h[4] & 16),mit_holding:!!(h[4] & 32),
    encoder_raw:raw,angle_deg:raw * 360 / profile.counts,electrical_deg:params.getUint16(6,true) % profile.counts * 360 / profile.counts,
    rpm:motion.getInt16(2,true) / 10,id:motion.getInt16(4,true) / 1000,iq:motion.getInt16(6,true) / 1000,
    ntc_adc:adc,diagnostic:sensor.getUint16(4,true),ntc_ohm:adc > 0 && adc < 4095 ? 4700 * adc / (4096 - adc) : null,
    isr_us:sensor.getUint16(6,true),phase_r:params.getUint16(2,true) / 1000,phase_l:params.getUint16(4,true) / 1000000,
    temperature:temperature !== -32768 && h[4] & 4 ? temperature / 100 : null,temperature_limit:thermal.getInt16(4,true) / 100,
    dyno_iq_target:thermal.getInt16(6,true) / 1000,position_deg:position.getInt32(2,true) / 100,
    calibration_travel_deg:position.getUint16(6,true) / 10,vbus_v:vbus === 0xffff ? null : vbus / 100,dyno_stop};
}

export class FocLink {
  readonly bus: CanTransport;
  private sequence = 0;
  private receive?: (id: number, data: Uint8Array) => void;
  private reject?: (error: Error) => void;

  readonly node: number;
  readonly profile: FocProfile;

  constructor(io: DeviceIo, node: number, profile: FocProfile) {
    this.node = node;
    this.profile = profile;
    this.bus = new CanTransport(io,(id,data) => this.receive?.(id,data));
  }

  fail(error: Error) {this.reject?.(error);}

  exchange<T>(action: number, value: number, decode: (data: Uint8Array) => T | undefined, timeout = 250): Promise<T> {
    const seq = this.sequence = this.sequence % 254 + 1, id = 0x610 + this.node;
    const payload = Uint8Array.of(0xd0,2,seq,action,0,0,0,0);
    view(payload).setInt16(4,value,true);
    return new Promise((resolve,reject) => {
      let settled = false;
      const finish = (error?: Error, result?: T) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        clearInterval(probe);
        this.receive = this.reject = undefined;
        if (error) reject(error); else resolve(result);
      };
      const timer = setTimeout(() => finish(new Error(`${this.profile.model} 节点 ${this.node} 无完整应答`)),timeout);
      const probe = this.bus.interface === 'zqwl' && this.profile.model !== '7010' ? setInterval(() => {
        void this.bus.send(id,Uint8Array.of(0xd0,2,255,0,0,0,0,0)).catch(error => finish(error));
      },20) : undefined;
      this.reject = finish;
      this.receive = (reply,data) => {
        if (reply !== 0x590 + this.node || data.length !== 8 || data[data[0] === 0xd1 ? 2 : 1] !== seq) return;
        try {
          const result = decode(data);
          if (result !== undefined) finish(undefined,result);
        } catch (error) {finish(error);}
      };
      void this.bus.send(id,payload).catch(error => finish(error));
    });
  }

  command(action: number,value = 0) {
    return this.exchange(action,value,data => {
      if (data[0] !== 0xd1 || data[1] !== 2) return;
      if (String.fromCharCode(...data.subarray(4)) !== this.profile.model) throw new Error(`设备身份与 ${this.profile.model} 不匹配`);
      if (data[3]) throw new Error(`板端拒绝操作 ${action}，状态码 ${data[3]}`);
      return true;
    });
  }

  snapshot() {
    const packets = new Map<number, Uint8Array>();
    return this.exchange(1,0,data => {
      if (data[0] < 0xd2 || data[0] > 0xda) return;
      packets.set(data[0],data);
      return decodeFocSnapshot(this.profile,packets);
    });
  }

  diagnostic(action: 21 | 22 | 23, timeout = 120) {
    const size = action === 23 ? 140 : 144, count = Math.ceil(size / 5);
    const packet = new Uint8Array(count * 5), received = new Set<number>();
    return this.exchange(action,1,data => {
      if (data[0] !== 0xdb || data[2] >= count) return;
      const offset = data[2] * 5, fragment = data.subarray(3);
      if (received.has(data[2]) && fragment.some((value,index) => value !== packet[offset + index]))
        throw new Error('测功诊断分帧内容冲突');
      packet.set(fragment,offset);
      received.add(data[2]);
      if (received.size === count) return packet.slice(0,size);
    },timeout);
  }
}
