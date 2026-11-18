import {MotorDevice} from './motor.ts';
import {PowerChannel,TorqueSensor} from './rs485.ts';
import {canInterface} from './transports.ts';
import {CurrentMotor,BrakeCurrent,CoupledCurrent} from './control.ts';
import {TestSubjects,Recordings,DesktopRecordStore} from './storage.ts';
import {Specialized} from './specialized.ts';
import {O,OP} from './mbp-codec.ts';
import {finite,jsonValue,now,pack,sleep,utc} from './bytes.ts';
export class Bench {
  updating = false;
  private pendingWrites = 0;
  declare brake: BrakeCurrent;
  declare channels: { [k: string]: PowerChannel; };
  declare coupled: CoupledCurrent;
  declare device: MotorDevice;
  declare emit;
  declare io;
  declare lastPowerFault;
  declare motor: CurrentMotor;
  declare motorIsolated: boolean | undefined;
  declare observe: (kind: string, data: unknown) => void;
  declare polling: boolean | undefined;
  declare prepared: { id: number; epoch: number; } | null | undefined;
  declare recordings: Recordings;
  declare autoRecording: Recordings | undefined;
  declare recoveryExpected: Set<string> | null | undefined;
  declare recoveryMotor: boolean | undefined;
  declare sensor: TorqueSensor;
  declare specialized: Specialized;
  declare stopKeys;
  declare stopTask;
  declare store;
  declare subjects: TestSubjects;
  declare timers: number[] | undefined;
  declare token: `${string}-${string}-${string}-${string}-${string}`;

  epoch = 0; owner = null; blocked = false; stopping = false; clearingFault = false; errors = []; reason = ''; expected = new Set();
  connection = {running:false,report:[],candidates:[]}; histories = [];
  private waitingForMotor = false;
  private motorConnectTask: Promise<ReturnType<CurrentMotor['status']>> | null = null;
  private nextMotorConnect = 0;
  private motorConnectGeneration = 0;
  private motorHint: {device?: string; node?: number} = {};
  constructor(io,store,emit,desktop=false) {
    this.io = io; this.store = store; this.emit = emit; this.token = crypto.randomUUID();
    this.observe = (kind,data) => { this.recordings?.observe(kind,data); this.autoRecording?.observe(kind,data); this.emit(kind,data); };
    this.channels = Object.fromEntries(['motor-supply','brake-supply'].map(key => [key,new PowerChannel(key,io)]));
    this.sensor = new TorqueSensor(io,this.observe); this.device = new MotorDevice(io,this.observe);
    this.motor = new CurrentMotor(this.device,this); this.brake = new BrakeCurrent(this.channels['brake-supply']); this.coupled = new CoupledCurrent(this);
    this.subjects = new TestSubjects(store,() => this.busy());
    this.recordings = new Recordings(store,() => this.source(),() => this.subjects.selection()); this.specialized = new Specialized(this);
    if (desktop) this.autoRecording = new Recordings(new DesktopRecordStore(io),() => this.source(),() => this.subjects.selection(),true);
    for (const recording of [this.recordings,this.autoRecording].filter(Boolean)) {
      recording.context = () => this.recordingContext();
      recording.drain = () => this.sensor.flushFrames();
    }
  }
  recordingContext() {
    const special = Object.values(this.specialized.live).find(report => report?.running);
    if (special) return {runId:special.id,kind:special.origin,phase:special.phase || 'preparing'};
    const run = this.coupled.run.running || this.coupled.run.postStopTailState === 'collecting' ? this.coupled.run : null;
    if (run) return {runId:run.id,kind:'coupled',phase:run.postStopTailState === 'collecting' ? 'post_stop_tail' : run.phase,targetNm:run.targetNm,measuredNm:run.measuredNm,currentA:run.brakeCurrentA};
    if (this.brake.run.running) return {runId:this.brake.run.id,kind:'brake',phase:this.brake.run.phase};
    const motor = this.motor.run;
    return {runId:motor.id,kind:motor.running ? 'motor' : 'idle',phase:motor.running ? motor.phase : 'idle'};
  }
  async initialize() {
    await this.subjects.initialize(); await this.recordings.initialize(); await this.specialized.initialize();
    const journal = await this.store.get('output-journal');
    if (journal?.dirty) {
      this.recoveryExpected = new Set(journal.expected || []);
      this.recoveryMotor = !!journal.motorMayBeOn;
      this.expected = new Set(journal.expected || []);
      this.blocked = true; this.reason = '上次会话有未核实的输出记录，等待相关设备回读';
    }
    if (this.autoRecording) await this.autoRecording.start({name:"自动采集"});
    this.histories = await this.recordings.history();
    this.timers = [setInterval(() => this.publish(),200),setInterval(() => {this.recordings.capture(); this.autoRecording?.capture();},50),setInterval(() => {void this.poll();},300)];
  }
  busy() { return !!(this.motor.run.running || this.brake.run.running || this.coupled.run.running || this.recordings.live?.running || this.specialized.active()); }
  source() { return {...Object.fromEntries(Object.entries(this.channels).map(([key,channel]) => [key,channel.status()])), 'torque-sensor':this.sensor.status(),'current-motor':this.motor.status()}; }
  safety() { return {epoch:this.epoch,owned:!!this.owner,blocked:this.blocked,stopping:this.stopping,reason:this.reason,errors:[...this.errors],motorIsolated:!!this.motorIsolated,unusedChannels:Object.entries(this.channels).filter(([,channel]) => !channel.config && !channel.outputUncertain).map(([key]) => key),leaseTimeoutS:3,lastPowerFault:this.lastPowerFault || null}; }
  health(channels = this.source(),safety = this.safety()) { return {app:'motor-test-bench-hardware',hostApi:1,recordSchema:2,wireProtocol:[1,0],runtime:'browser',token:this.token,safety,channels}; }
  routes(devices = this.source(),control = this.safety(),recording = this.recordings.status()) {
    const motor = devices['current-motor'],brake = this.brake.status();
    return {'/api/health':this.health(devices,control),'/api/control/status':control,'/api/connections/status':this.connection,'/api/motor-current/status':{...motor,safety:control},'/api/brake-current/status':{...brake,safety:control},'/api/coupled-current/status':this.coupled.status(motor,brake,control),'/api/recordings/status':{current:recording},'/api/automatic/status':this.specialized.status('automatic',false),'/api/specialized/status':this.specialized.status('specialized',false),'/api/test-subjects':this.subjects.status()};
  }
  state() {
    const devices = this.source(),control = this.safety(),recording = this.recordings.status();
    return {hostApi:1,recordSchema:2,routes:this.routes(devices,control,recording),devices,control,test:this.coupled.run,recording,desktopBusy:this.desktopBusy()};
  }
  desktopBusy() {
    return this.busy() || this.blocked || this.stopping || !!(this.device.state.flags & 1) ||
      Object.values(this.channels).some(channel => channel.outputUncertain || channel.snapshot?.status & 128);
  }
  publish() {
    if (this.owner && !this.stopping && !this.blocked && !this.coupled.run.running && [this.motor,this.brake].some(engine => engine.run.phase === 'stopped' && engine.run.stopConfirmed === false)) this.beginStop('输出关闭未确认，执行原电源断电兜底',Object.keys(this.channels));
    this.emit('state',this.state());
  }
  async poll() {
    if (this.polling) return;
    this.polling = true;
    try {
      await Promise.all(Object.entries(this.channels).filter(([,channel]) => channel.config).map(async ([key,channel]) => {
        try {
          await channel.read();
          if (channel.issue && channel.snapshot.status & 128) {
            this.lastPowerFault = {kind:'power_fault',channel:key,reason:channel.issue,evidence:channel.powerCheck || channel.snapshot,receivedAt:utc()};
            this.observe('power_fault',this.lastPowerFault);
            if (!this.stopping) this.beginStop('电源：' + channel.issue,[key]);
          }
        } catch (error) {
          channel.connected = false; channel.error = error.message;
          if (channel.outputUncertain && !this.stopping && !this.blocked) this.beginStop('电源通信失效：' + error.message,Object.keys(this.channels));
        }
      }));
      if (this.sensor.config && !this.sensor.parser) {
        try { await this.sensor.read(); } catch (error) { this.sensor.connected = false; this.sensor.error = error.message; }
      }
      await this.reconcileRecovery();
      this.pollMotorConnection();
    } finally { this.polling = false; }
  }
  async reconcileRecovery() {
    if (!this.recoveryExpected || !this.blocked || this.stopping) return;
    if (!this.recoveryExpected.size && !this.recoveryMotor) return;
    const epoch = this.epoch, devices = this.source(), stamp = utc();
    const fresh = device => device?.connected && Number.isFinite(device.snapshot?.receivedAt) &&
      stamp - device.snapshot.receivedAt >= 0 && stamp - device.snapshot.receivedAt <= 1.5;
    const power = devices['motor-supply'], drive = devices['current-motor'], motor = drive?.snapshot?.motor;
    const needsMotor = this.recoveryMotor || this.recoveryExpected.has('current-motor');
    const motorOff = fresh(drive) && motor?.output_on === false && Number.isFinite(motor.age_s) && motor.age_s >= 0 && motor.age_s <= 1;
    const decimals = power?.config?.voltageDecimals ?? power?.snapshot?.config?.voltageDecimals;
    const rawVoltage = power?.snapshot?.measured?.[0];
    const isolated = fresh(power) && !(power.snapshot.status & 128) && Number.isFinite(decimals) &&
      Number.isFinite(rawVoltage) && rawVoltage >= 0 && rawVoltage / 10 ** decimals < 2.4;
    if (needsMotor && !motorOff && !isolated) return;
    for (const key of this.recoveryExpected) {
      if (key === 'current-motor') continue;
      if (!fresh(devices[key]) || key !== 'motor-supply' && (devices[key].snapshot.status & 128)) return;
    }
    const powered = fresh(devices['motor-supply']) && !!(devices['motor-supply'].snapshot.status & 128);
    const expected = powered ? ['motor-supply'] : [];
    try { await this.store.put('output-journal',{dirty:powered,expected,motorMayBeOn:false}); }
    catch (error) { this.errors = ['输出状态确认保存失败：' + error.message]; return; }
    if (epoch !== this.epoch || this.stopping) return;
    this.blocked = false; this.errors = []; this.reason = '上次会话的输出状态已核实';
    this.recoveryExpected = null; this.recoveryMotor = false; this.expected = new Set(expected);
    this.motorIsolated = needsMotor && !motorOff && isolated;
    this.motor.run.stopConfirmed = needsMotor || motorOff ? true : null;
    this.brake.run.stopConfirmed = fresh(devices['brake-supply']) && !(devices['brake-supply'].snapshot.status & 128) ? true : null;
    this.coupled.run.stopConfirmed = this.motor.run.stopConfirmed && this.brake.run.stopConfirmed ? true : null;
    this.coupled.run.stopErrors = [];
    this.observe('safety_recovered',this.safety()); this.publish();
  }
  async claim(headers,{energizing = false,restoring = false,keys = [],motor = false} = {}) {
    const client = headers['x-workbench-client'], epoch = headers['x-control-epoch'];
    if (!client || client.length < 16 || client.length > 128) throw new Error('请刷新网页以建立控制会话');
    if (String(epoch) !== String(this.epoch)) throw new Error('停止已取消旧指令，请读取状态后重新操作');
    if (this.blocked && !restoring) throw new Error('停机尚未确认，请先重试停止');
    if (this.owner && client !== this.owner) throw new Error('另一控制会话仍在使用设备');
    if (energizing) {
      if (motor && !this.motor.status().connected) throw new Error('电机未连接，未发送启动命令');
      this.owner = client; keys.filter(key => this.channels[key]?.config).forEach(key => this.expected.add(key));
      if (motor) {this.expected.add('current-motor');this.motorIsolated = false;}
      await this.store.put('output-journal',{dirty:true,expected:[...this.expected],motorMayBeOn:this.expected.has('current-motor')});
    }
  }
  beginStop(reason = '操作者停止',keys = []) {
    this.cancelMotorConnection();
    this.epoch++; this.prepared = null; this.reason = reason; this.blocked = true; this.stopping = true;
    this.motor.generation++; this.brake.generation++; this.coupled.generation++;
    for (const origin of ['automatic','specialized']) this.specialized.generation[origin]++;
    for (const channel of Object.values(this.channels)) channel.epoch++;
    this.stopKeys ||= new Set(); keys.forEach(key => this.stopKeys.add(key));
    for (const key of this.recoveryExpected || []) if (this.channels[key]) this.stopKeys.add(key);
    if (!this.stopTask) this.stopTask = (async () => {
      do { await this.stopOutputs(); } while (this.stopKeys.size);
    })().finally(() => {this.stopTask = null;});
    return this.safety();
  }
  async stopOutputs() {
    this.stopping = true;
    const errors = [], attempt = async (label,action) => {
      try { await action(); return true; } catch (error) { errors.push(`${label}：${error.message}`); return false; }
    };
    let motorOk = true;
    if (this.recoveryMotor && (!this.device.bus || this.device.bus.closed)) {motorOk = false;errors.push('电机：请重新连接原驱动板以确认停止，或连接原电机电源进行断电确认');}
    const motor = this.device.bus && !this.device.bus.closed ? attempt('电机',() => this.motor.stop()).then(ok => {motorOk = ok;}) : Promise.resolve();
    if (this.channels['brake-supply'].config) this.stopKeys.add('brake-supply');
    const drainPowerOff = async () => { while (this.stopKeys.size) {
      const keys = [...this.stopKeys]; this.stopKeys.clear();
      await Promise.all(keys.map(key => {
        if (this.channels[key]?.config) return key === 'brake-supply'
          ? attempt('制动',() => this.brake.stop()) : attempt(key,() => this.channels[key].output(false));
        if (this.recoveryExpected?.has(key)) errors.push(`${key}：请重新连接原电源以确认输出关闭`);
        return Promise.resolve();
      }));
    } };
    await drainPowerOff();
    await motor;
    await drainPowerOff();
    if (!motorOk && this.channels['motor-supply'].config) {
      const channel = this.channels['motor-supply'];
      const isolated = await attempt('电机电源断电确认',async () => {
        await channel.output(false);
        const deadline = now() + 8;
        while (now() < deadline) {
          await channel.read();
          if (!(channel.snapshot.status & 128) && channel.snapshot.measured[0] / 10 ** channel.config.voltageDecimals < 2.4) { this.motorIsolated = true; return; }
          await sleep(100);
        }
        throw new Error('电源残压尚未降到原停机确认范围');
      });
      if (isolated) motorOk = true;
    }
    Object.assign(this.motor.run,{running:false,phase:'stopped',stopConfirmed:motorOk});
    Object.assign(this.brake.run,{running:false,phase:'stopped'});
    Object.assign(this.coupled.run,{running:false,phase:'stopped',stopErrors:errors});
    await drainPowerOff();
    this.stopping = false; this.errors = errors; this.blocked = !motorOk || errors.some(error => !this.motorIsolated || !error.startsWith('电机：'));
    if (!this.blocked) { this.owner = null; this.expected.clear(); }
    const motorMayBeOn = !motorOk || !this.motorIsolated && !!(this.device.state.flags & 1);
    const dirty = this.blocked || motorMayBeOn || Object.values(this.channels).some(channel => channel.outputUncertain);
    const expected = dirty ? Object.keys(this.channels).filter(key => this.channels[key].outputUncertain || this.recoveryExpected?.has(key)) : [];
    if (motorMayBeOn) expected.push('current-motor');
    try { await this.store.put('output-journal',{dirty,expected,motorMayBeOn}); }
    catch (error) { this.errors.push('停机记录保存失败：' + error.message); this.blocked = true; }
    this.coupled.run.stopConfirmed = !this.blocked;
    if (!this.blocked) {this.recoveryExpected = null;this.recoveryMotor = false;}
    this.observe('safety_stop_result',this.safety()); this.publish();
  }
  async motorConfig(data: {device?: string; node?: number} = {}) {
    const ports = await this.io.call('ports',{}), selected = await this.io.call('can-config',{});
    const port = ports.find(port => port.port === (data.device || selected.device)) || ports.find(canInterface);
    if (!port) throw new Error('请先授权 USB-CAN 适配器');
    if (!canInterface(port)) throw new Error('所选接口不是已支持的 CAN 适配器');
    return {...data,device:port.port,interface:canInterface(port),baud:500000,node:Number(data.node ?? selected.node ?? 1)};
  }
  private reportMotorConnection(message: string) {
    this.connection.report = this.connection.report.filter(line => !line.startsWith('电机驱动板：'));
    this.connection.report.push('电机驱动板：' + message);
  }
  private cancelMotorConnection() {
    if (this.waitingForMotor) {
      this.motor.error = '已取消驱动板自动连接';
      this.reportMotorConnection(this.motor.error);
    }
    this.waitingForMotor = false;
    this.motorConnectGeneration++;
  }
  private connectMotor(hint = this.motorHint) {
    if (this.motorConnectTask) return this.motorConnectTask;
    this.motorHint = hint; this.waitingForMotor = true;
    const generation = this.motorConnectGeneration;
    this.motorConnectTask = (async () => {
      try {
        const config = await this.motorConfig(hint);
        if (generation !== this.motorConnectGeneration) return this.motor.status();
        const result = await this.motor.connect(config);
        if (generation === this.motorConnectGeneration) {
          this.waitingForMotor = false;
          this.reportMotorConnection('已连接');
        }
        return result;
      } catch (error) {
        if (generation === this.motorConnectGeneration) {
          this.nextMotorConnect = now() + 1;
          this.motor.error = '等待驱动板上电回传，电源开启后自动连接；' + error.message;
          this.reportMotorConnection(this.motor.error);
        }
        throw error;
      } finally {
        this.motorConnectTask = null;
        this.publish();
      }
    })();
    return this.motorConnectTask;
  }
  private pollMotorConnection() {
    if (!this.waitingForMotor || this.motorConnectTask || this.connection.running || this.stopping || this.busy() || now() < this.nextMotorConnect) return;
    if (this.motor.status().connected) {this.waitingForMotor = false;return;}
    const power = this.channels['motor-supply'];
    if (!power.connected || power.issue || !(power.snapshot?.status & 128) || utc() - power.snapshot.receivedAt > 2) return;
    this.motor.error = '电机电源已开启，正在等待驱动板回传并自动连接';
    this.reportMotorConnection(this.motor.error);
    void this.connectMotor().catch(() => {});
  }
  private async probeRs485(device, key, hint) {
    const config = {...hint,device,transport:'serial'};
    if (key === 'torque-sensor') {
      const sensor = new TorqueSensor(this.io,undefined);
      const streaming = ['hex6','hex8'].includes(hint?.protocol);
      const candidates = ['hex6','hex8'].map(protocol => ({
        ...config,protocol,baud:streaming ? hint.baud : 115200,stopbits:streaming ? hint.stopbits : 1,
      }));
      candidates.push({...config,protocol:'modbus',baud:streaming ? 19200 : config.baud,stopbits:streaming ? 2 : config.stopbits});
      for (const candidate of candidates) {
        try { await sensor.connect(candidate,.4); return candidate; }
        catch { continue; }
        finally { await sensor.disconnect(); }
      }
      return null;
    }

    const power = new PowerChannel(key,this.io);
    try { await power.connect(config); return config; }
    catch { return null; }
    finally { await power.transport?.close(); }
  }
  async connectAll(hints) {
    if (this.busy() || this.connection.running) throw new Error('试验或记录中不重新扫描');
    this.connection = {running:true,report:[],candidates:[]};
    void (async () => {
      const labels = {'motor-supply':'电机电源','brake-supply':'制动电源','torque-sensor':'动态扭矩传感器'};
      const channels = {'torque-sensor':this.sensor,...this.channels};
      const report = (label, result) => this.connection.report.push(`${label}：${result}`);
      try {
        const ports = await this.io.call('ports',{});
        const rs485 = ports.filter(port => port.kind === 'serial' && !canInterface(port));
        const reserved = new Set(Object.values(channels).map(channel => channel.config?.device).filter(Boolean));
        for (const hint of Object.values(hints) as {device?: string}[]) if (hint?.device) reserved.add(hint.device);
        const missing = [];
        for (const [key,channel] of Object.entries(channels)) {
          if (channel.status().connected) { report(labels[key],'已连接'); continue; }
          const hint = hints[key];
          if (rs485.some(port => port.port === hint?.device)) {
            try { await channel.connect(hint); report(labels[key],'已连接'); }
            catch (error) { report(labels[key],error.message); }
          } else missing.push(key);
        }

        const available = rs485.filter(port => port.vid != null && !port.opened && !reserved.has(port.port));
        const candidates = missing.length ? (await Promise.all(available.map(async port => {
          const matches = [];
          for (const key of missing) {
            const config = await this.probeRs485(port.port,key,hints[key]);
            if (config) matches.push({key,config,description:port.description});
          }
          return matches;
        }))).flat() : [];
        for (const key of missing) {
          const matches = candidates.filter(candidate => candidate.key === key);
          if (!matches.length) {
            report(labels[key],rs485.length ? '未识别到设备，请确认转接器都已授权且设备已应答' : '请先通过顶部“授权串口”逐个授权 USB-RS485，再点一键连接');
            continue;
          }
          const [match] = matches;
          if (matches.length > 1 || candidates.some(other => other !== match && other.config.device === match.config.device)) {
            report(labels[key],'存在多个匹配，请在设备面板手动绑定');
            continue;
          }
          try {
            await channels[key].connect(match.config);
            const result = `已自动识别并连接（${match.description}）`;
            report(labels[key],result);
          } catch (error) { report(labels[key],error.message); }
        }
        try {
          if (!this.motor.status().connected) await this.connectMotor(hints['current-motor']);
          this.reportMotorConnection('已连接');
        } catch { /* connectMotor keeps the pending connection status. */ }
      } catch (error) {
        report('接口扫描失败',error.message);
      } finally { this.connection.running = false; this.publish(); }
    })();
    return this.connection;
  }
  async request(path,method,body,headers = {}) {
    if (method !== 'POST') return this.dispatch(path,method,body,headers);
    this.pendingWrites++;
    try { return await this.dispatch(path,method,body,headers); }
    finally { this.pendingWrites--; }
  }
  private async dispatch(path,method,body,headers) {
    const url = new URL(path,'https://browser.invalid'); path = url.pathname.replace('/api/v1/','/api/');
    if(this.updating&&method!=='GET'&&!path.endsWith('/stop'))throw new Error('正在准备更新，暂不接受设备控制');
    if (method === 'GET') {
      if(path==='/api/desktop/update-ready'){
        if(this.pendingWrites || this.desktopBusy())return {ready:false,error:'试验或设备输出尚未结束，请先停止后再更新'};
        this.updating=true;return {ready:true};
      }
      if(path==='/api/desktop/update-cancel'){
        if(this.updating && this.autoRecording?.live && !this.autoRecording.live.running)
          await this.autoRecording.start({name:'自动采集'});
        this.updating=false;return {ready:true};
      }
      if (path === '/api/desktop/close-ready') {
        this.sensor.flushFrames();
        if (url.searchParams.get('finish') === '1') {
          if (this.recordings.live?.running) await this.recordings.stop(null);
          else await this.recordings.flush();
          if (this.autoRecording?.live?.running) await this.autoRecording.stop(null);
          else await this.autoRecording?.flush();
        } else {
          await this.recordings.flush();
          await this.autoRecording?.flush();
        }
        return {ready:true};
      }
      if (path === '/api/desktop/autosave') return this.autoRecording?.status() || null;
      if (path === '/api/recordings/status') return {current:this.recordings.status(),...(url.searchParams.get('history') !== '0' ? {records:await this.recordings.history()} : {})};
      if (path === '/api/automatic/status' || path === '/api/specialized/status') return this.specialized.status(path.split('/')[2],url.searchParams.get('history') !== '0');
      if (path === '/api/state') return this.state();
      if (path === '/api/health') return this.health();
      if (path === '/api/control/status') return this.safety();
      if (path === '/api/connections/status') return this.connection;
      if (path === '/api/motor-current/status') return {...this.motor.status(),safety:this.safety()};
      if (path === '/api/brake-current/status') return {...this.brake.status(),safety:this.safety()};
      if (path === '/api/coupled-current/status') return this.coupled.status();
      if (path === '/api/test-subjects') return this.subjects.status();
      if (path === '/api/ports') return {ports:await this.io.call('ports',{})};
      if (path === '/api/recordings') return {records:await this.recordings.history(),current:this.recordings.status()};
      if (path === '/api/sensors/torque-sensor/frames') return this.sensor.batch(Number(url.searchParams.get('since') || 0));
      if (path === '/api/sensors/torque-sensor/capture.csv') {
        const config = this.sensor.config, rows = this.sensor.frames.after(0);
        return {file:true,type:'text/csv',body:'\ufeffsequence,received_unix_s,raw_torque,raw_speed,torque_Nm,speed_rpm\r\n' + rows.map(row => [...row,row[2] / 10 ** config.torqueDecimals,row[3] / 10 ** config.speedDecimals].join(',')).join('\r\n')};
      }
      const file = path.match(/^\/api\/(recordings|automatic|specialized)\/files\/([a-f0-9]{32})\/([a-z.]+)$/);
      if (file) return {file:true,...(file[1] === 'recordings' ? await this.recordings.file(file[2],file[3]) : await this.specialized.file(file[1],file[2],file[3]))};
      throw new Error('接口不存在：' + path);
    }
    if (method !== 'POST') throw new Error('不支持的请求方法');
    if (headers['x-motorbench-token'] !== this.token) throw new Error('控制令牌无效，请刷新网页');
    if (path === '/api/control/heartbeat') return this.safety();
    if (path === '/api/control/release') { if (this.owner && headers['x-workbench-client'] === this.owner) this.beginStop('控制页面已离开',Object.keys(this.channels)); return this.safety(); }
    if (path.endsWith('/stop') && !path.startsWith('/api/recordings/') && !path.startsWith('/api/specialized/')) {
      if (path === '/api/automatic/stop') return this.specialized.stop('automatic',body.id);
      return {httpStatus:202,safety:this.beginStop('操作者停止',path === '/api/control/stop' ? Object.keys(this.channels) : [])};
    }
    if (/^\/api\/supplies\/[^/]+\/output$/.test(path) && body.on === false) return {httpStatus:202,safety:this.beginStop('操作者关闭电源',[path.split('/')[3]])};
    const energizing = path.endsWith('/start') && !['/api/recordings/start','/api/connections/start','/api/specialized/start'].includes(path) || path === '/api/coupled-current/load' || path === '/api/coupled-current/target' || path.endsWith('/output') && body.on === true;
    if ((energizing || path === '/api/specialized/start') && this.clearingFault) throw new Error('正在清除故障，请等待设备回读');
    const requestEpoch = this.epoch;
    const keys = path.startsWith('/api/supplies/') ? [path.split('/')[3]] : path.startsWith('/api/coupled-current/') ? Object.keys(this.channels) : path.startsWith('/api/brake-current/') ? ['brake-supply'] : ['motor-supply'];
    await this.claim(headers,{energizing,motor:energizing && /^\/api\/(motor-current|motor|coupled-current|automatic)\//.test(path),restoring:path.endsWith('/connect') || path.endsWith('/read') || path === '/api/connections/start',keys});
    if (requestEpoch !== this.epoch) throw new Error('指令已被停止取消');
    if (['/api/recordings/start','/api/specialized/start','/api/automatic/start'].includes(path)) {
      await this.coupled.finishTail();
      if (requestEpoch !== this.epoch) throw new Error('指令已被停止取消');
    }
    this.observe('operation_request',{path,request:body,controlEpoch:this.epoch});
    const parts = path.split('/').filter(Boolean), action = parts.at(-1);
    if (path.startsWith('/api/test-subjects/')) {
      if (action === 'create') return this.subjects.create(body.kind,body.name);
      if (action === 'save-setup') return this.subjects.saveSetup(body);
      if (action === 'select') return this.subjects.select(body);
    }
    if (path === '/api/control/claim') { this.owner = headers['x-workbench-client']; return this.safety(); }
    if (path === '/api/connections/start') return this.connectAll(body);
    if (path === '/api/recordings/start') { const current = await this.recordings.start(body); return {current}; }
    if (path === '/api/recordings/stop') return {current:await this.recordings.stop(body.id)};
    if (path === '/api/specialized/start') return this.specialized.start(body,'specialized');
    if (path === '/api/specialized/stop') return this.specialized.stop('specialized',body.id);
    if (path === '/api/automatic/start') return this.specialized.start(body,'automatic');
    if (path.startsWith('/api/motor-current/')) {
      if (action === 'clear-fault') {
        if (this.clearingFault) throw new Error('正在清除故障');
        this.clearingFault = true;
        const checkStopped = () => {
          if (requestEpoch !== this.epoch || this.stopping) throw new Error('清故障已被停止操作取消');
          if (this.coupled.run.running || this.specialized.active() || this.motor.run.running || this.brake.run.running || this.device.session) throw new Error('请先结束测试并确认控制会话已关闭');
          if (this.coupled.run.stopConfirmed !== true && this.motor.run.stopConfirmed !== true) throw new Error('请先执行停止并确认输出关闭');
          const snapshot = this.device.snapshot();
          if (snapshot.motor.output_on || snapshot.motor.age_s > 1 || !snapshot.motor.fault && snapshot.motor.state !== 4) throw new Error('没有新鲜的输出关闭故障状态');
        };
        try { await this.device.clearKnownFault(checkStopped); return this.motor.status(); }
        finally { this.clearingFault = false; }
      }
      if (action === 'connect') return this.connectMotor(body);
      if (action === 'disconnect') { if (this.motor.run.running || this.device.state.flags & 1) throw new Error('请先停止输出'); this.cancelMotorConnection(); await this.motorConnectTask?.catch(() => {}); await this.device.close(); return this.motor.status(); }
      if (action === 'start') return this.motor.start(body);
    }
    if (path === '/api/brake-current/start') return this.brake.start(body);
    if (path === '/api/coupled-current/start') return this.coupled.start(body);
    if (path === '/api/coupled-current/target') return this.coupled.setCurrent(body);
    if (path === '/api/coupled-current/load') return this.coupled.load(body);
    if (parts[1] === 'supplies' && parts.length === 4) {
      const channel = this.channels[parts[2]];
      if (!channel) throw new Error('未知电源通道');
      if (this.coupled.run.running && !['read'].includes(action)) throw new Error('请先结束联动测试');
      if (action === 'connect') return channel.connect(body);
      if (action === 'disconnect') return channel.disconnect();
      if (action === 'read') return channel.read();
      if (action === 'set') return channel.setValues(body);
      if (action === 'output') {
        const result = await channel.output(body.on,body);
        if (parts[2] === 'motor-supply' && body.on) {
          this.motorIsolated = false;
          this.waitingForMotor = !this.motor.status().connected;
          this.nextMotorConnect = 0;
          this.pollMotorConnection();
        }
        return result;
      }
      if (action === 'limits') {
        const maxVoltage = finite(body.maxVoltage,'限幅电压',0,24), cap = parts[2] === 'motor-supply' ? 20 : 2.3;
        const maxCurrent = finite(body.maxCurrent,'限幅电流',0,Math.min(cap,channel.config.ratedCurrent ?? Infinity));
        Object.assign(channel.config,{maxVoltage,maxCurrent});return channel.read();
      }
    }
    if (parts[1] === 'sensors' && parts[2] === 'torque-sensor') {
      if (this.specialized.active() && action !== 'read') throw new Error('请先结束专项测试');
      if (action === 'connect') return this.sensor.connect(body);
      if (action === 'disconnect') return this.sensor.disconnect();
      if (action === 'read') return this.sensor.read();
    }
    if (parts[1] === 'motor') return this.manual(action,body);
    throw new Error('接口不存在：' + path);
  }
  async manual(action,data) {
    if (this.coupled.run.running || this.specialized.active()) throw new Error('请先结束当前测试');
    if (action === 'prepare') {
      if (this.motor.run.running || !this.owner) throw new Error('请先申请控制权并停止当前输出');
      const mode = {IQ:1,VELOCITY:3,POSITION:4,MAX_AVAILABLE_IQ:5}[data.mode];
      if (!mode) throw new Error('无效控制模式');
      const mapping = {iqTargetA:[O.iq_target,1000],phaseLimitA:[O.phase_run_limit,1000],busBudgetA:[O.bus_current_budget,1000],busVoltageV:[O.bus_voltage_basis,1000],negativeIdMinA:[O.negative_id_min,1000],speedLimitRpm:[O.speed_soft_limit,1000],velocityRpm:[O.velocity_target,1000],positionTurns:[O.position_target,1000000]};
      const config = {[O.run_mode]:mode,[O.direction]:data.direction ?? 1}, epoch = this.epoch;
      for (const [key,[oid,scale]] of Object.entries(mapping)) if (data[key] != null) config[oid] = oid === O.position_target ? BigInt(Math.round(finite(data[key],key) * scale)) : Math.round(finite(data[key],key) * scale);
      const preparedId = await this.device.prepare(config);
      if (epoch !== this.epoch || this.blocked) throw new Error('准备已被停止取消');
      this.prepared = {id:preparedId,epoch}; return {preparedId,configuration:jsonValue(config)};
    }
    if (action === 'start') {
      const prepared = this.prepared;
      if (!prepared || prepared.id !== data.preparedId || prepared.epoch !== this.epoch) throw new Error('准备结果已失效');
      await this.device.start(prepared.id);
      if (prepared.epoch !== this.epoch || this.blocked) throw new Error('启动已被停止取消');
      Object.assign(this.motor.run,{running:true,phase:'manual',stopConfirmed:null}); this.prepared = null; return this.motor.status();
    }
    if (action === 'target') { if (!this.motor.run.running || this.motor.run.phase !== 'manual') throw new Error('当前没有独立电机输出'); await this.device.setTarget(O.iq_target,Math.round(finite(data.iqTargetA,'目标 Iq') * 1000)); return this.motor.status(); }
    if (action === 'clear-fault') throw new Error('请使用电机清除故障按钮');
    if (action === 'diagnostic') return jsonValue(await this.device.request(OP.read_diagnostic,pack('H',data.kind || 2),{channel:1}));
    if (action === 'frozen') return jsonValue(await this.device.readFrozen());
    throw new Error('未知独立电机操作');
  }
}
