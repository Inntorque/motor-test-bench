import {now,utc,SerialQueue,jsonValue} from './bytes.ts';
import {stats,fit,performance,thermal,finite} from './metrics.ts';
export class Capture {
  declare began: number;
  declare config;
  declare env: import('./bench.ts').Bench;
  declare gap: boolean | undefined;
  declare lastMotor: { stamp: number; iq: number; id: number; position: number | null; speed: number | null; } | undefined;
  declare lastSensor: { torque: number; speed: number; stamp: number; } | undefined;
  declare lastTemp;
  declare peak: { torqueNm: number; speedRpm: number; sourceUnixS: number; t: number; bandwidthVerified: boolean; } | undefined;
  declare phase;
  declare report;
  declare sensorConfig;
  declare speedPeak: { speedRpm: number; torqueNm: number; sourceUnixS: number; } | undefined;
  declare startedAt: number;
  declare target;

  stamps = {}; cursor = 0; windows = []; temperatures = []; preview = []; issues = []; thermalPreview = []; totals: {initialC?: number; lastC?: number; maxC?: number; count?: number} = {}; best = {}; kt: Record<string, {iq: number; torque: number; count: number}> = {}; counts = {}; bucket = []; bucketBegan = 0; rows = []; part = 0; queue = new SerialQueue(); validWindows = 0; steadyWindows = 0;
  constructor(env,report) {
    this.env = env; this.report = report; this.config = report.config; this.began = now(); this.startedAt = utc();
    const frames = env.sensor.batch(0).frames; if (frames.length) this.cursor = frames.at(-1)[0];
    this.write('configuration',null,this.config);
  }
  write(source,stamp,payload,fields = {}) {
    this.counts[source] = (this.counts[source] || 0) + 1;
    const subject = this.report.testSubject, setup = subject.testSetup || {};
    this.rows.push({elapsed_s:now() - this.began,pc_recorded_unix_s:utc(),source_received_unix_s:stamp,phase:this.phase,source,...fields,motor_name:subject.motor?.name || '',driver_board_name:subject.driverBoard?.name || '',test_setup_id:setup.id,test_setup_name:setup.name,gearbox_name:setup.gearboxName,has_gearbox:setup.hasGearbox,reduction_ratio:setup.ratio,measurement_axis:setup.axis,measured_object_mass_kg:setup.massKg,measured_object_volume_L:setup.volumeL,payload_json:JSON.stringify(jsonValue(payload))});
  }
  sensor(stamp,torque,speed,sequence,payload) {
    if (!finite(torque) || !finite(speed)) return;
    const power = torque * speed * Math.PI / 30;
    this.lastSensor = {torque,speed,stamp}; this.bucket.push({kind:'sensor',torque,speed,power,stamp});
    if (!this.speedPeak || Math.abs(speed) > Math.abs(this.speedPeak.speedRpm)) this.speedPeak = {speedRpm:speed,torqueNm:torque,sourceUnixS:stamp};
    if (!this.peak || Math.abs(torque) > Math.abs(this.peak.torqueNm)) this.peak = {torqueNm:torque,speedRpm:speed,sourceUnixS:stamp,t:now() - this.began,bandwidthVerified:this.config.bandwidthVerified};
    this.write('sensor',stamp,payload,{torque_Nm:torque,speed_rpm:speed,power_W:power,sensor_sequence:sequence});
  }
  poll(phase,target = null) {
    const elapsed = now() - this.began, sensor = this.env.sensor, packet = sensor.parser ? sensor.batch(this.cursor) : null;
    if (this.phase !== phase || this.target !== target) { this.flushWindow(elapsed); this.phase = phase; this.target = target; }
    const source = {motor:this.env.motor.status(),sensor:sensor.status(),...Object.fromEntries(Object.entries(this.env.channels).map(([key,channel]) => [key,channel.status()]))};
    for (const [key,channel] of Object.entries(source)) {
      const snapshot = channel.snapshot, stamp = snapshot?.receivedAt;
      if (!channel.connected || !finite(stamp) || utc() - stamp > 1.5 || key === 'sensor' && packet || this.stamps[key] === stamp) continue;
      this.stamps[key] = stamp;
      if (key === 'motor') {
        const f = snapshot.feedback, ntc = snapshot.ntc, ratio = this.config.ratio;
        this.lastMotor = {stamp,iq:f.iq_a,id:f.id_a,position:finite(f.position_deg) ? f.position_deg / ratio : null,speed:finite(f.speed_rpm) ? f.speed_rpm / ratio : null};
        this.bucket.push({kind:'motor',...this.lastMotor});
        this.write('motor',stamp,channel,{iq_A:f.iq_a,id_A:f.id_a,speed_rpm:this.lastMotor.speed,position_deg:this.lastMotor.position,rotor_position_deg:f.position_deg,rotor_speed_rpm:f.speed_rpm});
        const tempStamp = ntc.sourceDeviceTimeUs || f.deviceTimeUs || stamp;
        if (ntc.valid && finite(ntc.temp_c) && ntc.age_s <= 1.5 && this.lastTemp !== tempStamp) {
          this.lastTemp = tempStamp;
          const point = {t:elapsed,temperatureC:ntc.temp_c,phase,iq:f.iq_a,speedRpm:this.lastMotor.speed,torqueNm:this.lastSensor && utc() - this.lastSensor.stamp <= 1.5 ? this.lastSensor.torque : null};
          this.temperatures.push(point); while (this.temperatures.length && this.temperatures[0].t < elapsed - this.config.thermalWindowS * 1.1) this.temperatures.shift();
          this.thermalPreview.push(point); if (this.thermalPreview.length > 1000) this.thermalPreview = this.thermalPreview.filter((_,index) => index % 2 === 0);
          this.totals.initialC ??= ntc.temp_c; this.totals.lastC = ntc.temp_c; this.totals.maxC = Math.max(this.totals.maxC ?? ntc.temp_c,ntc.temp_c); this.totals.count = (this.totals.count || 0) + 1;
          this.write('temperature',stamp,ntc,{temperature_C:ntc.temp_c});
        }
      } else if (key === 'sensor') this.sensor(stamp,snapshot.torque,snapshot.speed,null,channel);
      else this.write(key,stamp,channel);
    }
    if (packet) {
      if (packet.dropped || packet.reset || this.sensorConfig && JSON.stringify(this.sensorConfig) !== JSON.stringify(packet.config)) { this.gap = true; if (!this.issues.includes('sensor_stream_gap')) this.issues.push('sensor_stream_gap'); }
      if (packet.reset) this.cursor = 0;
      this.sensorConfig = packet.config;
      for (const [seq,stamp,torque,speed] of packet.frames) {
        if (seq <= this.cursor) continue;
        if (seq !== this.cursor + 1) { this.gap = true; if (!this.issues.includes('sensor_stream_gap')) this.issues.push('sensor_stream_gap'); }
        this.cursor = seq;
        if (stamp >= this.startedAt) this.sensor(stamp,torque / 10 ** packet.config.torqueDecimals,speed / 10 ** packet.config.speedDecimals,seq,{rawTorque:torque,rawSpeed:speed,config:packet.config});
      }
    }
    if (elapsed - this.bucketBegan >= this.config.windowS) this.flushWindow(elapsed);
    return {source,row:this.lastMotor};
  }
  flushWindow(elapsed) {
    if (!this.bucket.length) { this.bucketBegan = elapsed; return; }
    const sensors = this.bucket.filter(row => row.kind === 'sensor'), motors = this.bucket.filter(row => row.kind === 'motor');
    const t = stats(sensors.map(row => row.torque)), n = stats(sensors.map(row => row.speed)), iq = stats(motors.map(row => row.iq)), id = stats(motors.map(row => row.id));
    const overlap = !!(sensors.length && motors.length && Math.min(sensors.at(-1).stamp,motors.at(-1).stamp) > Math.max(sensors[0].stamp,motors[0].stamp));
    const valid = t.count >= 2 && iq.count >= 2 && overlap && !this.gap && elapsed - this.bucketBegan >= this.config.windowS * .9;
    const steady = valid && t.std <= Math.max(this.config.torqueNoiseNm ?? .05,Math.abs(t.mean) * .05) && n.std <= Math.max(1,Math.abs(n.mean) * .05);
    const point = {t:elapsed,durationS:elapsed - this.bucketBegan,phase:this.phase,target:this.target,torque:t.mean,n:n.mean,iq:iq.mean,id:id.mean,powerW:stats(sensors.map(row => row.power)).mean,idStdA:id.std,valid,steady,sensorCount:sensors.length,motorCount:motors.length};
    this.windows.push(point); this.validWindows += Number(valid); this.steadyWindows += Number(steady);
    if (this.windows.length > 6000) this.windows = this.windows.filter((_,index) => index % 2 === 0);
    if (valid) for (const [key,field] of [['torque','torque'],['power','powerW'],['speed','n']]) {
      if (key !== 'torque' && !steady) continue;
      if (!this.best[key] || Math.abs(point[field]) > Math.abs(this.best[key][field])) this.best[key] = point;
    }
    if (steady && String(this.phase).startsWith('Kt') && finite(point.id) && finite(point.idStdA) && Math.abs(point.n) <= this.config.ktMaxSpeedRpm && Math.abs(point.id) <= this.config.ktMaxIdA && point.idStdA <= this.config.ktMaxIdA) {
      const entry = this.kt[this.phase] ||= {count:0,iq:0,torque:0}; entry.count++;
      for (const key of ['iq','torque']) entry[key] += (point[key] - entry[key]) / entry.count;
    }
    this.preview.push(point); if (this.preview.length > 1000) this.preview = this.preview.filter((_,index) => index % 2 === 0);
    this.write('window',null,point); this.bucket = []; this.bucketBegan = elapsed; this.gap = false;
  }
  status() {
    const metric = performance([...this.windows,...Object.values(this.best)],this.peak,this.config,this.issues), kt = fit(Object.values(this.kt).map(row => [row.iq,row.torque]));
    Object.assign(metric,{outputEquivalentKt:kt,ktState:kt ? 'identified_in_selected_windows' : 'insufficient_quasistatic_current_levels',validWindows:this.validWindows,steadyWindows:this.steadyWindows,rawSpeedPeak:this.speedPeak});
    const temperature = {...thermal(this.temperatures,this.config),...this.totals};
    if (this.totals.count) temperature.changeFromInitialK = this.totals.lastC - this.totals.initialC;
    let candidate = null;
    if (temperature.stable) {
      const point = [...this.windows].reverse().find(point => point.steady && point.phase !== 'cooling');
      if (point) { candidate = {...point,temperatureC:temperature.lastC,criterion:'stable under this recipe; not a universal motor rating',declaredSpeedRpm:this.config.ratedSpeedRpm,declaredTorqueNm:this.config.ratedTorqueNm}; for (const [key,suffix] of [['massKg','PerKg'],['volumeL','PerL']]) { candidate['torque' + suffix] = this.config[key] ? Math.abs(point.torque) / this.config[key] : null; candidate['powerW' + suffix] = this.config[key] ? Math.abs(point.powerW) / this.config[key] : null; } }
    }
    return {counts:{...this.counts},preview:this.preview.slice(-1000),thermalPreview:this.thermalPreview.slice(-1001),performance:metric,thermal:temperature,continuousCandidate:candidate,qualityIssues:[...this.issues]};
  }
  flush() {
    return this.queue.run(async () => {
      if (!this.rows.length) return;
      const rows = this.rows.slice();
      await this.env.store.put(`chunks:${this.report.origin}:${this.report.id}:${String(this.part).padStart(10,'0')}`,{rows});
      this.part++; this.rows.splice(0,rows.length);
    });
  }
  async close() { this.flushWindow(now() - this.began); await this.flush(); }
}
