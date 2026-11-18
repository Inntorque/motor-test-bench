import {mkdir, open, writeFile, rename} from 'node:fs/promises';
import {resolve} from 'node:path';

export class DataArchive {
  constructor(directory,renderChart) { this.directory = directory; this.renderChart=renderChart; this.sessions = new Map(); this.queue = Promise.resolve(); }
  write(packet) {
    const next = this.queue.then(() => this.save(packet));
    this.queue = next.catch(() => {});
    return next;
  }
  async save({id, report, part, csv, jsonl, measurements, characteristics}) {
    if (!/^[a-f0-9]{32}$/.test(id)) throw new Error('记录标识无效');
    let session = this.sessions.get(id);
    if (!session) {
      if (!report || report.id !== id || !Number.isFinite(report.startedAt)) throw new Error('记录信息无效');
      const date = new Date(report.startedAt * 1000);
      const stamp = [date.getFullYear(),date.getMonth()+1,date.getDate(),date.getHours(),date.getMinutes(),date.getSeconds()].map(n=>String(n).padStart(2,'0')).join('-');
      const directory = resolve(this.directory, `${stamp}_${id}`);
      await mkdir(directory, {recursive:true});
      session = {directory, part:0, savedCharts:new Set()}; this.sessions.set(id,session);
    }
    if (report) {
      for(const summary of report.characteristicRuns || []){
        if(session.savedCharts.has(summary.id))continue;
        if(!/^[a-f0-9]{32}$/.test(summary.id))throw new Error('曲线标识无效');
        const characteristicPoints=report.characteristicPoints.filter(point=>point.runId===summary.id);
        const svg=await this.renderChart({...summary,mode:'torque_sweep',running:false,characteristicSchema:2,characteristicPoints});
        const chart=resolve(session.directory,`特性图-${summary.id}.svg`);
        await writeFile(chart+'.tmp',svg);
        await rename(chart+'.tmp',chart);
        session.savedCharts.add(summary.id);
      }
      const file = resolve(session.directory, 'report.json');
      await writeFile(file+'.tmp', JSON.stringify(report,null,2));
      await rename(file+'.tmp', file);
    } else {
      if (part < session.part) return;
      if (part !== session.part || typeof csv !== 'string' || typeof jsonl !== 'string' || typeof measurements !== 'string' || typeof characteristics !== 'string') throw new Error('记录数据顺序无效');
      const handles = [];
      try {
        for (const [name, body] of [['timeline.csv',csv],['timeline.jsonl',jsonl],['measurements.csv',measurements],['characteristics.csv',characteristics]]) {
          const handle = await open(resolve(session.directory,name),'a+');
          const {size} = await handle.stat(); handles.push({handle,size});
          await handle.writeFile(body); await handle.sync();
        }
        session.part++;
      } catch (error) {
        for (const {handle,size} of handles) await handle.truncate(size);
        throw error;
      } finally {
        await Promise.all(handles.map(({handle})=>handle.close()));
      }
    }
    return {directory:session.directory};
  }
}
