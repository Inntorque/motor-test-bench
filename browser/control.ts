import type {RunState, StopEvidence} from './types.ts';
import {finite,now,utc,sleep} from './bytes.ts';
import {O} from './mbp-codec.ts';
import {uid} from './storage.ts';
import {powerProblem} from './rs485.ts';
import {CHARACTERISTIC_INTERVAL_SECONDS,CHARACTERISTIC_WINDOW_SECONDS,CHARACTERISTIC_TIMING_TOLERANCE,StagePointCollector} from './characteristic-points.ts';
const fresh = channel => channel?.connected && utc() - (channel.snapshot?.receivedAt || 0) < 1.5;
export const brakeReference = [[.113077,0],[.4922,.2],[.7739,.25],[1.3149,.3],[1.3986,.35],[2.2483,.375],[2.7863,.4]];
const calibratedMaxTorqueNm = 2.7;
export const torqueMetadata = {
  minTargetNm:0,maxTargetNm:calibratedMaxTorqueNm,maxCurrentA:1.999,control:'open_loop',
  referenceMinNm:.3,referenceMaxNm:calibratedMaxTorqueNm,referenceMaxCurrentA:.4,backgroundNm:.2,date:'2026-10-20',
  reference:brakeReference.map(([torqueNm,commandA]) => ({torqueNm,commandA})),
};
export function brakeCurrentForTorque(target: number) {
  finite(target,'目标力矩',torqueMetadata.minTargetNm,torqueMetadata.maxTargetNm);
  if (target <= brakeReference[0][0]) return 0;
  const index = brakeReference.findIndex(([torque]) => torque >= target);
  const [t0,a0] = brakeReference[index - 1], [t1,a1] = brakeReference[index];
  const current = a0 + (a1 - a0) * (target - t0) / (t1 - t0);
  return Math.min(torqueMetadata.maxCurrentA,Math.round(current * 1000) / 1000);
}
export class StandstillConfirmation {
  static readonly durationSeconds = 5;
  samples = []; pending = false; evidence: StopEvidence = {};
  update(measured,status,eligible) {
    const snapshot = status.snapshot || {}, feedback = snapshot.feedback || {}, motor = snapshot.motor || {}, encoder = snapshot.encoder || {};
    const previous = this.evidence;
    this.evidence = {sensorSpeedRpm:measured.speed,confirmed:false};
    if (!eligible || !Number.isFinite(measured.speed) || !Number.isFinite(measured.torque) || Math.abs(measured.speed) >= 10 ** -Number(measured.speedDecimals ?? 1) * .5) { this.samples = []; this.pending = false; return false; }
    const times = [measured.receivedAt,motor.updated_at,feedback.updated_at,feedback.position_at];
    if (!times.every(t => Number.isFinite(t) && utc() - t >= -.25 && utc() - t <= 1) || ![feedback.speed_rpm,feedback.position_deg].every(Number.isFinite) || feedback.stale || feedback.position_stale || !encoder.valid || encoder.stale || motor.fault || motor.state !== 2 || motor.output_on !== true) {
      this.samples = []; this.pending = false; this.evidence.state = 'encoder_unconfirmed'; return false;
    }
    this.evidence.motorRotorRpm = feedback.speed_rpm;
    if (Math.abs(feedback.speed_rpm) > .1) { this.samples = []; this.pending = false; this.evidence.state = 'encoder_moving'; return false; }
    const stamp = Math.max(feedback.position_at,measured.receivedAt),last=this.samples.at(-1);
    if (last && (feedback.position_at<=last[2] || measured.receivedAt<=last[3])) { this.evidence = {...previous,...this.evidence}; return false; }
    if (last && stamp-last[0]>1)this.samples=[];
    this.samples.push([stamp,feedback.position_deg,feedback.position_at,measured.receivedAt]);
    const positions = this.samples.map(row => row[1]), span = Math.max(...positions) - Math.min(...positions);
    if (span > .5) { this.samples = []; this.pending = false; Object.assign(this.evidence,{state:'encoder_position_moving',positionSpanDeg:span}); return false; }
    this.pending = true; const duration = stamp - this.samples[0][0];
    while (this.samples.length > 128) this.samples.splice(1,1);
    const confirmed = this.samples.length >= 3 && duration >= StandstillConfirmation.durationSeconds;
    Object.assign(this.evidence,{state:confirmed ? 'confirmed' : 'confirming',positionSpanDeg:span,durationS:duration,samples:this.samples.length,confirmed});
    return confirmed;
  }
}
export class CurrentMotor {
  declare device;
  declare env;
  declare stopping;
  declare task: Promise<void> | undefined;
  private checkingReadiness = false;

  run: RunState = {phase:'idle',running:false,reason:'',targetA:0,elapsed:0,steps:0,stopConfirmed:null}; error = ''; generation = 0;
  constructor(device,env) { this.device = device; this.env = env; }
  status() {
    let snapshot = null, connectionError = '';
    try {
      snapshot = this.device.snapshot();
      try { this.healthy(snapshot,true); snapshot.startBlockReason = ''; }
      catch (error) { snapshot.startBlockReason = error.message; }
    } catch (error) {connectionError = error.message;}
    return {connected:!!snapshot,snapshot,error:this.error || connectionError,run:{...this.run},testSubject:this.env.subjects?.selection()};
  }
  healthy(snapshot,starting = false) {
    const {motor,readiness,ntc,encoder,feedback,capabilities} = snapshot;
    if (motor.fault || motor.state === 4) throw new Error(`驱动板保护：${readiness.detail || 'fault'}（分类 ${readiness.faultCategory}，厂家代码 ${readiness.vendorCode}）`);
    if (capabilities.fields & 1 << 10) {
      if (!ntc.valid || ntc.fault || ntc.age_s > 1.5 || ntc.tripped_latched || !Number.isFinite(ntc.temp_c)) throw new Error('温度反馈无效、超时或保护锁存');
      if (!Number.isFinite(ntc.limit_c)) throw new Error('驱动板未返回声明的温度保护边界');
      if (ntc.temp_c >= ntc.limit_c) throw new Error('达到设备 NTC 温度保护边界');
    }
    if ((capabilities.fields & 0xc0) === 0xc0 && (!encoder.valid || encoder.stale)) throw new Error('编码器反馈无效或超时');
    if (starting) {
      if (!(readiness.flags & 1)) throw new Error(readiness.detail || '驱动板未就绪');
      if (motor.state !== 0 || motor.output_on !== false) throw new Error('电机输出尚未关闭');
    } else {
      if (feedback.stale || !Number.isFinite(feedback.iq_a) || (motor.mode === 5 || motor.mode === 1 && capabilities.fields & 1 << 4) && !Number.isFinite(feedback.id_a)) throw new Error('电流 Id/Iq 反馈缺失或超时');
      if (snapshot.limitA && Math.max(Math.abs(feedback.id_a || 0),Math.abs(feedback.iq_a)) > snapshot.limitA * 1.1) throw new Error('实际电流超过设备电流保护限值');
    }
  }
  async connect(config) { await this.device.connect(config); this.error = ''; return this.status(); }
  validate(data) {
    if (data.mode === 'maximum_output') return {step:0,interval:1,duration:0};
    const step = finite(data.stepA,'步长',.001,5), interval = finite(data.intervalS,'间隔',.2,10), duration = finite(data.durationS,'持续时间',1,255);
    if (Math.abs(step * 1000 - Math.round(step * 1000)) > 1e-6) throw new Error('电流分辨率为 0.001 A');
    return {step,interval,duration};
  }
  maximumConfig(data) {
    return {busLimitA:finite(data.busLimitA ?? 5,'母线电流',.1,20),busVoltageV:finite(data.busVoltageV ?? 24,'母线电压',6,26.4),maxPhaseA:finite(data.maxPhaseA ?? 0,'相电流',0,2147483),negativeIdMinA:finite(data.negativeIdMinA ?? 0,'负 Id',-2147483,0),maxSpeedRpm:finite(data.maxSpeedRpm ?? 0,'转速',0,20000)};
  }
  async start(data) {
    if (this.run.running || this.task || this.checkingReadiness) throw new Error('已有电机任务');
    const startGeneration = this.generation;
    this.checkingReadiness = true;
    try { await this.device.refreshReadiness(); }
    finally { this.checkingReadiness = false; }
    if (startGeneration !== this.generation) throw new Error('启动已被停止取消');
    const snapshot = this.device.snapshot(); this.healthy(snapshot,true);
    if (data.mode === 'dyno' || data.mode === 'current_loop') return this.startControl(data,snapshot);
    if (data.mode === 'full_current') throw new Error('固定 10 A 模式已删除，请选择测功模式或电流环模式');
    const {step,interval,duration} = this.validate(data), maximum = data.mode === 'maximum_output', mode = maximum ? 5 : 1;
    if (!(snapshot.capabilities.modes & 1 << mode) || !(snapshot.capabilities.fields & 1 << 2) || maximum && !(snapshot.capabilities.fields & 1 << 4)) throw new Error('设备缺少本次模式或电流反馈能力');
    if (!snapshot.limitA) throw new Error('设备未声明有效相电流保护值');
    let limit = maximum ? snapshot.supportedMaxA : snapshot.limitA;
    if (data.maxTargetA != null) limit = finite(data.maxTargetA,'目标上限',step,Math.min(limit,5));
    if (step > limit) throw new Error('步长超过设备电流上限');
    const config = {[O.run_mode]:mode,[O.direction]:1,[O.iq_target]:0,[O.phase_run_limit]:!duration && !maximum ? Math.round(snapshot.supportedMaxA * 1000) : 0,[O.bus_current_budget]:0,[O.bus_voltage_basis]:0,[O.negative_id_min]:0,[O.speed_soft_limit]:0};
    const maximumConfig = maximum ? this.maximumConfig(data) : null;
    if (maximum) {
      if (!snapshot.capabilities.maximumOutput) throw new Error('驱动板未声明 MAX_AVAILABLE_IQ 能力');
      if (maximumConfig.negativeIdMinA * 1000 < Number(snapshot.limits[O.allowed_negative_id_min] || 0) || maximumConfig.maxPhaseA > snapshot.supportedMaxA) throw new Error('请求超过设备声明的 Id 或相电流运行范围');
      Object.assign(config,{[O.phase_run_limit]:Math.round(maximumConfig.maxPhaseA * 1000),[O.bus_current_budget]:Math.round(maximumConfig.busLimitA * 1000),[O.bus_voltage_basis]:Math.round(maximumConfig.busVoltageV * 1000),[O.negative_id_min]:Math.round(maximumConfig.negativeIdMinA * 1000),[O.speed_soft_limit]:Math.round(maximumConfig.maxSpeedRpm * 1000)});
    }
    const generation = ++this.generation;
    this.run = {id:uid(),phase:'starting',running:true,reason:'',targetA:0,elapsed:0,steps:0,stopConfirmed:null,stepA:step,intervalS:interval,durationS:duration,limitA:limit,deviceLimitA:snapshot.limitA,limitReached:false,peakIqA:0,mode:data.mode || 'step',maximumConfig};
    this.task = this.execute(config,{step,interval,duration,limit,maximum,generation}).finally(() => {this.task = null;});
    return this.status();
  }
  startControl(data,snapshot) {
    if (!snapshot.capabilities[data.mode==='dyno'?'dyno':'currentLoop']) throw new Error('当前驱动未声明所选控制模式的完整能力');
    const targetA = data.mode === 'current_loop' ? finite(data.targetA,'目标 Iq',snapshot.capabilities.currentLoopMinA,snapshot.capabilities.currentLoopMaxA) : 0;
    const generation = ++this.generation;
    this.run = {id:uid(),mode:data.mode,phase:'starting',running:true,reason:'',targetA,elapsed:0,stopConfirmed:null};
    this.task = this.executeControl(data.mode,targetA,generation).finally(() => {this.task = null;});
    return this.status();
  }
  async executeControl(mode,targetA,generation) {
    let reason = '任务结束';
    try {
      await this.device.startControl(mode,targetA,() => {if(generation !== this.generation)throw new Error('启动已被停止取消');});
      if(generation !== this.generation)return;
      const began = now();
      Object.assign(this.run,{phase:'running',startedAt:utc(),limitReached:true,limitReachedAt:began});
      while(generation === this.generation) {
        const snapshot = this.device.snapshot(); this.healthy(snapshot);
        if(snapshot.motor.state !== 2 || !snapshot.motor.output_on || snapshot.motor.mode !== (mode === 'dyno' ? 5 : 1))throw new Error('电机输出已停止或模式变化');
        this.run.elapsed = now()-began;
        if(mode === 'dyno')this.run.targetA = snapshot.feedback.iq_reference_a;
        await sleep(50);
      }
    } catch(error) {reason = error.message; this.error = reason;}
    finally {
      if(generation === this.generation) {
        try {await this.stop();} catch(error) {this.error = `${reason}；${error.message}`;}
        this.run.reason = reason;
      }
    }
  }
  async execute(config,options) {
    const {step,interval,duration,limit,maximum,generation} = options;
    let began, reason = '持续时间到期';
    try {
      const prepared = await this.device.prepare(config);
      if (generation !== this.generation) return;
      await this.device.start(prepared);
      if (generation !== this.generation) return;
      began = now(); this.run.phase = 'running'; this.run.startedAt = utc();
      if (maximum) Object.assign(this.run,{maximumRequested:true,maximumRequestedAt:now()});
      let nextStep = began + (duration ? interval : 0);
      while (generation === this.generation) {
        const elapsed = now() - began; this.run.elapsed = elapsed;
        if (duration && elapsed >= duration) break;
        const snapshot = this.device.snapshot(); this.healthy(snapshot);
        this.run.peakIqA = Math.max(this.run.peakIqA,Math.abs(snapshot.feedback.iq_a));
        if (![1,2].includes(snapshot.motor.state) || !snapshot.motor.output_on || snapshot.motor.mode !== config[O.run_mode]) throw new Error('电机输出已停止或模式变化');
        if (maximum) this.run.targetA = snapshot.feedback.iq_reference_a;
        else if (now() >= nextStep && this.run.targetA < limit) {
          if (duration && now() - nextStep > interval) throw new Error('通信耗时超过步进周期，请增大间隔');
          const target = Math.min(limit,Math.round((this.run.targetA + (duration ? step : limit)) * 1000) / 1000);
          await this.device.setTarget(O.iq_target,Math.round(target * 1000));
          if (generation !== this.generation) break;
          Object.assign(this.run,{targetA:target,lastTargetA:target,steps:this.run.steps + 1}); nextStep = now() + interval;
          if (target >= limit && !this.run.limitReached) Object.assign(this.run,{limitReached:true,limitReachedAt:now()});
        }
        await sleep(50);
      }
    } catch (error) { reason = error.message; this.error = reason; }
    finally {
      if (generation === this.generation) {
        try { await this.stop(); } catch (error) { this.error = `${reason}；${error.message}`; }
        this.run.reason = reason;
      }
    }
  }
  async stop() {
    this.generation++;
    if (this.stopping) return this.stopping;
    this.run.stopConfirmed = false;
    this.stopping = (async () => {
      try { await this.device.stop(); Object.assign(this.run,{running:false,phase:'stopped',targetA:0,stopConfirmed:true,stopMethod:'device-disabled'}); return this.status(); }
      catch (error) { this.run.running = false; this.run.phase = 'stopped'; this.error = error.message; throw error; }
      finally { this.stopping = null; }
    })();
    return this.stopping;
  }
}
export class BrakeCurrent {
  declare channel;
  declare task: Promise<void> | undefined;

  run: RunState = {running:false,phase:'idle',targetA:0,steps:0,elapsed:0,reason:'',stopConfirmed:null}; generation = 0;
  constructor(channel) { this.channel = channel; }
  status() { return {run:{...this.run},device:this.channel.status()}; }
  async start(data) {
    if (this.run.running) throw new Error('已有制动任务');
    const hold = data.mode === 'hold', step = finite(hold ? data.currentA : data.stepA,'制动电流',hold ? 0 : .001,2.3), interval = hold ? .5 : finite(data.intervalS,'间隔',.2,10), duration = finite(data.durationS,'持续时间',1,255);
    if (!this.channel.status().connected) throw new Error('制动电源未连接');
    const generation = ++this.generation;
    this.run = {id:uid(),running:true,phase:'starting',targetA:0,steps:0,elapsed:0,reason:'',stopConfirmed:null};
    this.task = (async () => {
      let reason = '持续时间到期';
      try {
        await this.channel.output(false); await this.channel.setValues({voltage:24,current:hold ? step : 0});
        if (generation !== this.generation) return;
        await this.channel.output(true,{voltage:24,current:hold ? step : 0});
        const began = now(); let next = began + interval; this.run.phase = 'running';
        while (generation === this.generation && now() - began < duration) {
          this.run.elapsed = now() - began;
          if (!hold && now() >= next) { this.run.targetA = Math.min(2.3,Math.round((this.run.targetA + step) * 1000) / 1000); await this.channel.setValues({voltage:24,current:this.run.targetA}); this.run.steps++; next = now() + interval; }
          await sleep(50);
        }
      } catch (error) { reason = error.message; }
      finally { if (generation === this.generation) { try { await this.stop(); } catch (error) { reason += '；' + error.message; } this.run.reason = reason; } }
    })();
    return this.status();
  }
  async stop() {
    this.generation++;this.run.stopConfirmed = false;
    try {await this.channel.output(false);Object.assign(this.run,{targetA:0,stopConfirmed:true});}
    finally {Object.assign(this.run,{running:false,phase:'stopped'});}
    return this.status();
  }
}
export class CoupledCurrent {
  declare env;
  declare task: Promise<void> | undefined;
  private cancelTail = false;
  private loadClock = 0;

  run: RunState = {running:false,phase:'idle',reason:'',stopConfirmed:null}; generation = 0;
  constructor(env) { this.env = env; }
  status(motor = this.env.motor.status(),brake = this.env.brake.status(),safety = this.env.safety()) { return {run:structuredClone(this.run),motor,brake,safety,capabilities:{torqueSweep:true,fullIqRamp:true,maximumOutput:!!motor.snapshot?.capabilities?.maximumOutput},torqueCalibration:torqueMetadata}; }
  async finishTail() {
    if (this.run.running || !this.task) return;
    this.cancelTail = true;
    await this.task;
  }
  async start(data) {
    if (this.run.running || this.run.stopConfirmed === false || this.env.motor.run.running || this.env.brake.run.running) throw new Error('已有输出任务或输出关闭未确认');
    await this.finishTail();
    this.cancelTail = false;
    if (data.mode !== 'torque_sweep') return this.startHold(data);
    const c = data.brake || {}, config = {
      startNm:finite(c.startNm,'起点力矩',0,torqueMetadata.maxTargetNm),
      stepNm:finite(c.stepNm,'力矩步长',.01,1),
      maxNm:Math.min(finite(c.maxNm,'结束力矩',Number.MIN_VALUE,99.9),torqueMetadata.maxTargetNm),
      intervalS:CHARACTERISTIC_INTERVAL_SECONDS,
    };
    if (config.startNm > config.maxNm) throw new Error('起始力矩不能超过结束力矩');
    const generation = ++this.generation;
    this.run = {id:uid(),mode:'torque_sweep',running:true,phase:'checking',reason:'',elapsed:0,stopConfirmed:null,motorMode:data.motorMode || 'dyno',torqueConfig:config,targetNm:0,measuredNm:null,brakeCurrentA:0,loadSteps:0,motorName:this.env.subjects?.selection()?.motor?.name,characteristicSchema:2,characteristicPoints:[]};
    this.task = this.sweep(data,generation); return this.status();
  }
  async startHold(data) {
    if (data.brake?.mode !== 'hold' || Number(data.motor?.durationS) !== Number(data.brake.durationS)) throw new Error('联动两路需要相同持续时间及固定制动模式');
    const generation = ++this.generation;
    this.run = {id:uid(),running:true,phase:'checking',reason:'',elapsed:0,stopConfirmed:null};
    this.task = (async () => {
      try {
        await this.env.motor.start(data.motor); await this.env.brake.start(data.brake);
        while (generation === this.generation && this.env.motor.run.running && this.env.brake.run.running) await sleep(50);
      } catch (error) { this.run.reason = error.message; }
      finally { if (generation === this.generation) await this.stop(); }
    })();
    return this.status();
  }
  async setCurrent(data) {
    if(!this.run.running || this.run.motorMode !== 'current_loop' || this.run.id !== data.id || this.run.loadRequested || !['ramping','awaiting_load'].includes(this.run.phase))throw new Error('仅可在电流环空载准备阶段调整目标');
    const generation = this.generation, motor = this.env.motor;
    await motor.device.setControlCurrent(data.targetA);
    if(generation !== this.generation)throw new Error('电流设置已被停止取消');
    motor.run.targetA = Number(data.targetA); this.run.phase = 'ramping';
    return this.status();
  }
  load(data) {
    if (!this.run.running || this.run.phase !== 'awaiting_load' || data.id !== this.run.id || this.run.loadRequested) throw new Error('请先完成满转准备，再开始加载');
    const accepted = utc(), clicked = data.clickedAt ?? accepted;
    if (!Number.isFinite(clicked) || clicked < this.run.startedAt || clicked > accepted + .05 || accepted - clicked > 1)
      throw new Error('加载请求时间已过期，请重新开启力矩输出');
    const start = Math.min(clicked, accepted);
    this.loadClock = now() - (accepted - start);
    Object.assign(this.run, {loadRequested:true, loadStartedAt:start, curveStartedAt:start, loadElapsedS:0});
    this.env.observe('characteristic_start',{runId:this.run.id,receivedAt:start,motorName:this.run.motorName,characteristicSchema:2});
    return this.status();
  }
  async sweep(data,generation) {
    const env = this.env, brakeChannel = env.channels['brake-supply'], standstill = new StandstillConfirmation(), samples = [];
    let reason = '任务结束', recording, began, loadAt, lastSensor, rotated = false, commandedStage = 0;
    let commandConfirmedAt = Infinity;
    let collector: StagePointCollector | null = null;
    let brakeTask: Promise<void> | null = null, brakeError: Error | null = null;
    const savePoint = point => {if(point){this.run.characteristicPoints.push(point);env.observe('characteristic_point',point);}};
    try {
      if (!fresh(env.sensor.status())) throw new Error('扭矩传感器未连接或读数过期');
      const power = await env.channels['motor-supply'].read();
      if (!power.connected || !(power.snapshot.status & 128) || powerProblem('motor-supply',power.config,power.snapshot)) throw new Error('请先开启电机电源并确认供电');
      const motorSnapshot = env.motor.status().snapshot, required = (1 << 2) | (1 << 5) | (1 << 6) | (1 << 7);
      if (!motorSnapshot || (motorSnapshot.capabilities.fields & required) !== required) throw new Error('加载测试缺少 Iq、转速或多圈位置反馈能力');
      await brakeChannel.output(false); await brakeChannel.setValues({voltage:24,current:0});
      if (generation !== this.generation) return;
      if (!env.recordings.live?.running) { const report = await env.recordings.start({name:'满输出与力矩加载试验'}); recording = report.id; }
      this.run.recordingId = recording || env.recordings.live?.id;
      if (generation !== this.generation) return;
      await env.motor.start({mode:this.run.motorMode,busLimitA:20,busVoltageV:24,...Object.fromEntries(['targetA','negativeIdMinA','maxPhaseA','maxSpeedRpm'].filter(key => data[key] != null).map(key => [key,data[key]]))});
      await env.device.wait(() => !env.motor.run.running || env.motor.run.phase === 'running',5000,'电机启动超时');
      if (!env.motor.run.running) throw new Error(env.motor.run.reason || env.motor.error || '电机启动失败');
      began = now(); Object.assign(this.run,{phase:'ramping',startedAt:utc()});
      let target = 0, stageSlot = 1;
      while (generation === this.generation) {
        if(brakeError)throw brakeError;
        const sensor = env.sensor.status(), supply = env.channels['motor-supply'].status(), motor = env.motor.status();
        if (!fresh(sensor)) throw new Error('扭矩传感器反馈过期');
        if (!supply.connected || !(supply.snapshot.status & 128) || utc() - supply.snapshot.receivedAt > 2) throw new Error('电机电源关闭或反馈过期');
        if (!motor.run.running) throw new Error(motor.run.reason || env.motor.error || '电机输出任务已结束');
        const measured = sensor.snapshot;
        if (lastSensor !== measured.receivedAt) { lastSensor = measured.receivedAt; samples.push([now(),Math.abs(measured.torque)]); }
        while (samples.length && samples[0][0] < now() - .5) samples.shift();
        const actual = samples.reduce((sum,row) => sum + row[1],0) / samples.length;
        const moving = Math.abs(measured.speed) > 10 ** -Number(measured.speedDecimals ?? 1); rotated ||= moving;
        const full = motor.run.mode === 'maximum_output' ? motor.run.maximumRequested : motor.run.limitReached;
        const energized=motor.snapshot?.motor?.output_on===true && !motor.snapshot.motor.fault;
        const brake=brakeChannel.status(),wasConfirming=standstill.pending;
        if (!brakeTask && fresh(brake) && Boolean(brake.snapshot.status & 128) !== Boolean(this.run.brakeEnabled))
          throw new Error(this.run.brakeEnabled?'制动输出意外关闭':'零励磁阶段制动输出意外开启');
        const outputConfirmed = commandedStage === this.run.loadSteps && measured.receivedAt >= commandConfirmedAt;
        const stopped = standstill.update(measured,motor,loadAt!=null && outputConfirmed && rotated && full && energized && fresh(brake) && !!(brake.snapshot.status & 128));
        this.run.stopConfirmation = {...standstill.evidence};
        if (standstill.pending) this.run.stallDetectedAt ??= measured.receivedAt;
        else delete this.run.stallDetectedAt;
        if (stopped) {
          savePoint(collector?.stall(measured,supply));
          this.run.stallEndpoint = {...measured,encoderConfirmation:{...standstill.evidence}};
          this.run.measurementEndedAt = measured.receivedAt;
          Object.assign(this.run,{measuredNm:Math.abs(measured.torque),measuredSpeedRpm:0,elapsed:now()-began});
          reason='加载中连续零速 5 秒且编码器确认静止，已记录本次工况堵转扭矩和特性图';break;
        }
        Object.assign(this.run,{measuredNm:actual,measuredSpeedRpm:measured.speed,elapsed:now() - began});
        if (full && moving && this.run.phase === 'ramping') this.run.phase = 'awaiting_load';
        if (this.run.loadRequested && loadAt == null) {
          loadAt = this.loadClock;
          target = this.run.torqueConfig.startNm;
          const startedAt = this.run.loadStartedAt, stage = {targetNm:target,startedAt,scheduledAt:startedAt};
          Object.assign(this.run,{phase:'loading',targetNm:target,loadSteps:1,loadStages:[stage]});
          collector = new StagePointCollector(this.run.id,0,target,startedAt,this.run.torqueConfig.intervalS);
          env.observe('torque_stage',stage);
        }
        if(loadAt!=null)this.run.loadElapsedS=Math.max(0,now()-loadAt);
        if(wasConfirming && !standstill.pending && collector) {
          stageSlot=Math.max(stageSlot,Math.ceil((now()-loadAt+CHARACTERISTIC_WINDOW_SECONDS)/this.run.torqueConfig.intervalS));
          const scheduledStart=this.run.loadStartedAt+(stageSlot-1)*this.run.torqueConfig.intervalS;
          collector=new StagePointCollector(this.run.id,this.run.loadSteps-1,target,measured.receivedAt,scheduledStart+this.run.torqueConfig.intervalS-measured.receivedAt);
        }
        if(collector && outputConfirmed && full && energized && !standstill.pending)collector.update(measured,supply);
        const deadline=loadAt == null ? Infinity : loadAt+stageSlot*this.run.torqueConfig.intervalS;
        if(collector && !standstill.pending && now()>=deadline){
          if(now()-deadline>CHARACTERISTIC_TIMING_TOLERANCE || brakeTask || commandedStage!==this.run.loadSteps)throw new Error('固定周期到点时控制或通信未完成；停止加载，不补发、不延长周期');
          if(!full || !energized)throw new Error('固定周期到点时电机输出无效，未生成特性点');
          savePoint(collector.finish());
          if(target>=this.run.torqueConfig.maxNm){
            reason=target===torqueMetadata.maxTargetNm?'标定范围内的力矩阶梯完成，保留实测结果':'设定力矩阶梯完成，保留实测结果';
            break;
          }
          target=Math.min(this.run.torqueConfig.maxNm,Math.round((target+this.run.torqueConfig.stepNm)*1000000)/1000000);
          const startedAt=utc(),scheduledAt=this.run.loadStartedAt+stageSlot*this.run.torqueConfig.intervalS;
          stageSlot++;
          this.run.targetNm=target;this.run.loadSteps++;this.run.loadStages.push({targetNm:target,startedAt,scheduledAt});
          env.observe('torque_stage',this.run.loadStages.at(-1));
          collector=new StagePointCollector(this.run.id,this.run.loadSteps-1,target,scheduledAt,this.run.torqueConfig.intervalS);
        }
        const stageEnd=loadAt == null ? Infinity : loadAt+stageSlot*this.run.torqueConfig.intervalS;
        if (!fresh(brake) && !brakeTask) throw new Error('制动电源反馈过期');
        if (loadAt != null && commandedStage!==this.run.loadSteps && !standstill.pending && !brakeTask && now()<stageEnd-CHARACTERISTIC_WINDOW_SECONDS) {
          const current = brakeCurrentForTorque(target), stage = this.run.loadSteps, commandedTarget = target;
          brakeTask=(async()=>{
            const values = {voltage:24,current};
            if (this.run.brakeEnabled) await brakeChannel.setValues(values);
            else if (current > 0) {
              await brakeChannel.output(true,values);
              if (generation !== this.generation) return;
              this.run.brakeEnabled = true;
            }
            if (generation !== this.generation) return;
            commandedStage = stage;
            commandConfirmedAt = utc();
            this.run.brakeCurrentA = current;
            env.observe('torque_feedback',{id:this.run.id,targetNm:commandedTarget,measuredNm:actual,currentA:current});
          })().catch(error=>{if(generation===this.generation)brakeError=error;}).finally(()=>{brakeTask=null;});
        }
        await sleep(30);
      }
    } catch (error) { reason = error.message; }
    finally {
      if (generation === this.generation) { this.run.reason = reason; this.run.measurementEndedAt ??= utc(); await this.stop(); }
      if(this.run.characteristicPoints.length){
        const {id,motorName,loadStartedAt}=this.run;
        env.observe('characteristic_end',{id,motorName,loadStartedAt});
      }
      await brakeTask;
      if (recording && env.recordings.live?.id === recording && env.recordings.live.running) {
        await this.collectStopTail(recording);
        env.sensor.flushFrames?.();
        if (env.recordings.live?.id === recording && env.recordings.live.running) await env.recordings.stop(recording);
      }
    }
  }
  private async collectStopTail(recording) {
    const run = this.run, env = this.env;
    while (run.running && !this.cancelTail) await sleep(30);
    if (this.cancelTail || !run.measurementEndedAt || !run.startedAt) return;
    if (!run.stopConfirmed) {
      run.postStopTailState = 'unconfirmed';
      env.observe('post_stop_tail_end',{runId:run.id,recordingId:recording,state:run.postStopTailState,zero:null});
      return;
    }
    run.postStopTailState = 'collecting';
    env.observe('post_stop_tail_start',{runId:run.id,recordingId:recording,measurementEndedAt:run.measurementEndedAt});
    const deadline = now() + 15;
    let lastStamp = run.measurementEndedAt, lastSequence = -1, zeroSince = null, endpoint = null;
    while (!this.cancelTail && this.run === run && now() < deadline && env.recordings.live?.id === recording && env.recordings.live.running) {
      const sensor = env.sensor.status(), sample = sensor.snapshot;
      if (!fresh(sensor)) { run.postStopTailState = 'disconnected'; break; }
      if (sample.receivedAt > lastStamp && (sample.framesReceived == null || sample.framesReceived > lastSequence)) {
        lastStamp = sample.receivedAt; lastSequence = sample.framesReceived ?? lastSequence;
        if (Number.isFinite(sample.torque) && sample.speed === 0) {
          zeroSince ??= sample.receivedAt;
          endpoint = {receivedAt:sample.receivedAt,torque:sample.torque,speed:0};
          if (sample.receivedAt - zeroSince >= .5) { run.postStopZero = endpoint; run.postStopTailState = 'zero'; break; }
        } else { zeroSince = null; endpoint = null; }
      }
      await sleep(50);
    }
    if (run.postStopTailState === 'collecting') run.postStopTailState = this.cancelTail ? 'cancelled' : 'timeout';
    env.observe('post_stop_tail_end',{runId:run.id,recordingId:recording,state:run.postStopTailState,zero:run.postStopZero || null});
  }
  async stop() {
    this.generation++; this.run.phase = 'stopping'; this.run.measurementEndedAt ??= utc();
    const results = await Promise.allSettled([this.env.motor.device.bus && !this.env.motor.device.bus.closed ? this.env.motor.stop() : Promise.resolve(),this.env.channels['brake-supply'].config ? this.env.brake.stop() : Promise.resolve()]);
    const errors = results.filter(result => result.status === 'rejected').map(result => result.reason.message);
    Object.assign(this.run,{running:false,phase:'stopped',stopConfirmed:!errors.length,stopErrors:errors});
    return this.status();
  }
}
