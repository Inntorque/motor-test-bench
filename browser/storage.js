import profile from '../profiles/bench.json' with {type:'json'};
import {jsonValue,utc,finite,SerialQueue} from './bytes.js';
export const uid = () => crypto.randomUUID().replaceAll('-','');
export class BrowserStore {
  constructor(app) { this.prefix = app + ':'; }
  async open() {
    this.db = await new Promise((resolve,reject) => {
      const request = indexedDB.open('inntorque-browser-tests',1);
      request.onupgradeneeded = () => request.result.createObjectStore('data');
      request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
    });
    return this;
  }
  transaction(mode, action) {
    return new Promise((resolve,reject) => {
      const transaction = this.db.transaction('data',mode), store = transaction.objectStore('data');
      let result;
      const request = action(store);
      request.onsuccess = () => { result = request.result; };
      transaction.oncomplete = () => resolve(result);
      transaction.onerror = () => reject(transaction.error); transaction.onabort = () => reject(transaction.error || new Error('浏览器记录写入已取消'));
    });
  }
  get(key) { return this.transaction('readonly',store => store.get(this.prefix + key)); }
  put(key,value) { return this.transaction('readwrite',store => store.put(jsonValue(value),this.prefix + key)); }
  async entries(prefix) {
    const range = IDBKeyRange.bound(this.prefix + prefix,this.prefix + prefix + '\uffff');
    return this.transaction('readonly',store => store.getAll(range));
  }
}
export class TestSubjects {
  constructor(store,busy) { this.store = store; this.busy = busy; }
  async initialize() {
    this.data = await this.store.get('subjects');
    if (!this.data) {
      const motor = {id:uid(),name:profile.motor.label};
      this.data = {schema:2,motors:[motor],driverBoards:[],selected:{motorId:'',driverBoardId:'',testSetupId:''},testSetups:[{id:uid(),motorId:motor.id,name:'原带减速箱配置（待确认）',hasGearbox:true,gearboxName:'',ratio:profile.motor.nameplate.reduction_ratio,axis:'output',massKg:null,volumeL:null,revision:0,ratioConfirmed:false,ratioSource:'旧设备档案；待现场核对'}]};
      await this.store.put('subjects',this.data);
    }
  }
  status() { return {...structuredClone(this.data),configurationLocked:!!this.busy()}; }
  selection() {
    const {selected} = this.data;
    const motor = this.data.motors.find(item => item.id === selected.motorId) || null;
    const driverBoard = this.data.driverBoards.find(item => item.id === selected.driverBoardId) || null;
    const testSetup = this.data.testSetups.find(item => item.id === selected.testSetupId && item.motorId === selected.motorId) || null;
    return structuredClone({motor,driverBoard,testSetup});
  }
  editable() { if (this.busy()) throw new Error('测试或记录进行中，请结束后再切换或修改测试配置'); }
  async create(kind,name) {
    if (!['motor','driverBoard'].includes(kind) || typeof name !== 'string' || !name.trim() || name.trim().length > 80) throw new Error('请选择电机或驱动板，并填写 1–80 字名称');
    const group = this.data[kind === 'motor' ? 'motors' : 'driverBoards'];
    let item = group.find(item => item.name.toLowerCase() === name.trim().toLowerCase());
    if (!item) { item = {id:uid(),name:name.trim()}; group.push(item); await this.store.put('subjects',this.data); }
    return {catalog:this.status(),item};
  }
  async saveSetup(value) {
    this.editable();
    if (typeof value.name !== 'string' || !value.name.trim() || value.name.trim().length > 80 || typeof value.hasGearbox !== 'boolean') throw new Error('请填写配置名称并选择裸机或带减速箱');
    const ratio = finite(value.ratio,'减速比',.000001);
    if (!value.hasGearbox && ratio !== 1) throw new Error('裸机减速比必须为 1');
    if (!this.data.motors.some(item => item.id === value.motorId)) throw new Error('请先选择电机');
    const existing = this.data.testSetups.find(item => item.id === value.id && item.motorId === value.motorId);
    if (value.id && !existing) throw new Error('测试配置不存在或不属于所选电机');
    if (this.data.testSetups.some(item => item.id !== value.id && item.motorId === value.motorId && item.name.toLowerCase() === value.name.trim().toLowerCase())) throw new Error('该电机下已有同名配置');
    const item = {id:value.id || uid(),motorId:value.motorId,name:value.name.trim(),hasGearbox:value.hasGearbox,gearboxName:value.hasGearbox ? String(value.gearboxName || '').slice(0,80) : '',ratio,axis:value.hasGearbox ? 'output' : 'motor',massKg:value.massKg == null ? null : finite(value.massKg,'质量',Number.MIN_VALUE),volumeL:value.volumeL == null ? null : finite(value.volumeL,'体积',Number.MIN_VALUE),revision:(existing?.revision || 0) + 1,ratioConfirmed:true,ratioSource:'操作者保存的测试配置'};
    if (existing) this.data.testSetups[this.data.testSetups.indexOf(existing)] = item;
    else this.data.testSetups.push(item);
    await this.store.put('subjects',this.data); return {catalog:this.status(),item};
  }
  async select(value) {
    this.editable(); const result = {};
    for (const [key,group] of [['motorId','motors'],['driverBoardId','driverBoards']]) {
      const id = value[key] || '';
      if (typeof id !== 'string' || id && !this.data[group].some(item => item.id === id)) throw new Error('选择的电机或驱动板不存在');
      result[key] = id;
    }
    result.testSetupId = value.testSetupId || '';
    if (result.testSetupId && !this.data.testSetups.some(item => item.id === result.testSetupId && item.motorId === result.motorId)) throw new Error('测试配置不属于所选电机');
    this.data.selected = result; await this.store.put('subjects',this.data); return this.status();
  }
}
const fields = ['elapsed_s','recording_elapsed_s','source_recording_elapsed_s','pc_recorded_unix_s','phase','source','source_received_unix_s','sensor_sequence','torque_Nm','sensor_speed_rpm','shaft_power_W','commanded_iq_A','reported_iq_reference_A','actual_iq_A','actual_id_A','rotor_position_deg','output_position_deg','rotor_speed_rpm','output_speed_rpm','motor_ntc_C','motor_output_on','motor_mode','motor_fault','supply_target_voltage_V','supply_target_current_A','supply_actual_voltage_V','supply_actual_current_A','supply_actual_power_W','supply_output_on','supply_max_voltage_V','supply_max_current_A','brake_target_torque_Nm','brake_feedback_torque_Nm','brake_command_current_A','payload_json','motor_name','driver_board_name','test_setup_id','test_setup_name','gearbox_name','has_gearbox','reduction_ratio','measurement_axis','measured_object_mass_kg','measured_object_volume_L'];
const csvCell = value => value == null ? '' : '"' + String(value).replaceAll('"','""') + '"';
export class Recordings {
  live = null; buffer = []; queue = new SerialQueue(); part = 0;
  constructor(store,source,subject) { this.store = store; this.source = source; this.subject = subject; }
  async initialize() {
    for (const report of await this.history()) if (report.running) {
      report.running = false; report.state = 'interrupted'; report.reason = '网页已关闭或重新加载，记录中断；已有数据保留'; report.finishedAt = utc();
      await this.store.put(`reports:recordings:${report.id}`,report);
    }
  }
  status() { return this.live ? structuredClone(this.live) : null; }
  async history() { return (await this.store.entries('reports:recordings:')).filter(value => value?.recordSchema === 2 && value.id).sort((a,b) => b.startedAt - a.startedAt).slice(0,200); }
  async start(data = {}, subject = this.subject()) {
    if (this.live?.running) throw new Error('已有首页记录，请先停止记录');
    const source = this.source();
    if (!Object.values(source).some(channel => channel?.connected && utc() - (channel.snapshot?.receivedAt || 0) < 3)) throw new Error('请先连接并读取至少一个真实设备');
    const id = uid(); this.buffer = []; this.part = 0; this.stamps = {};
    this.live = {id,recordSchema:2,hostApi:1,name:String(data.name || '工作台试验').slice(0,100),testSubject:structuredClone(subject),ratio:subject.testSetup?.ratio ?? null,range:data.range || 100,startedAt:utc(),running:true,state:'recording',reason:'',elapsedS:0,sourceAtStart:source,counts:{},files:{csv:`/api/recordings/files/${id}/timeline.csv`,jsonl:`/api/recordings/files/${id}/timeline.jsonl`,report:`/api/recordings/files/${id}/report.json`}};
    await this.store.put(`reports:recordings:${id}`,this.live);
    this.timer = setInterval(() => { void this.flush().catch(error => { this.live.error = error.message; }); },1000);
    this.observe('configuration',{testSubject:subject,recordingId:id}); return this.status();
  }
  observe(kind,payload) {
    if (!this.live?.running) return;
    const report = this.live, subject = report.testSubject, setup = subject.testSetup || {};
    const stamp = utc(), elapsed = stamp - report.startedAt;
    this.buffer.push({elapsed_s:elapsed,recording_elapsed_s:elapsed,source_recording_elapsed_s:elapsed,pc_recorded_unix_s:stamp,phase:this.phase || 'recording',source:kind,source_received_unix_s:payload.receivedAt ?? null,payload_json:JSON.stringify(jsonValue(payload)),motor_name:subject.motor?.name || '',driver_board_name:subject.driverBoard?.name || '',test_setup_id:setup.id,test_setup_name:setup.name,gearbox_name:setup.gearboxName,has_gearbox:setup.hasGearbox,reduction_ratio:setup.ratio,measurement_axis:setup.axis,measured_object_mass_kg:setup.massKg,measured_object_volume_L:setup.volumeL});
    if (kind === 'sensor_frames') {
      const base = this.buffer.pop(), config = payload.config;
      for (const [sequence,receivedAt,rawTorque,rawSpeed] of payload.frames) {
        const torque = rawTorque / 10 ** config.torqueDecimals, speed = rawSpeed / 10 ** config.speedDecimals;
        this.buffer.push({...base,source_received_unix_s:receivedAt,sensor_sequence:sequence,torque_Nm:torque,sensor_speed_rpm:speed,shaft_power_W:torque * speed * Math.PI / 30,payload_json:JSON.stringify({rawTorque,rawSpeed,config})});
      }
    }
    report.counts[kind] = (report.counts[kind] || 0) + 1; report.elapsedS = elapsed;
  }
  capture() {
    if (!this.live?.running) return;
    for (const [key,channel] of Object.entries(this.source())) {
      if (!channel?.connected || !channel.snapshot) continue;
      const snapshot = channel.snapshot;
      if (snapshot.receivedAt === this.stamps[key]) continue;
      this.stamps[key] = snapshot.receivedAt; this.observe(key,snapshot);
      const row = this.buffer.at(-1), ratio = this.live.ratio;
      if (key === 'current-motor') Object.assign(row,{actual_iq_A:snapshot.feedback?.iq_a,actual_id_A:snapshot.feedback?.id_a,reported_iq_reference_A:snapshot.feedback?.iq_reference_a,rotor_position_deg:snapshot.feedback?.position_deg,output_position_deg:ratio ? snapshot.feedback?.position_deg / ratio : null,rotor_speed_rpm:snapshot.feedback?.speed_rpm,output_speed_rpm:ratio ? snapshot.feedback?.speed_rpm / ratio : null,motor_ntc_C:snapshot.ntc?.temp_c,motor_output_on:snapshot.motor?.output_on,motor_mode:snapshot.motor?.mode,motor_fault:snapshot.motor?.fault});
      else if (key === 'torque-sensor') Object.assign(row,{torque_Nm:snapshot.torque,sensor_speed_rpm:snapshot.speed,shaft_power_W:Number.isFinite(snapshot.torque) && Number.isFinite(snapshot.speed) ? snapshot.torque * snapshot.speed * Math.PI / 30 : null});
      else if (key.endsWith('-supply')) {
        const config = snapshot.config, sv = 10 ** config.voltageDecimals, sa = 10 ** config.currentDecimals;
        Object.assign(row,{supply_target_voltage_V:snapshot.settings?.[1] / sv,supply_target_current_A:snapshot.settings?.[2] / sa,supply_actual_voltage_V:snapshot.measured?.[0] / sv,supply_actual_current_A:snapshot.measured?.[1] / sa,supply_output_on:Boolean(snapshot.status & 128),supply_max_voltage_V:config.maxVoltage,supply_max_current_A:config.maxCurrent});
      }
    }
  }
  flush() {
    return this.queue.run(async () => {
      if (!this.live) return;
      const rows = this.buffer.slice(), report = this.status();
      if (rows.length) {
        await this.store.put(`chunks:recordings:${report.id}:${String(this.part).padStart(10,'0')}`,{rows});
        this.part++; this.buffer.splice(0,rows.length);
      }
      await this.store.put(`reports:recordings:${report.id}`,report);
    });
  }
  async stop(id) {
    if (!this.live || id && this.live.id !== id) throw new Error('当前记录不存在或已变化');
    clearInterval(this.timer); this.capture(); this.live.running = false; this.live.state = 'complete'; this.live.finishedAt = utc();
    await this.flush(); return this.status();
  }
  async file(id,name) {
    if (this.live?.id === id) await this.flush();
    const report = await this.store.get(`reports:recordings:${id}`);
    if (!report) throw new Error('记录不存在');
    if (name === 'report.json') return {body:JSON.stringify(report,null,2),type:'application/json'};
    const chunks = await this.store.entries(`chunks:recordings:${id}:`), rows = chunks.flatMap(chunk => chunk.rows);
    if (name === 'timeline.jsonl') return {body:rows.map(row => JSON.stringify(row)).join('\n') + '\n',type:'application/x-ndjson'};
    if (name === 'timeline.csv') return {body:'\ufeff' + fields.join(',') + '\r\n' + rows.map(row => fields.map(field => csvCell(row[field])).join(',')).join('\r\n'),type:'text/csv'};
    throw new Error('记录文件不存在');
  }
}
