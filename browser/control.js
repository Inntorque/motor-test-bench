import {finite,now,utc,sleep} from './bytes.js';
import {O} from './mbp-codec.js';
import {uid} from './storage.js';
import {powerProblem} from './rs485.js';
const fresh = channel => channel?.connected && utc() - (channel.snapshot?.receivedAt || 0) < 1.5;
export const brakeReference = [[.113077,0],[.4922,.2],[.7739,.25],[1.3149,.3],[1.3986,.35],[2.2483,.375],[2.7863,.4]];
export const torqueMetadata = {minTargetNm:0,referenceMinNm:.3,maxTargetNm:99.9,maxCurrentA:1.999,referenceMaxNm:2.7,referenceMaxCurrentA:.4,backgroundNm:.2,date:'2026-10-20',feedback:'measured_shaft_torque',reference:brakeReference.map(([torqueNm,commandA]) => ({torqueNm,commandA}))};
const clamp = (value,low,high) => Math.max(low,Math.min(high,value));
export class BrakeTorque {
  target = 0; current = 0; demand = 0;
  seed(target) {
    if (!target) return 0;
    for (let i = 1; i < brakeReference.length; i++) {
      const [t0,a0] = brakeReference[i - 1], [t1,a1] = brakeReference[i];
      if (target >= t0 && target <= t1) return a0 + (a1 - a0) * (target - t0) / (t1 - t0);
    }
    throw new Error('目标力矩没有实测参考');
  }
  update(target,measured) {
    finite(target,'目标力矩',0,99.9); finite(measured,'实测力矩');
    if (!target) { this.target = this.current = this.demand = 0; return 0; }
    const seed = t => t >= .3 ? this.seed(Math.min(t,2.7)) : 0;
    if (target !== this.target) { this.demand += seed(target) - seed(this.target); this.target = target; }
    const error = target - measured;
    if (Math.abs(error) <= .05) this.demand = this.current;
    else { if ((this.demand - this.current) * error < 0) this.demand = this.current; this.demand += clamp(error * .05,-.01,.01); }
    this.demand = clamp(this.demand,0,1.999);
    this.current = Math.round(clamp(this.current + clamp(this.demand - this.current,-.025,.025),0,1.999) * 1000) / 1000;
    return this.current;
  }
}
export class StandstillConfirmation {
  samples = []; pending = false; evidence = {};
  update(measured,status,eligible) {
    const snapshot = status.snapshot || {}, feedback = snapshot.feedback || {}, motor = snapshot.motor || {}, encoder = snapshot.encoder || {};
    const previous = this.evidence;
    this.evidence = {sensorSpeedRpm:measured.speed,confirmed:false};
    if (!eligible || Math.abs(measured.speed) >= 10 ** -Number(measured.speedDecimals ?? 1) * .5) { this.samples = []; this.pending = false; return false; }
    const times = [motor.updated_at,feedback.updated_at,feedback.position_at];
    if (!times.every(t => Number.isFinite(t) && utc() - t >= -.25 && utc() - t <= 1) || ![feedback.speed_rpm,feedback.position_deg].every(Number.isFinite) || feedback.stale || feedback.position_stale || !encoder.valid || encoder.stale || motor.state !== 2 || motor.output_on !== true) {
      this.samples = []; this.pending = false; this.evidence.state = 'encoder_unconfirmed'; return false;
    }
    this.evidence.motorRotorRpm = feedback.speed_rpm;
    if (Math.abs(feedback.speed_rpm) > .1) { this.samples = []; this.pending = false; this.evidence.state = 'encoder_moving'; return false; }
    const stamp = feedback.position_at;
    if (this.samples.length && stamp <= this.samples.at(-1)[0]) { this.evidence = {...previous,...this.evidence}; return false; }
    this.samples.push([stamp,feedback.position_deg]);
    const positions = this.samples.map(row => row[1]), span = Math.max(...positions) - Math.min(...positions);
    if (span > .5) { this.samples = [[stamp,feedback.position_deg]]; this.pending = false; Object.assign(this.evidence,{state:'encoder_position_moving',positionSpanDeg:span}); return false; }
    this.pending = true; const duration = stamp - this.samples[0][0];
    while (this.samples.length > 128) this.samples.splice(1,1);
    const confirmed = this.samples.length >= 3 && duration >= 1.5;
    Object.assign(this.evidence,{state:confirmed ? 'confirmed' : 'confirming',positionSpanDeg:span,durationS:duration,samples:this.samples.length,confirmed});
    return confirmed;
  }
}
export class CurrentMotor {
  run = {phase:'idle',running:false,reason:'',targetA:0,elapsed:0,steps:0,stopConfirmed:null}; error = ''; generation = 0;
  constructor(device,env) { this.device = device; this.env = env; }
  status() {
    let snapshot = null;
    try {
      snapshot = this.device.snapshot();
      try { this.healthy(snapshot,true); snapshot.startBlockReason = ''; }
      catch (error) { snapshot.startBlockReason = error.message; }
    } catch {}
    return {connected:!!snapshot,snapshot,error:this.error,run:{...this.run},testSubject:this.env.subjects?.selection()};
  }
  healthy(snapshot,starting = false) {
    const {motor,readiness,ntc,encoder,feedback,capabilities} = snapshot;
    if (motor.fault || motor.state === 4) throw new Error(`驱动板保护：${readiness.detail || 'fault'}（分类 ${readiness.faultCategory}，厂家代码 ${readiness.vendorCode}）`);
    if (capabilities.fields & 1 << 10) {
      if (!ntc.valid || ntc.fault || ntc.age_s > 1.5 || ntc.tripped_latched || !Number.isFinite(ntc.temp_c)) throw new Error('温度反馈无效、超时或保护锁存');
      if (Number.isFinite(ntc.limit_c) && ntc.temp_c >= ntc.limit_c) throw new Error('达到设备 NTC 温度保护边界');
    }
    if ((capabilities.fields & 0xc0) === 0xc0 && (!encoder.valid || encoder.stale)) throw new Error('编码器反馈无效或超时');
    if (starting) {
      if (!(readiness.flags & 1)) throw new Error(readiness.detail || '驱动板未就绪');
      if (motor.state !== 0 || motor.output_on !== false) throw new Error('电机输出尚未关闭');
    } else {
      if (feedback.stale || !Number.isFinite(feedback.iq_a) || motor.mode === 5 && !Number.isFinite(feedback.id_a)) throw new Error('电流 Id/Iq 反馈缺失或超时');
      if (snapshot.limitA && Math.max(Math.abs(feedback.id_a || 0),Math.abs(feedback.iq_a)) > snapshot.limitA * 1.1) throw new Error('实际电流超过设备电流保护限值');
    }
  }
  async connect(config) { await this.device.connect(config); this.error = ''; return this.status(); }
  validate(data) {
    if (['maximum_output','full_current'].includes(data.mode)) return {step:0,interval:1,duration:0};
    const step = finite(data.stepA,'步长',.001,5), interval = finite(data.intervalS,'间隔',.2,10), duration = finite(data.durationS,'持续时间',1,255);
    if (Math.abs(step * 1000 - Math.round(step * 1000)) > 1e-6) throw new Error('电流分辨率为 0.001 A');
    return {step,interval,duration};
  }
  maximumConfig(data) {
    return {busLimitA:finite(data.busLimitA ?? 5,'母线电流',.1,20),busVoltageV:finite(data.busVoltageV ?? 24,'母线电压',6,26.4),maxPhaseA:finite(data.maxPhaseA ?? 0,'相电流',0,2147483),negativeIdMinA:finite(data.negativeIdMinA ?? 0,'负 Id',-2147483,0),maxSpeedRpm:finite(data.maxSpeedRpm ?? 0,'转速',0,20000)};
  }
  async start(data) {
    if (this.run.running || this.task) throw new Error('已有电机任务');
    const snapshot = this.device.snapshot(); this.healthy(snapshot,true);
    const {step,interval,duration} = this.validate(data), maximum = data.mode === 'maximum_output', mode = maximum ? 5 : 1;
    if (!(snapshot.capabilities.modes & 1 << mode) || !(snapshot.capabilities.fields & 1 << 2) || maximum && !(snapshot.capabilities.fields & 1 << 4)) throw new Error('设备缺少本次模式或电流反馈能力');
    if (!snapshot.limitA) throw new Error('设备未声明有效相电流保护值');
    let limit = maximum ? snapshot.supportedMaxA : data.mode === 'full_current' ? snapshot.fullTargetA : snapshot.limitA;
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
    this.run = {phase:'starting',running:true,reason:'',targetA:0,elapsed:0,steps:0,stopConfirmed:null,stepA:step,intervalS:interval,durationS:duration,limitA:limit,deviceLimitA:snapshot.limitA,limitReached:false,peakIqA:0,mode:data.mode || 'step',maximumConfig};
    this.task = this.execute(config,{step,interval,duration,limit,maximum,generation}).finally(() => {this.task = null;});
    return this.status();
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
  run = {running:false,phase:'idle',targetA:0,steps:0,elapsed:0,reason:'',stopConfirmed:null}; generation = 0;
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
  run = {running:false,phase:'idle',reason:'',stopConfirmed:null}; generation = 0;
  constructor(env) { this.env = env; }
  status() { return {run:structuredClone(this.run),motor:this.env.motor.status(),brake:this.env.brake.status(),safety:this.env.safety(),capabilities:{torqueSweep:true,fullIqRamp:true,maximumOutput:!!this.env.motor.status().snapshot?.capabilities?.maximumOutput},torqueCalibration:torqueMetadata}; }
  async start(data) {
    if (this.run.running || this.run.stopConfirmed === false || this.env.motor.run.running || this.env.brake.run.running) throw new Error('已有输出任务或输出关闭未确认');
    if (data.mode !== 'torque_sweep') return this.startHold(data);
    const c = data.brake || {}, config = {startNm:finite(c.startNm,'起点力矩',0,99.9),stepNm:finite(c.stepNm,'力矩步长',.01,1),maxNm:finite(c.maxNm,'结束力矩',Number.MIN_VALUE,99.9),intervalS:finite(c.intervalS,'保持时间',.5,30)};
    if (config.startNm > config.maxNm) throw new Error('起始力矩不能超过结束力矩');
    const generation = ++this.generation;
    this.run = {id:uid(),mode:'torque_sweep',running:true,phase:'checking',reason:'',elapsed:0,stopConfirmed:null,motorMode:data.motorMode || 'full_current',torqueConfig:config,targetNm:0,measuredNm:null,brakeCurrentA:0,loadSteps:0};
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
  load(data) {
    if (!this.run.running || this.run.phase !== 'awaiting_load' || data.id !== this.run.id || this.run.loadRequested) throw new Error('请先完成满转准备，再开始加载');
    this.run.loadRequested = true; return this.status();
  }
  async sweep(data,generation) {
    const env = this.env, controller = new BrakeTorque(), standstill = new StandstillConfirmation(), samples = [];
    let reason = '任务结束', recording, began, loadAt, holding, currentLimitAt, lastSensor, rotated = false;
    try {
      if (!fresh(env.sensor.status())) throw new Error('扭矩传感器未连接或读数过期');
      const power = await env.channels['motor-supply'].read();
      if (!power.connected || !(power.snapshot.status & 128) || powerProblem('motor-supply',power.config,power.snapshot)) throw new Error('请先开启电机电源并确认供电');
      const motorSnapshot = env.motor.status().snapshot, required = (1 << 2) | (1 << 5) | (1 << 6) | (1 << 7);
      if (!motorSnapshot || (motorSnapshot.capabilities.fields & required) !== required) throw new Error('加载测试缺少 Iq、转速或多圈位置反馈能力');
      await env.channels['brake-supply'].output(false); await env.channels['brake-supply'].setValues({voltage:24,current:0});
      if (generation !== this.generation) return;
      if (!env.recordings.live?.running) { const report = await env.recordings.start({name:'满输出与力矩加载试验'}); recording = report.id; }
      this.run.recordingId = recording || env.recordings.live?.id;
      if (generation !== this.generation) return;
      await env.motor.start({mode:this.run.motorMode,busLimitA:20,busVoltageV:24,...Object.fromEntries(['negativeIdMinA','maxPhaseA','maxSpeedRpm'].filter(key => data[key] != null).map(key => [key,data[key]]))});
      await env.device.wait(() => !env.motor.run.running || env.motor.run.phase === 'running',5000,'电机启动超时');
      if (!env.motor.run.running) throw new Error(env.motor.run.reason || env.motor.error || '电机启动失败');
      began = now(); Object.assign(this.run,{phase:'ramping',startedAt:utc()});
      let target = 0, nextPoll = now();
      while (generation === this.generation) {
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
        const fullAt = motor.run.mode === 'maximum_output' ? motor.run.maximumRequestedAt : motor.run.limitReachedAt;
        const stopped = standstill.update(measured,motor,rotated || full && fullAt != null && now() - fullAt >= 2);
        this.run.stopConfirmation = {...standstill.evidence};
        if (standstill.pending) this.run.stallDetectedAt ??= measured.receivedAt;
        else delete this.run.stallDetectedAt;
        if (stopped) { this.run.stallEndpoint = {...measured,encoderConfirmation:{...standstill.evidence}}; this.run.measurementEndedAt = measured.receivedAt; throw new Error('编码器连续确认电机轴静止，且扭矩传感器报零；结束加载并保留实测终点'); }
        Object.assign(this.run,{measuredNm:actual,measuredSpeedRpm:measured.speed,elapsed:now() - began});
        if (full && moving && this.run.phase === 'ramping') this.run.phase = 'awaiting_load';
        if (this.run.loadRequested && loadAt == null) { loadAt = now(); target = this.run.torqueConfig.startNm; Object.assign(this.run,{phase:'loading',loadStartedAt:utc(),curveStartedAt:Math.max(this.run.startedAt,utc() - 1),targetNm:target,loadSteps:1}); }
        if (!standstill.pending && now() >= nextPoll) {
          if (loadAt == null) {
            const brake = await env.channels['brake-supply'].read(); if (brake.snapshot.status & 128) throw new Error('空载阶段制动输出意外开启');
          } else {
            const command = controller.update(target,actual), channel = env.channels['brake-supply'];
            await channel.setValues({voltage:24,current:command});
            if (generation !== this.generation) break;
            if (target && !(channel.snapshot.status & 128)) {
              if (this.run.brakeEnabled) throw new Error('制动输出意外关闭');
              await channel.output(true,{voltage:24,current:command}); this.run.brakeEnabled = true;
            }
            if (!target && channel.snapshot.status & 128) throw new Error('零负载阶段制动输出意外开启');
            this.run.brakeCurrentA = command;
            env.observe('torque_feedback',{id:this.run.id,targetNm:target,measuredNm:actual,currentA:command});
            if (holding == null && (!target || actual >= target - .1)) holding = now();
            if (holding != null && now() - holding >= this.run.torqueConfig.intervalS) {
              if (target >= this.run.torqueConfig.maxNm) { reason = '设定力矩阶梯完成，保留实测结果'; break; }
              target = Math.round(Math.min(this.run.torqueConfig.maxNm,target + this.run.torqueConfig.stepNm) * 1000000) / 1000000;
              holding = null; this.run.targetNm = target; this.run.loadSteps++;
            }
            if (command >= 1.999) currentLimitAt ??= now(); else currentLimitAt = null;
            if (currentLimitAt != null && now() - currentLimitAt >= 2 && actual < target - .1) { reason = '达到制动安全电流 1.999 A 边界，目标力矩未达到；未确认电机极限'; break; }
          }
          nextPoll = now() + .5;
        }
        await sleep(30);
      }
    } catch (error) { reason = error.message; }
    finally {
      if (generation === this.generation) { this.run.reason = reason; this.run.measurementEndedAt ??= utc(); await this.stop(); }
      if (recording && env.recordings.live?.id === recording && env.recordings.live.running) await env.recordings.stop(recording);
    }
  }
  async stop() {
    this.generation++; this.run.phase = 'stopping'; this.run.measurementEndedAt ??= utc();
    const results = await Promise.allSettled([this.env.motor.device.bus && !this.env.motor.device.bus.closed ? this.env.motor.stop() : Promise.resolve(),this.env.channels['brake-supply'].config ? this.env.brake.stop() : Promise.resolve()]);
    const errors = results.filter(result => result.status === 'rejected').map(result => result.reason.message);
    Object.assign(this.run,{running:false,phase:'stopped',stopConfirmed:!errors.length,stopErrors:errors});
    return this.status();
  }
}
