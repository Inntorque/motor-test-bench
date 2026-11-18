import {characteristicFields,isOutputPoint} from './characteristic-points.ts';
import {sensorColumns,motorColumns,supplyColumns,brakeColumns,alignedColumns,measurementFields,alignmentLimits} from './recording-data.ts';
import type {RecordingContext} from './recording-data.ts';
import profile from '../profiles/bench.json' with {type:'json'};
import {jsonValue,utc,now,finite,SerialQueue} from './bytes.ts';
export const uid = () => crypto.randomUUID().replaceAll('-','');
export class BrowserStore {
  declare db;
  declare prefix: string;

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
  declare busy;
  declare data;
  declare store;

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
    this.editable(); const result: {motorId?: string; driverBoardId?: string; testSetupId?: string} = {};
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
const extraFields = ['timestamp_basis','sensor_stream_id','sensor_batch_id','sensor_frame_index','sensor_batch_size','run_id','test_kind','shaft_power_signed_W',...measurementFields];
const allFields = [...new Set([...fields,...extraFields])];
const alignedFields = [...new Set(['elapsed_s','recording_elapsed_s','pc_recorded_unix_s','timestamp_basis','run_id','test_kind','phase',...measurementFields,...fields.slice(fields.indexOf('motor_name'))])];
const csvCell = value => value == null ? '' : '"' + String(value).replaceAll('"','""') + '"';
export class Recordings {
  declare phase;
  declare automatic: boolean;
  declare source: () => Record<string, {connected?: boolean; snapshot?: ReturnType<import('./motor.ts').MotorDevice['snapshot']>}>;
  declare stamps: {} | undefined;
  declare store;
  declare subject;
  declare timer: number | undefined;

  context: () => RecordingContext = () => ({});
  drain = () => {};
  startedMonotonic = 0; hadMeasurements = false;
  live = null; buffer = []; queue = new SerialQueue(); part = 0;
  constructor(store,source,subject,automatic=false) { this.store = store; this.source = source; this.subject = subject; this.automatic = automatic; }
  async initialize() {
    for (const report of await this.history()) if (report.running) {
      report.running = false; report.state = 'interrupted'; report.reason = '网页已关闭或重新加载，记录中断；已有数据保留'; report.finishedAt = utc();
      await this.store.put(`reports:recordings:${report.id}`,report);
    }
  }
  status() { return this.live ? structuredClone(this.live) : null; }
  async history() { return (await this.store.entries('reports:recordings:')).filter(value => value?.recordSchema === 2 && value.id).sort((a,b) => b.startedAt - a.startedAt).slice(0,200); }
  async start(data: {name?: string; range?: number} = {}, subject = this.subject()) {
    if (this.live?.running) throw new Error('已有首页记录，请先停止记录');
    const source = this.source();
    if (!this.automatic && !Object.values(source).some(channel => channel?.connected && utc() - (channel.snapshot?.receivedAt || 0) < 3)) throw new Error('请先连接并读取至少一个真实设备');
    const id = uid(); this.startedMonotonic = now(); this.hadMeasurements = false; this.buffer = []; this.part = 0; this.stamps = {};
    this.live = {id,recordSchema:2,hostApi:1,name:String(data.name || '工作台试验').slice(0,100),testSubject:structuredClone(subject),ratio:subject.testSetup?.ratio ?? null,range:data.range || (this.automatic ? null : 100),startedAt:utc(),running:true,state:'recording',reason:'',elapsedS:0,sourceAtStart:source,counts:{},files:{csv:`/api/recordings/files/${id}/timeline.csv`,jsonl:`/api/recordings/files/${id}/timeline.jsonl`,report:`/api/recordings/files/${id}/report.json`,characteristics:`/api/recordings/files/${id}/characteristics.csv`}};
    Object.assign(this.live,{dataFormat:3,timing:{sensor:'host receive time; frames received together may share timestamps; no invented 1 kHz timestamps',elapsed_s:'source receive UTC minus session start UTC, or monotonic recording time for events',recording_elapsed_s:'monotonic host recording time',source_recording_elapsed_s:'source receive UTC minus session start UTC; blank when unavailable'},alignment:{mode:'latest received snapshot at each 50 ms host poll; no interpolation or future samples',maxAgeSeconds:alignmentLimits},power:{shaft_power_W:'abs(T*n)/9550*1000, matches UI',shaft_power_signed_W:'T*n/9550*1000',supply_actual_power_W:'measured V * measured A'}});
    await this.store.put(`reports:recordings:${id}`,this.live);
    this.timer = setInterval(() => { void this.flush().catch(error => { this.live.error = error.message; }); },1000);
    this.observe('configuration',{testSubject:subject,recordingId:id}); return this.status();
  }
  observe(kind,payload) {
    if (!this.live?.running) return;
    const report = this.live, subject = this.automatic ? this.subject() : report.testSubject, setup = subject.testSetup || {};
    if (this.automatic) { report.testSubject = subject; report.ratio = setup.ratio ?? null; }
    const stamp = utc(), elapsed = now() - this.startedMonotonic, context = this.context();
    const received = Number.isFinite(payload.receivedAt) ? payload.receivedAt : null;
    const sourceElapsed = received == null ? null : received - report.startedAt;
    this.buffer.push({elapsed_s:sourceElapsed ?? elapsed,recording_elapsed_s:elapsed,source_recording_elapsed_s:sourceElapsed,pc_recorded_unix_s:stamp,timestamp_basis:received == null ? 'host_recorded' : 'host_received',phase:context.phase || this.phase || 'idle',run_id:context.runId ?? null,test_kind:context.kind ?? null,source:kind,source_received_unix_s:received,payload_json:JSON.stringify(jsonValue(payload)),motor_name:subject.motor?.name || '',driver_board_name:subject.driverBoard?.name || '',test_setup_id:setup.id,test_setup_name:setup.name,gearbox_name:setup.gearboxName,has_gearbox:setup.hasGearbox,reduction_ratio:setup.ratio,measurement_axis:setup.axis,measured_object_mass_kg:setup.massKg,measured_object_volume_L:setup.volumeL});
    if (kind === 'sensor_frames') {
      const base = this.buffer.pop(), config = payload.config;
      for (const [index,[sequence,receivedAt,rawTorque,rawSpeed]] of payload.frames.entries()) {
        const torque = rawTorque / 10 ** config.torqueDecimals, speed = rawSpeed / 10 ** config.speedDecimals;
        this.buffer.push({...base,elapsed_s:receivedAt-report.startedAt,source_recording_elapsed_s:receivedAt-report.startedAt,source_received_unix_s:receivedAt,timestamp_basis:'host_receive_batch',sensor_stream_id:payload.streamId ?? null,sensor_batch_id:payload.batchId ?? null,sensor_frame_index:index,sensor_batch_size:payload.frames.length,sensor_sequence:sequence,...sensorColumns(torque,speed),payload_json:JSON.stringify({rawTorque,rawSpeed,config})});
      }
    }
    if (kind === 'characteristic_end') (report.characteristicRuns ??= []).push(structuredClone(payload));
    if (kind === 'characteristic_start') report.characteristicSchema=payload.characteristicSchema;
    if (kind === 'characteristic_point') {
      Object.assign(this.buffer.at(-1),payload);
      (report.characteristicPoints ??= []).push(structuredClone(payload));
      report.characteristicSchema ??= 1;
    }
    if (kind === 'torque_feedback') Object.assign(this.buffer.at(-1),brakeColumns(payload));
    report.counts[kind] = (report.counts[kind] || 0) + (kind === 'sensor_frames' ? payload.frames.length : 1); report.elapsedS = elapsed;
  }
  capture() {
    if (!this.live?.running) return;
    const devices = this.source();
    for (const [key,channel] of Object.entries(devices)) {
      if (!channel?.connected || !channel.snapshot) continue;
      const snapshot = channel.snapshot;
      if (snapshot.receivedAt === this.stamps[key]) continue;
      this.stamps[key] = snapshot.receivedAt; this.observe(key,snapshot);
      const row = this.buffer.at(-1), ratio = this.live.ratio;
      if (key === 'current-motor') Object.assign(row,motorColumns(snapshot,ratio));
      else if (key === 'torque-sensor') Object.assign(row,sensorColumns(snapshot.torque,snapshot.speed));
      else if (key.endsWith('-supply')) Object.assign(row,supplyColumns(snapshot));
    }
    this.hadMeasurements ||= Object.values(devices).some(device => !!device?.snapshot);
    if (this.hadMeasurements) {
      const stamp = utc();
      this.observe('measurement',{receivedAt:stamp});
      Object.assign(this.buffer.at(-1),alignedColumns(devices,stamp,this.live.ratio,this.context()),{timestamp_basis:'host_poll_latest'});
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
      delete this.live.error;
    });
  }
  async stop(id) {
    if (!this.live || id && this.live.id !== id) throw new Error('当前记录不存在或已变化');
    this.drain(); clearInterval(this.timer); this.capture(); this.live.running = false; this.live.state = 'complete'; this.live.finishedAt = utc();
    try { await this.flush(); }
    catch (error) {
      if (this.automatic) {
        this.live.running = true; this.live.state = 'recording'; delete this.live.finishedAt;
        this.timer = setInterval(() => { void this.flush().catch(error => { this.live.error = error.message; }); },1000);
      }
      throw error;
    }
    return this.status();
  }
  async file(id,name) {
    if (this.live?.id === id) await this.flush();
    const report = await this.store.get(`reports:recordings:${id}`);
    if (!report) throw new Error('记录不存在');
    if (name === 'report.json') return {body:JSON.stringify(report,null,2),type:'application/json'};
    const chunks = await this.store.entries(`chunks:recordings:${id}:`), rows = chunks.flatMap(chunk => chunk.rows);
    if (name === 'characteristics.csv') return {body:'\ufeff'+characteristicFields.join(',')+'\r\n'+rows.filter(row=>row.source==='characteristic_point' && isOutputPoint(row)).map(row=>characteristicFields.map(field=>csvCell(row[field])).join(',')+'\r\n').join(''),type:'text/csv'};
    if (name === 'timeline.jsonl') return {body:rows.map(row => JSON.stringify(row)).join('\n') + '\n',type:'application/x-ndjson'};
    if (name === 'timeline.csv') return {body:'\ufeff' + allFields.join(',') + '\r\n' + rows.map(row => allFields.map(field => csvCell(row[field])).join(',')).join('\r\n'),type:'text/csv'};
    throw new Error('记录文件不存在');
  }
}

export class DesktopRecordStore {
  declare io: import('./messages.ts').DeviceIo;
  constructor(io: import('./messages.ts').DeviceIo) { this.io = io; }
  put(key: string, value) {
    const [, , id, part] = key.split(':');
    const packet = part === undefined ? {id,report:{...value,files:{csv:'timeline.csv',jsonl:'timeline.jsonl',report:'report.json',measurements:'measurements.csv',characteristics:'characteristics.csv'}}} : {
      id,part:Number(part),
      csv:(Number(part) === 0 ? '\ufeff'+allFields.join(',')+'\r\n' : '')+value.rows.map(row=>allFields.map(field=>csvCell(row[field])).join(',')).join('\r\n')+'\r\n',
      measurements:(Number(part) === 0 ? '\ufeff'+alignedFields.join(',')+'\r\n' : '')+value.rows.filter(row=>row.source==='measurement').map(row=>alignedFields.map(field=>csvCell(row[field])).join(',')+'\r\n').join(''),
      characteristics:(Number(part)===0?'\ufeff'+characteristicFields.join(',')+'\r\n':'')+value.rows.filter(row=>row.source==='characteristic_point' && isOutputPoint(row)).map(row=>characteristicFields.map(field=>csvCell(row[field])).join(',')+'\r\n').join(''),
      jsonl:value.rows.map(row=>JSON.stringify(row)).join('\n')+'\n',
    };
    return this.io.call('archive-write',{archive:packet});
  }
}
