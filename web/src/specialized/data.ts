export function referencesCsv(text) {
  const rows=text.replace(/^\uFEFF/,'').trim().split(/\r?\n/).map(line=>line.split(',').map(value=>value.trim().replace(/^"(.*)"$/,'$1')));
  const visit=rows[0].indexOf('visit_id'), reference=rows[0].indexOf('reference_deg');
  if(visit<0||reference<0)throw Error('CSV 必须包含 visit_id 和 reference_deg 列');
  const seen=new Set();
  return rows.slice(1).filter(row=>row.some(Boolean)).map(row=>{
    const visitId=Number(row[visit]),referenceDeg=Number(row[reference]);
    if(!Number.isInteger(visitId)||visitId<1||!row[reference]||!Number.isFinite(referenceDeg)||seen.has(visitId))throw Error('参考角度缺失、无效或访问编号重复');
    seen.add(visitId);return {visitId,referenceDeg};
  });
}
export function saveFile(name,body,type='text/csv;charset=utf-8') {
  const url=URL.createObjectURL(new Blob([body],{type}));const anchor=document.createElement('a');anchor.href=url;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

const KEY='motor-bench:specialized-reference-v2:';
export function referenceAnalysis(report,rows,instrument) {
  if(!instrument?.aligned||!instrument.name?.trim()||!Number.isFinite(instrument.zeroDeg))throw Error('外部参考仪器、零位和坐标对齐确认缺失');
  const visits=report.positionVisits||[],ids=new Set(visits.map(v=>v.visitId));
  if(!visits.length||rows.some(row=>!ids.has(row.visitId)))throw Error('参考访问编号不属于本次完整定位记录');
  const references: Record<string, number>=Object.fromEntries(rows.map(row=>[row.visitId,row.referenceDeg]));
  const summary=values=>{
    if(!values.length)return {count:0};const mean=values.reduce((a,b)=>a+b,0)/values.length;
    return {count:values.length,mean,min:Math.min(...values),max:Math.max(...values),std:Math.sqrt(values.reduce((s,v)=>s+(v-mean)**2,0)/values.length),range:Math.max(...values)-Math.min(...values)};
  };
  const external=[];
  for(const direction of ['forward','reverse'])for(const target of [...new Set<number>(visits.filter(v=>v.direction===direction).map(v=>v.nominalDeg))].sort((a,b)=>a-b)) {
    external.push({targetDeg:target,direction,...summary(visits.filter(v=>v.direction===direction&&v.nominalDeg===target&&references[v.visitId]!==undefined).map(v=>references[v.visitId]-target-instrument.zeroDeg))});
  }
  const valid=external.filter(row=>row.count),repeated=valid.filter(row=>row.count>=2),differences=[];
  for(const a of valid)for(const b of valid)if(a.targetDeg===b.targetDeg&&a.direction!==b.direction)differences.push(Math.abs(a.mean-b.mean));
  const metrics={...report.positionMetrics,external,externalReferenceReady:true,referenceCount:rows.length,instrument,
    externalCoverageComplete:rows.length===visits.length,claim:rows.length===visits.length?'external_direct_measurement':'feedback_only_or_partial_reference',externalMaxAbsMeanErrorDeg:valid.length?Math.max(...valid.map(row=>Math.abs(row.mean))):null,
    externalWorstRepeatRangeDeg:repeated.length?Math.max(...repeated.map(row=>row.range)):null,externalReversalDifferenceDeg:differences.length?Math.max(...differences):null};
  return {recordId:report.id,referenceData:references,referenceInstrument:instrument,positionMetrics:metrics,
    analyzedAt:new Date().toISOString(),analysisStorage:'separate browser analysis; raw drive record unchanged'};
}
export function loadAnalysis(report,storage=localStorage) {
  const data=storage.getItem(KEY+report.id);return data?{...report,...JSON.parse(data)}:report;
}
export function saveAnalysis(report,analysis,storage=localStorage) {
  storage.setItem(KEY+report.id,JSON.stringify(analysis));
}
