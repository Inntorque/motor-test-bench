import {now,SerialQueue} from './bytes.ts';
import {FocLink,decodeFocSnapshot,focProfiles,focRunning,focValue} from './foc-protocol.ts';
import type {DeviceIo,FocAppId} from './messages.ts';
import {BrowserStore} from './storage.ts';
import {canInterface} from './transports.ts';

type Snapshot = ReturnType<typeof decodeFocSnapshot>;
const starts = new Set([2,3,8,10,12,17,19]);
const commands = new Set([2,3,4,5,7,8,9,10,11,12,13,19,20]);

function diagnosticValues(buffer: Uint8Array, metadata = false) {
  const data = new DataView(buffer.buffer,buffer.byteOffset,buffer.byteLength);
  const result: Record<string, number | string | null> = {};
  let offset = 0;
  const read = (names: string,width: number,get: (at: number) => number) => {
    for (const name of names.split(' ')) {
      const value = get(offset);
      result[name] = Number.isFinite(value) ? value : null;
      offset += width;
    }
  };
  const u32 = at => data.getUint32(at,true), u16 = at => data.getUint16(at,true);
  const u8 = at => data.getUint8(at), f32 = at => data.getFloat32(at,true);
  if (metadata) {
    read('version image_crc32 image_size clock_hz pwm_hz boot_count',4,u32);
    read('self_slot active_slot pending_slot try_left confirmed pole_pairs entry run_direction_negative',1,u8);
    read('encoder_direction',4,at => data.getInt32(at,true));
    read('run_id',4,u32);
    read('r l flux negative_id_min phase_limit adc_limit speed_limit bus_budget bus_voltage_cap electrical_zero encoder_delay offset_a offset_c current_per_count id_kp id_ki iq_kp iq_ki duty_min duty_max iq_ramp current_margin',4,f32);
    result.uid = [...buffer.subarray(offset)].map(value => value.toString(16).padStart(2,'0')).join('');
  } else {
    read('cycles sample_seq run_id allocator_count sample_age_us bus_age_us temperature_age_us allocator_age_us max_isr_cycles saturated_ticks control_tick',4,u32);
    read('state fault entry flags limits source direction_negative reserved',1,u8);
    read('adc_a adc_bus adc_c encoder_raw',2,u16);
    read('rpm bus_v temperature_c electrical_angle id_alloc iq_alloc id_ref iq_ref id_meas iq_meas ud_request uq_request ud uq voltage_limit voltage_scale model_voltage voltage_trim ia ib ic',4,f32);
  }
  return result;
}
type Diagnostic = ReturnType<typeof diagnosticValues>;
type Capture = {id: string; board: Diagnostic; samples: number; missed: number; final?: Snapshot; frozen?: Diagnostic};

export class FocApp {
  private readonly profile;
  private readonly queue = new SerialQueue();
  private link?: FocLink;
  private port?: {id: string; channel: string; interface: string};
  private data?: Snapshot;
  private stamp = 0;
  private page = 0;
  private heartbeat = 0;
  private lease = false;
  private searching = false;
  private error = '';
  private logs: string[] = [];
  private phaseUntil = 0;
  private nextDiagnostic = 0;
  private phase?: {at: number; values: number[]};
  private capture?: Capture;
  private lastCapture?: Capture;
  private readonly token = crypto.randomUUID();

  private readonly id: FocAppId;
  private readonly io: DeviceIo;
  private readonly store: BrowserStore;
  private readonly emit: (event: string, value: unknown) => void;

  constructor(id: FocAppId, io: DeviceIo, store: BrowserStore, emit: (event: string, value: unknown) => void) {
    this.id = id;
    this.io = io;
    this.store = store;
    this.emit = emit;
    this.profile = focProfiles[id];
  }

  async initialize() {
    if (this.id !== 'foc-7010') this.lastCapture = await this.store.get('foc-capture-latest') as Capture | undefined;
  }

  private log(message: string) {
    this.logs.push(new Date().toLocaleTimeString() + '  ' + message);
    if (this.logs.length > 150) this.logs.shift();
  }

  ioError(device: string, message: string) {
    if (device !== this.link?.bus.device) return;
    this.error = message;
    this.lease = false;
    this.link.fail(new Error(message));
    this.log(message);
  }

  private schedule(link: FocLink) {
    setTimeout(async () => {
      await this.queue.run(async () => {
        if (this.link !== link) return;
        try {
          if (now() - this.page >= .8) this.lease = false;
          if (this.lease && now() - this.heartbeat >= .1) {
            await link.command(6);
            this.heartbeat = now();
          }
          this.data = await link.snapshot();
          this.stamp = now();
          this.error = '';
          if (this.lease && !focRunning(this.data.state)) this.lease = false;
          this.emit('activity',{active:this.lease});
        } catch (error) {
          this.error = error.message;
          return;
        }
        if (this.id !== 'foc-7010') {
          try {await this.sampleDiagnostic(link);}
          catch {
            if (this.capture) this.capture.missed++;
            this.phase = undefined;
            if (this.data.state !== 17 && this.capture) await this.finishCapture();
          }
        }
      }).catch(error => this.log(error.message));
      if (this.link === link) this.schedule(link);
    },this.profile.interval);
  }

  private async connect(body) {
    const node = body.node;
    if (!Number.isInteger(node) || node < 0 || node > 15) throw new Error('节点须为 0–15 的整数');
    if (this.link) throw new Error('请先断开当前连接');
    const ports = await this.io.call('ports',{}) as {port: string; description: string; kind: string; opened: boolean}[];
    const candidates = ports.filter(port => !port.opened && canInterface(port) &&
      (!body.channel || body.channel === port.port) && (body.interface === 'auto' || body.interface === canInterface(port)));
    if (!candidates.length) throw new Error('请先授权并选择可用的 CAN 适配器');
    this.searching = true;
    try {
      for (const port of candidates) {
        const link = new FocLink(this.io,node,this.profile), adapter = canInterface(port);
        try {
          await link.bus.open({device:port.port,interface:adapter,bitrate:500000});
          await link.command(0);
          this.data = await link.snapshot();
          this.link = link;
          this.port = {id:port.port,channel:port.description,interface:adapter};
          this.stamp = now();
          this.error = '';
          this.log(`已识别 ${this.profile.model} · 节点 ${node}`);
          this.schedule(link);
          return;
        } catch (error) {
          await link.bus.close();
          this.error = error.message;
          this.log(`${port.description} · ${error.message}`);
        }
      }
      throw new Error(this.error);
    } finally {this.searching = false;}
  }

  private async finishCapture(frozen?: Diagnostic) {
    if (!this.capture) return;
    const completed = {...this.capture,final:this.data,frozen};
    await this.store.put('foc-capture-latest',completed);
    this.lastCapture = completed;
    this.capture = undefined;
    this.log(`测功诊断记录完成 · ${completed.samples} 帧`);
  }

  private async sampleDiagnostic(link: FocLink) {
    const stamp = now(), state = this.data.state;
    if (state !== 17 && this.capture) {
      const frozen = diagnosticValues(await link.diagnostic(22));
      await this.finishCapture(Number(frozen.flags) & 1 && frozen.run_id === this.capture.board.run_id ? frozen : undefined);
      return;
    }
    if (state !== 17 && (this.id !== 'foc-8513' || ![8,11].includes(state) || stamp >= this.phaseUntil)) {
      this.phase = undefined;
      return;
    }
    if (stamp < this.nextDiagnostic) return;
    this.nextDiagnostic = stamp + (state === 17 ? .2 : .1);
    const row = diagnosticValues(await link.diagnostic(21,state === 17 ? 120 : 60));
    if (state !== 17) {
      const values = [row.ia,row.ib,row.ic];
      this.phase = Number(row.flags) & 128 && row.fault === 0 && values.every(value => typeof value === 'number') ?
        {at:stamp,values:values as number[]} : undefined;
      return;
    }
    if (!this.capture || this.capture.board.run_id !== row.run_id) {
      await this.finishCapture();
      const board = diagnosticValues(await link.diagnostic(23),true);
      if (board.run_id !== row.run_id) return;
      this.capture = {id:crypto.randomUUID(),board,samples:0,missed:0};
    }
    await this.store.put(`foc-sample:${this.capture.id}:${String(this.capture.samples).padStart(9,'0')}`,{...row,host_ms:Date.now()});
    this.capture.samples++;
  }

  private async disconnect() {
    const link = this.link;
    if (!link) return;
    const leased = this.lease;
    this.lease = false;
    try {
      if (leased) await link.command(5);
    } finally {
      this.link = undefined;
      try {await link.bus.close();}
      finally {
        try {await this.finishCapture();}
        finally {
          this.data = this.port = this.phase = undefined;
          this.error = '';
          this.emit('activity',{active:false});
        }
      }
    }
    this.log('已释放 CAN 适配器');
  }

  private async control(body, mit: boolean) {
    const link = this.link;
    if (!link) throw new Error('请先连接设备');
    const action = mit ? (body.start ? 17 : 18) : body.action;
    const valid = mit ? typeof body.start === 'boolean' : commands.has(action) && (action !== 20 || this.id !== 'foc-7010');
    if (!valid)
      throw new Error('未知操作');
    if (action !== 5 && (now() - this.stamp >= .8 || this.error)) throw new Error('请等待新鲜的板端反馈');
    const values = mit ? [[14,body.torque],[15,body.speed],[16,body.direction],[action,body.position]] : [[action,body.value ?? 0]];
    const frames = values.map(([code,value]) => [code,focValue(this.profile,code,value)] as const);
    if (action === 5 || action === 20) this.lease = false;
    for (const [code,value] of frames) await link.command(code,value);
    if (starts.has(action)) {
      this.lease = true;
      this.heartbeat = now();
    }
    this.page = now();
    this.emit('activity',{active:this.lease});
    this.log(action === 5 ? '板端已确认关闭输出' : `板端已接受操作 ${action}`);
  }

  async request(path,method,body,headers) {
    const [route,query] = path.split('?');
    if (method === 'GET' && route === '/api/meta') return {token:this.token,selfcheck:false};
    if (method === 'GET' && route === '/api/state') {
      this.page = now();
      if (query === 'wave=phase') this.phaseUntil = this.page + .5;
      return {connected:!!this.link,channel:this.port?.channel || '',channelId:this.port?.id,interface:this.port?.interface || '',
        searching:this.searching,data:this.data || null,age:this.data ? now() - this.stamp : null,error:this.error,
        busy:false,job:null,logs:this.logs,phase:query === 'wave=phase' && this.phase ? {age:now() - this.phase.at,values:this.phase.values} : null,
        capture:!!(this.capture?.samples || this.lastCapture?.samples)};
    }
    if (method !== 'POST' || headers['x-control-token'] !== this.token) throw new Error('请求无效');
    if (route === '/api/export-dyno') {
      const latest = this.capture?.samples ? this.capture : this.lastCapture;
      if (!latest?.samples) throw new Error('尚无测功诊断记录');
      const capture = {...latest};
      const samples = await this.store.entries(`foc-sample:${capture.id}:`) as Diagnostic[];
      this.emit('download',{name:`${this.profile.model}-dyno-${capture.id}.json`,type:'application/json',body:JSON.stringify({...capture,rows:samples.slice(0,capture.samples)})});
      return {ok:true};
    }
    return this.queue.run(async () => {
      if (route === '/api/connect') await this.connect(body);
      else if (route === '/api/disconnect') await this.disconnect();
      else if (route === '/api/command' || route === '/api/mit') await this.control(body,route === '/api/mit');
      else throw new Error('未知操作');
      return {ok:true};
    });
  }
}
