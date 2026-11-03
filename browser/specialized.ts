import {finite as number,now,sleep,utc} from './bytes.ts';
import {uid} from './storage.ts';
import {Capture} from './capture.ts';
import {stats,positioning} from './metrics.ts';
import {O} from './mbp-codec.ts';
export function recipe(data,subject) {
  const setup = subject.testSetup;
  if (!setup) throw new Error('请先选择裸机或带减速箱测试配置');
  if (data.testSetupId != null && data.testSetupId !== setup.id || data.testSetupRevision != null && data.testSetupRevision !== setup.revision) throw new Error('测试配置已变化，请刷新后重新开始');
  const category = data.suite;
  if (!['performance','position','thermal'].includes(category)) throw new Error('未知专项项目');
  const result = {...data,suite:category,name:String(data.name || {performance:'性能测试',position:'定位精度',thermal:'温升曲线'}[category]).slice(0,100),ratio:setup.ratio,axis:setup.axis,massKg:setup.massKg,volumeL:setup.volumeL,directShaftConfirmed:setup.axis === 'motor'};
  for (const [key,defaultValue,low,high] of [['durationS',60,.1],['windowS',1,.1,60],['settleS',1,0],['dwellS',2,.2],['coolingS',0,0],['thermalWindowS',600,1],['thermalSlopeCPerMin',.1,0],['ktMaxSpeedRpm',5,0],['ktMaxIdA',.5,0],['target',1,-1e9,1e9]] as const) result[key] = number(data[key] ?? defaultValue,key,low,high ?? Infinity);
  result.controlMode = data.controlMode || 'IQ'; result.test = data.test || (category === 'performance' ? 'kt' : category);
  if (!['IQ','VELOCITY'].includes(result.controlMode)) throw new Error('未知工况控制方式');
  for (const key of ['ratedSpeedRpm','ratedTorqueNm','ambientC']) result[key] = data[key] == null ? null : number(data[key],key);
  if (category === 'position') {
    const points = data.positionsDeg || [0,45,90,135,180,225,270,315];
    if (!Array.isArray(points) || points.length < 2 || points.length > 100 || points.some(value => typeof value !== 'number' || !Number.isFinite(value)) || new Set(points).size !== points.length) throw new Error('定位需 2–100 个不同的有效目标点');
    result.positionsDeg = points.slice().sort((a,b) => a - b); result.cycles = number(data.cycles ?? 5,'重复次数',1,100); result.approachDeg = number(data.approachDeg ?? 2,'接近角度',.001); result.positionReference = data.positionReference || 'relative';
    if (!Number.isInteger(result.cycles) || !['relative','absolute'].includes(result.positionReference)) throw new Error('重复次数或坐标方式无效');
  }
  if (category === 'performance') {
    if (!['kt','point','capture'].includes(result.test)) throw new Error('未知性能配方');
    const levels = data.iqLevelsA || [.5,1,1.5,2];
    if (!Array.isArray(levels) || levels.length < 3 || levels.length > 40 || levels.some(value => typeof value !== 'number' || !Number.isFinite(value))) throw new Error('Kt 需 3–40 个有效电流级');
    result.iqLevelsA = levels;
  }
  return result;
}
const fresh = channel => channel?.connected && utc() - (channel.snapshot?.receivedAt || 0) <= 1.5;
export class Specialized {
  declare env;

  live = {automatic:null,specialized:null}; tasks = {}; historyCache = {automatic:[],specialized:[]}; generation = {automatic:0,specialized:0};
  constructor(env) { this.env = env; }
  active() { return Object.values(this.live).some(report => report?.running); }
  async initialize() {
    for (const origin of ['automatic','specialized']) {
      this.historyCache[origin] = await this.env.store.entries(`reports:${origin}:`);
      for (const report of this.historyCache[origin]) if (report.running) {
        Object.assign(report,{running:false,state:'interrupted',reason:'网页已关闭或重新加载，保留已有数据',finishedAt:utc()});
        await this.env.store.put(`reports:${origin}:${report.id}`,report);
      }
    }
  }
  status(origin,history = true) { return {current:this.live[origin] ? this.public(this.live[origin]) : null,...(history ? {records:this.historyCache[origin].slice(0,200).map(report => this.public(report))} : {}),availability:{performance:true,position:true,thermal:true}}; }
  public(report) {
    const value = {...report};
    for (const key of ['preview','thermalPreview']) if (value[key]) value[key] = value[key].filter((_,index) => index % Math.max(1,Math.ceil(value[key].length / 200)) === 0);
    if (report.positionVisits) { value.positionVisitCount = report.positionVisits.length; value.positionVisits = report.positionVisits.slice(-20); }
    return structuredClone(value);
  }
  async save(report) {
    await this.env.store.put(`reports:${report.origin}:${report.id}`,report);
    const rows = this.historyCache[report.origin], index = rows.findIndex(item => item.id === report.id);
    if (index < 0) rows.unshift(this.public(report)); else rows[index] = this.public(report);
  }
  async start(data,origin) {
    if (this.active()) throw new Error('已有专项任务，请先结束');
    const intent = this.generation[origin], epoch = this.env.epoch;
    const subject = this.env.subjects.selection(), config = recipe(data,subject), active = origin === 'automatic', env = this.env;
    if (active && (!data.motionConfirmed || env.motor.run.running || env.coupled.run.running || env.brake.run.running)) throw new Error('请确认机械运动条件，并先停止已有输出任务');
    const motor = env.motor.status(), sensor = env.sensor.status();
    if (!fresh(motor)) throw new Error('需要新鲜驱动板反馈');
    if (config.suite === 'performance' && !fresh(sensor)) throw new Error('性能测试需要新鲜转矩和转速');
    if (config.suite === 'thermal' && !motor.snapshot.ntc.valid) throw new Error('温升测试需要有效 NTC');
    const mode = config.suite === 'position' ? 4 : config.test === 'kt' || config.controlMode === 'IQ' ? 1 : 3;
    const position = motor.snapshot.feedback.position_deg;
    if (active) {
      env.motor.healthy(motor.snapshot,true);
      if (!(motor.snapshot.capabilities.modes & 1 << mode)) throw new Error('驱动板未声明所需控制模式');
      const currents = config.test === 'kt' ? config.iqLevelsA : mode === 1 ? [config.target] : [];
      if (!motor.snapshot.limitA || currents.some(value => Math.abs(value) > motor.snapshot.limitA)) throw new Error('目标电流超过设备现有限值');
      const speedLimit = Number(motor.snapshot.limits[O.rotor_speed_abs_max]);
      if (mode === 3 && (!Number.isFinite(speedLimit) || Math.abs(config.target * config.ratio * 1000) > speedLimit)) throw new Error('速度目标超出设备声明范围');
      if (mode === 4 && (!Number.isFinite(position) || motor.snapshot.feedback.position_stale)) throw new Error('定位需要新鲜多圈位置反馈');
    }
    const report = {id:uid(),origin,specializedSchema:2,category:config.suite,name:config.name,config,testSubject:subject,startedAt:utc(),running:true,state:active ? 'starting' : 'running',phase:active ? '准备' : '采集',reason:'',elapsedS:0,sourceAtStart:env.source(),deviceAtStart:motor.snapshot,originOutputDeg:Number.isFinite(position) ? position / config.ratio : 0,positionVisits:[],referenceData:{},stopConfirmed:null,valid:false};
    await this.save(report);
    if (intent !== this.generation[origin] || epoch !== this.env.epoch) {
      Object.assign(report,{running:false,state:'stopped',reason:'启动已被停止取消',finishedAt:utc()});
      await this.save(report); throw new Error(report.reason);
    }
    this.live[origin] = report;
    const generation = ++this.generation[origin];
    if (active) env.motor.run = {phase:'automatic',running:true,scriptId:report.id,targetA:null,steps:0,elapsed:0,stopConfirmed:null,acquisitionManaged:true};
    this.tasks[origin] = this.execute(report,mode,generation).finally(() => {delete this.tasks[origin];});
    return {current:this.public(report)};
  }
  async execute(report,mode,generation) {
    const env = this.env, config = report.config, origin = report.origin, active = origin === 'automatic', capture = new Capture(env,report);
    const began = now(); let phase = '准备', target = null, started = false, recording, lastSave = 0, seenOn = false, offAt;
    const check = () => { if (this.generation[origin] !== generation) throw new Error('操作者停止'); };
    const publish = async force => {
      Object.assign(report,{elapsedS:now() - began,phase,...capture.status()});
      if (report.category === 'position') report.positionMetrics = positioning(report.positionVisits,report.referenceData);
      if (force || now() - lastSave >= 2) { await capture.flush(); await this.save(report); lastSave = now(); }
    };
    const sample = async (cooling = false,collect = false,positionStats = null,seen = null) => {
      check(); const motor = env.motor.status();
      if (!fresh(motor)) throw new Error('驱动反馈失联，原始记录保留');
      if (active && !cooling) {
        env.motor.healthy(motor.snapshot);
        if (!motor.snapshot.motor.output_on || motor.snapshot.motor.mode !== mode) throw new Error('设备意外停止或控制模式改变');
      }
      if (config.suite === 'performance' && !fresh(env.sensor.status())) throw new Error('转矩／转速测量失联');
      if (config.suite === 'thermal' && !motor.snapshot.ntc.valid) throw new Error('NTC 温度源无效');
      const {row} = capture.poll(phase,target);
      if (collect && row && row.stamp >= seen.started && row.stamp !== seen.stamp) { positionStats.push(row.position); seen.stamp = row.stamp; }
      await publish(false); return motor;
    };
    const hold = async (seconds,cooling = false,collect = false) => {
      const end = now() + seconds, positions = [], seen = {started:utc(),stamp:null};
      while (now() < end) {
        await sample(cooling,collect,positions,seen);
        if (config.stopWhenStable && phase === 'heating' && report.thermal?.stable) break;
        await sleep(50);
      }
      return stats(positions);
    };
    const setTarget = async value => {
      check(); target = value;
      const oid = mode === 4 ? O.position_target : mode === 1 ? O.iq_target : O.velocity_target;
      const raw = mode === 4 ? BigInt(Math.round(value * config.ratio / 360 * 1000000)) : Math.round(value * (mode === 3 ? config.ratio : 1) * 1000);
      const response = await env.device.setTarget(oid,raw); capture.write('command',utc(),{object:oid,value:raw,response});
      if (mode === 1) env.motor.run.targetA = value;
    };
    try {
      if (!env.recordings.live?.running) { const record = await env.recordings.start({name:config.name},report.testSubject); recording = record.id; }
      else if (JSON.stringify(env.recordings.live.testSubject) !== JSON.stringify(report.testSubject)) throw new Error('现有完整记录测试配置不同');
      report.recordingId = recording || env.recordings.live.id;
      if (active) {
        check();
        const initial = mode === 1 ? 0 : mode === 4 ? report.originOutputDeg : config.target;
        const objects = {[O.run_mode]:mode,[O.direction]:1,[O.iq_target]:0,[O.phase_run_limit]:0};
        if (mode === 4) objects[O.position_target] = BigInt(Math.round(initial * config.ratio / 360 * 1000000));
        if (mode === 3) objects[O.velocity_target] = Math.round(initial * config.ratio * 1000);
        const prepared = await env.device.prepare(objects); check(); started = true; await env.device.start(prepared);
        capture.write('start_ack',utc(),{preparedId:prepared,mode}); report.state = 'running';
        if (config.suite === 'position') {
          const offset = config.positionReference === 'relative' ? report.originOutputDeg : 0;
          for (let cycle = 0; cycle < config.cycles; cycle++) for (const direction of ['forward','reverse']) {
            const points = direction === 'forward' ? config.positionsDeg : config.positionsDeg.slice().reverse();
            phase = '接近目标'; await setTarget(offset + points[0] + (direction === 'forward' ? -config.approachDeg : config.approachDeg)); await hold(config.settleS);
            for (const nominal of points) {
              phase = '定位稳定'; await setTarget(offset + nominal); await hold(config.settleS);
              phase = '定位采样'; const values = await hold(config.dwellS,false,true);
              if (values.count < 2) throw new Error('定位窗口缺少独立的新鲜反馈');
              const visit = {visitId:report.positionVisits.length + 1,cycle:cycle + 1,direction,nominalDeg:nominal,targetDeg:target,meanDeg:values.mean,stdDeg:values.std,count:values.count};
              report.positionVisits.push(visit); capture.write('position_visit',utc(),visit); await publish(true);
            }
          }
        } else if (config.suite === 'performance' && config.test === 'kt') {
          for (let index = 0; index < config.iqLevelsA.length; index++) { phase = '电流稳定'; await setTarget(config.iqLevelsA[index]); await hold(config.settleS); phase = `Kt ${index + 1}`; await hold(config.dwellS); }
        } else { phase = config.suite === 'thermal' ? 'heating' : '指定工况'; await setTarget(config.target); await hold(config.durationS); }
        if (config.suite === 'thermal') { const result = capture.status(); report.heatingResult = result.thermal; report.verifiedOperatingPoint = result.continuousCandidate; }
        await env.motor.stop(); report.stopConfirmed = true;
        if (config.coolingS) { phase = 'cooling'; target = null; await hold(config.coolingS,true); }
        report.state = 'completed'; report.reason = '专项配方完成';
      } else {
        while (now() - began < config.durationS) {
          check(); const motor = env.motor.status();
          if (motor.snapshot?.motor.output_on) seenOn = true;
          if (seenOn && motor.snapshot?.motor.output_on === false && offAt == null) { offAt = now(); const result = capture.status(); report.heatingResult = result.thermal; report.verifiedOperatingPoint = result.continuousCandidate; }
          phase = offAt != null ? 'cooling' : config.suite === 'performance' ? 'T-N 扫描' : 'heating';
          await sample(offAt != null);
          if (offAt != null && now() - offAt >= config.coolingS || config.stopWhenStable && report.thermal?.stable) break;
          await sleep(100);
        }
        report.state = 'completed'; report.reason = '工况采集完成';
      }
    } catch (error) { report.state = this.generation[origin] !== generation ? 'stopped' : 'interrupted'; report.reason = error.message; }
    finally {
      if (started && report.stopConfirmed !== true) {
        try { await env.motor.stop(); report.stopConfirmed = true; }
        catch (error) { report.stopConfirmed = false; report.state = 'interrupted'; report.reason += '；停机未确认：' + error.message; }
      }
      try { await capture.close(); } catch (error) { report.state = 'interrupted'; report.reason += '；记录关闭失败：' + error.message; }
      if (recording && env.recordings.live?.id === recording && env.recordings.live.running) {
        try { await env.recordings.stop(recording); } catch (error) { report.state = 'interrupted'; report.reason += '；全参数记录异常：' + error.message; }
      }
      Object.assign(report,{running:false,finishedAt:utc(),valid:report.state === 'completed'});
      if (active) Object.assign(env.motor.run,{running:false,phase:'stopped',reason:report.reason});
      phase = '结束'; await publish(true);
    }
  }
  async stop(origin,id) {
    const report = this.live[origin];
    if (!report || id && id !== report.id) throw new Error('专项任务已变化');
    this.generation[origin]++;
    if (origin === 'automatic' && this.env.device.bus && !this.env.device.bus.closed) await this.env.motor.stop();
    await this.tasks[origin]; return {current:this.public(report)};
  }
  async file(origin,id,name) {
    const report = await this.env.store.get(`reports:${origin}:${id}`);
    if (!report) throw new Error('专项记录不存在');
    if (name === 'report.json') return {body:JSON.stringify(report,null,2),type:'application/json'};
    const chunks = await this.env.store.entries(`chunks:${origin}:${id}:`), rows = chunks.flatMap(chunk => chunk.rows);
    if (name === 'timeline.jsonl') return {body:rows.map(row => JSON.stringify(row)).join('\n') + '\n',type:'application/x-ndjson'};
    if (name === 'data.csv') {
      const fields = [...new Set<string>(rows.flatMap(row => Object.keys(row)))], cell = value => value == null ? '' : '"' + String(value).replaceAll('"','""') + '"';
      return {body:'\ufeff' + fields.join(',') + '\r\n' + rows.map(row => fields.map(field => cell(row[field])).join(',')).join('\r\n'),type:'text/csv'};
    }
    throw new Error('专项文件不存在');
  }
}
