import {MotorDevice} from './motor.js';
import {PowerChannel,TorqueSensor} from './rs485.js';
import {canInterface} from './transports.js';
import {CurrentMotor,BrakeCurrent,CoupledCurrent} from './control.js';
import {TestSubjects,Recordings} from './storage.js';
import {Specialized} from './specialized.js';
import {O,OP} from './mbp-codec.js';
import {finite,jsonValue,now,pack,sleep,utc} from './bytes.js';
export class Bench {
  epoch = 0; owner = null; blocked = false; stopping = false; errors = []; reason = ''; expected = new Set();
  connection = {running:false,report:[],candidates:[]}; histories = [];
  constructor(io,store,emit) {
    this.io = io; this.store = store; this.emit = emit; this.token = crypto.randomUUID();
    this.observe = (kind,data) => { this.recordings?.observe(kind,data); this.emit(kind,data); };
    this.channels = Object.fromEntries(['motor-supply','brake-supply'].map(key => [key,new PowerChannel(key,io)]));
    this.sensor = new TorqueSensor(io,this.observe); this.device = new MotorDevice(io,this.observe);
    this.motor = new CurrentMotor(this.device,this); this.brake = new BrakeCurrent(this.channels['brake-supply']); this.coupled = new CoupledCurrent(this);
    this.subjects = new TestSubjects(store,() => this.busy());
    this.recordings = new Recordings(store,() => this.source(),() => this.subjects.selection()); this.specialized = new Specialized(this);
  }
  async initialize() {
    await this.subjects.initialize(); await this.recordings.initialize(); await this.specialized.initialize();
    const journal = await this.store.get('output-journal');
    if (journal?.dirty) {
      this.recoveryExpected = new Set(journal.expected || []);
      this.recoveryMotor = !!journal.motorMayBeOn;
      this.expected = new Set(journal.expected || []);
      this.blocked = true; this.reason = '上次退出时输出未确认关闭，请重新连接原设备并执行停止确认';
    }
    this.histories = await this.recordings.history();
    this.timers = [setInterval(() => this.publish(),200),setInterval(() => {this.recordings.capture();},50),setInterval(() => {void this.poll();},300)];
  }
  busy() { return !!(this.motor.run.running || this.brake.run.running || this.coupled.run.running || this.recordings.live?.running || this.specialized.active()); }
  source() { return {...Object.fromEntries(Object.entries(this.channels).map(([key,channel]) => [key,channel.status()])), 'torque-sensor':this.sensor.status(),'current-motor':this.motor.status()}; }
  safety() { return {epoch:this.epoch,owned:!!this.owner,blocked:this.blocked,stopping:this.stopping,reason:this.reason,errors:[...this.errors],motorIsolated:!!this.motorIsolated,unusedChannels:Object.entries(this.channels).filter(([,channel]) => !channel.config && !channel.outputUncertain).map(([key]) => key),leaseTimeoutS:3,lastPowerFault:this.lastPowerFault || null}; }
  health() { return {app:'motor-test-bench-hardware',hostApi:1,recordSchema:2,wireProtocol:[1,0],runtime:'browser',token:this.token,safety:this.safety(),channels:this.source()}; }
  routes() {
    return {'/api/health':this.health(),'/api/control/status':this.safety(),'/api/connections/status':this.connection,'/api/motor-current/status':{...this.motor.status(),safety:this.safety()},'/api/brake-current/status':{...this.brake.status(),safety:this.safety()},'/api/coupled-current/status':this.coupled.status(),'/api/recordings/status':{current:this.recordings.status()},'/api/automatic/status':this.specialized.status('automatic',false),'/api/specialized/status':this.specialized.status('specialized',false),'/api/test-subjects':this.subjects.status()};
  }
  publish() {
    if (this.owner && !this.stopping && !this.blocked && !this.coupled.run.running && [this.motor,this.brake].some(engine => engine.run.phase === 'stopped' && engine.run.stopConfirmed === false)) this.beginStop('输出关闭未确认，执行原电源断电兜底',Object.keys(this.channels));
    this.emit('state',{hostApi:1,recordSchema:2,routes:this.routes(),devices:this.source(),control:this.safety(),test:this.coupled.run,recording:this.recordings.status()});
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
    } finally { this.polling = false; }
  }
  async claim(headers,{energizing = false,restoring = false,keys = [],motor = false} = {}) {
    const client = headers['x-workbench-client'], epoch = headers['x-control-epoch'];
    if (!client || client.length < 16 || client.length > 128) throw new Error('请刷新网页以建立控制会话');
    if (String(epoch) !== String(this.epoch)) throw new Error('停止已取消旧指令，请读取状态后重新操作');
    if (this.blocked && !restoring) throw new Error('停机尚未确认，请先重试停止');
    if (this.owner && client !== this.owner) throw new Error('另一控制会话仍在使用设备');
    if (energizing) {
      this.owner = client; keys.filter(key => this.channels[key]?.config).forEach(key => this.expected.add(key));
      if (motor) {this.expected.add('current-motor');this.motorIsolated = false;}
      await this.store.put('output-journal',{dirty:true,expected:[...this.expected],motorMayBeOn:this.expected.has('current-motor')});
    }
  }
  beginStop(reason = '操作者停止',keys = []) {
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
    const brake = this.channels['brake-supply'].config ? attempt('制动',() => this.brake.stop()) : Promise.resolve();
    const drainPowerOff = async () => { while (this.stopKeys.size) {
      const keys = [...this.stopKeys]; this.stopKeys.clear();
      await Promise.all(keys.map(key => {
        if (this.channels[key]?.config) return attempt(key,() => this.channels[key].output(false));
        if (this.recoveryExpected?.has(key)) errors.push(`${key}：请重新连接原电源以确认输出关闭`);
        return Promise.resolve();
      }));
    } };
    await drainPowerOff();
    await Promise.all([motor,brake]);
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
    Object.assign(this.coupled.run,{running:false,phase:'stopped',stopConfirmed:motorOk && !errors.length,stopErrors:errors});
    await drainPowerOff();
    this.stopping = false; this.errors = errors; this.blocked = !motorOk || errors.some(error => !this.motorIsolated || !error.startsWith('电机：'));
    if (!this.blocked) { this.owner = null; this.expected.clear(); }
    const motorMayBeOn = !motorOk || !this.motorIsolated && !!(this.device.state.flags & 1);
    const dirty = this.blocked || motorMayBeOn || Object.values(this.channels).some(channel => channel.outputUncertain);
    const expected = dirty ? Object.keys(this.channels).filter(key => this.channels[key].outputUncertain || this.recoveryExpected?.has(key)) : [];
    if (motorMayBeOn) expected.push('current-motor');
    try { await this.store.put('output-journal',{dirty,expected,motorMayBeOn}); }
    catch (error) { this.errors.push('停机记录保存失败：' + error.message); this.blocked = true; }
    if (!this.blocked) {this.recoveryExpected = null;this.recoveryMotor = false;}
    this.observe('safety_stop_result',this.safety()); this.publish();
  }
  async motorConfig(data = {}) {
    const ports = await this.io.call('ports',{}), selected = await this.io.call('can-config',{});
    const port = ports.find(port => port.port === (data.device || selected.device)) || ports.find(canInterface);
    if (!port) throw new Error('请先授权 USB-CAN 适配器');
    if (!canInterface(port)) throw new Error('所选接口不是已支持的 CAN 适配器');
    return {...data,device:port.port,interface:canInterface(port),baud:500000,node:Number(data.node ?? selected.node ?? 1)};
  }
  async connectAll(hints) {
    if (this.busy() || this.connection.running) throw new Error('试验或记录中不重新扫描');
    this.connection = {running:true,report:[],candidates:[]};
    void (async () => {
      try {
        for (const key of ['motor-supply','brake-supply','torque-sensor','current-motor']) {
          const channel = key === 'current-motor' ? this.motor : key === 'torque-sensor' ? this.sensor : this.channels[key];
          if (channel.status().connected) { this.connection.report.push(`${key}：已连接`); continue; }
          try {
            const config = key === 'current-motor' ? await this.motorConfig(hints[key]) : hints[key];
            if (!config?.device) throw new Error('请在连接配置中指定已授权的串口');
            await channel.connect(config); this.connection.report.push(`${key}：已连接`);
          } catch (error) { this.connection.report.push(`${key}：${error.message}`); }
        }
      } finally { this.connection.running = false; this.publish(); }
    })();
    return this.connection;
  }
  async request(path,method,body,headers = {}) {
    const url = new URL(path,'https://browser.invalid'); path = url.pathname.replace('/api/v1/','/api/');
    const routes = this.routes();
    if (method === 'GET') {
      if (path === '/api/recordings/status') return {current:this.recordings.status(),...(url.searchParams.get('history') !== '0' ? {records:await this.recordings.history()} : {})};
      if (path === '/api/automatic/status' || path === '/api/specialized/status') return this.specialized.status(path.split('/')[2],url.searchParams.get('history') !== '0');
      if (path === '/api/state') return {hostApi:1,recordSchema:2,routes,devices:this.source(),control:this.safety(),test:this.coupled.run,recording:this.recordings.status()};
      if (routes[path]) return routes[path];
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
    const energizing = path.endsWith('/start') && !['/api/recordings/start','/api/connections/start','/api/specialized/start'].includes(path) || path === '/api/coupled-current/load' || path.endsWith('/output') && body.on === true;
    const requestEpoch = this.epoch;
    const keys = path.startsWith('/api/supplies/') ? [path.split('/')[3]] : path.startsWith('/api/coupled-current/') ? Object.keys(this.channels) : path.startsWith('/api/brake-current/') ? ['brake-supply'] : ['motor-supply'];
    await this.claim(headers,{energizing,motor:energizing && /^\/api\/(motor-current|motor|coupled-current|automatic)\//.test(path),restoring:path.endsWith('/connect') || path.endsWith('/read') || path === '/api/connections/start',keys});
    if (requestEpoch !== this.epoch) throw new Error('指令已被停止取消');
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
      if (action === 'connect') return this.motor.connect(await this.motorConfig(body));
      if (action === 'disconnect') { if (this.motor.run.running || this.device.state.flags & 1) throw new Error('请先停止输出'); await this.device.close(); return this.motor.status(); }
      if (action === 'start') return this.motor.start(body);
    }
    if (path === '/api/brake-current/start') return this.brake.start(body);
    if (path === '/api/coupled-current/start') return this.coupled.start(body);
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
          void this.motorConfig().then(config => this.motor.connect(config)).catch(error => {this.motor.error = error.message;});
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
    if (action === 'clear-fault') return this.device.request(OP.clear_fault,new Uint8Array(),{mutation:true});
    if (action === 'diagnostic') return jsonValue(await this.device.request(OP.read_diagnostic,pack('H',data.kind || 2),{channel:1}));
    if (action === 'frozen') return jsonValue(await this.device.readFrozen());
    throw new Error('未知独立电机操作');
  }
}
