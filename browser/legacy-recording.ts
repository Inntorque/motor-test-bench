import {SerialQueue,utc} from './bytes.ts';
import {uid} from './storage.ts';
const cell = value => value == null ? '' : '"' + String(value).replaceAll('"','""') + '"';
export class LegacyRecording {
  declare closing;
  declare error;
  declare live: { id: string; running: boolean; mode: number; seconds: number; startedAt: number; initial: unknown; outputSeen?: boolean; } | undefined;
  declare status;
  declare store;
  declare timer: number | undefined;

  buffer = []; part = 0; queue = new SerialQueue();
  constructor(store,status) {this.store = store;this.status = status;}
  async initialize() {
    for (const report of await this.history()) if (report.running) {
      Object.assign(report,{running:false,reason:'网页重新加载；已有记录保留',finishedAt:utc()});
      await this.store.put('reports:legacy:' + report.id,report);
    }
  }
  async history() {return (await this.store.entries('reports:legacy:')).sort((a,b) => b.startedAt - a.startedAt).slice(0,200);}
  async start(mode,seconds) {
    if (this.closing) await this.closing;
    if (this.live?.running) {this.event('start_again',{mode,seconds});return;}
    this.live = {id:uid(),running:true,mode,seconds,startedAt:utc(),initial:this.status()};
    await this.store.put('reports:legacy:' + this.live.id,this.live);
    this.part = 0;this.buffer = [];
    let ticks = 0;
    this.timer = setInterval(() => {
      const status = this.status(), {motor,feedback,encoder,telem,ntc} = status;
      this.event('sample',{motor,feedback,encoder,telem,ntc});
      if (++ticks % 10 === 0) void this.flush().catch(error => {this.error = error.message;});
    },50);
  }
  event(kind,data) {if (this.live?.running) this.buffer.push({t:utc(),kind,data});}
  flush() {
    return this.queue.run(async () => {
      if (!this.live || !this.buffer.length) return;
      const rows = this.buffer.slice();
      await this.store.put(`chunks:legacy:${this.live.id}:${String(this.part).padStart(8,'0')}`,{rows});
      this.buffer.splice(0,rows.length);this.part++;
    });
  }
  finish(reason) {
    if (this.closing) return this.closing;
    clearInterval(this.timer);
    if (!this.live?.running) return Promise.resolve();
    this.closing = (async () => {
      this.event('end',{reason});await this.flush();
      Object.assign(this.live,{running:false,finishedAt:utc(),reason,error:this.error || null});
      await this.store.put('reports:legacy:' + this.live.id,this.live);
    })().finally(() => {this.closing = null;});
    return this.closing;
  }
  async file(id,format) {
    if (this.live?.id === id) await this.flush();
    const report = await this.store.get('reports:legacy:' + id);
    if (!report) throw new Error('记录不存在');
    const rows = (await this.store.entries(`chunks:legacy:${id}:`)).flatMap(chunk => chunk.rows);
    const trace = await this.store.get('trace:legacy:' + id);
    if (format === 'timeline.jsonl') return {file:true,type:'application/x-ndjson',body:[{kind:'metadata',data:report},...rows,...(trace ? [{kind:'maximum_trace',data:trace}] : [])].map(row => JSON.stringify(row)).join('\n') + '\n'};
    if (format === 'maximum_trace.csv') {
      if (!trace?.complete) throw new Error(trace?.error || '冻结轨迹仍在读取或本次运行不支持该数据');
      const columns = 'mcu_time_us,adc_raw_a,adc_raw_b,adc_raw_c,encoder_raw,id_a,iq_a,id_ref_a,iq_ref_a,vd_alloc_v,vq_alloc_v,adc_sequence,launch_cnt,sample_margin_ticks,pwm_generation,flags,magnetic_word';
      return {file:true,type:'text/csv',body:columns + '\n' + trace.rows.map(row => row.join(',')).join('\n')};
    }
    const columns = ['t_s','host_time','state','speed_pll_rpm','id_a','iq_a','position_rotor_deg','enc_angle_deg','ia_ma','ib_ma','ic_ma','mode','event','payload_json'];
    const values = rows.map(row => {
      const s = row.kind === 'sample' ? row.data : {},f = s.feedback || {},e = s.encoder || {},t = s.telem || {};
      return [row.t - report.startedAt,row.t,s.motor?.state,f.speed_rpm,f.id_a,f.iq_a,f.position_deg,e.angle_deg,t.ia_mA,t.ib_mA,t.ic_mA,s.motor?.mode,row.kind,JSON.stringify(row.data)];
    });
    return {file:true,type:'text/csv',body:'\ufeff' + columns.join(',') + '\r\n' + values.map(row => row.map(cell).join(',')).join('\r\n')};
  }
}
