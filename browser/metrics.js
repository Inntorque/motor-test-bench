export const finite = value => typeof value === 'number' && Number.isFinite(value);
export function stats(values) {
  values = values.filter(finite);
  if (!values.length) return {count:0};
  const mean = values.reduce((sum,value) => sum + value,0) / values.length;
  const min = Math.min(...values), max = Math.max(...values);
  return {count:values.length,mean,min,max,std:Math.sqrt(values.reduce((sum,value) => sum + (value - mean) ** 2,0) / values.length),range:max - min};
}
export function fit(points) {
  if (points.length < 3) return null;
  const x = stats(points.map(point => point[0])), y = stats(points.map(point => point[1]));
  const xx = points.reduce((sum,[value]) => sum + (value - x.mean) ** 2,0);
  if (xx < 1e-12) return null;
  const slope = points.reduce((sum,[a,b]) => sum + (a - x.mean) * (b - y.mean),0) / xx, intercept = y.mean - slope * x.mean;
  const residual = points.reduce((sum,[a,b]) => sum + (b - slope * a - intercept) ** 2,0), total = points.reduce((sum,[,b]) => sum + (b - y.mean) ** 2,0);
  return {slope,intercept,rSquared:total ? 1 - residual / total : null,count:points.length,minIqA:x.min,maxIqA:x.max};
}
export function performance(windows,peak,config,issues) {
  const valid = windows.filter(window => window.valid), steady = valid.filter(window => window.steady);
  const largest = (points,field) => points.reduce((best,point) => !best || Math.abs(point[field]) > Math.abs(best[field]) ? point : best,null);
  const power = largest(steady,'powerW'), torque = largest(valid,'torque'), speed = largest(steady,'n'), densities = {};
  for (const [key,suffix] of [['massKg','PerKg'],['volumeL','PerL']]) {
    densities['peakTorque' + suffix] = config[key] && torque ? Math.abs(torque.torque) / config[key] : null;
    densities['shortTimePowerW' + suffix] = config[key] && power ? Math.abs(power.powerW) / config[key] : null;
  }
  return {axis:config.axis || 'output',highestObservedRpm:speed ? Math.abs(speed.n) : null,highestSpeedPoint:speed,rawTorquePeak:peak,windowTorquePeak:torque,maxObservedPowerPoint:power,outputEquivalentKt:null,ktState:'insufficient_quasistatic_current_levels',ratedSpeedRpm:config.ratedSpeedRpm,ratedValueSource:'operator_nameplate',continuousRating:'not_certified_by_short_time_sweep',densities,validWindows:valid.length,steadyWindows:steady.length,issues:[...issues],currentConvention:'MBP phase-peak-equivalent Iq; not supply DC current',torqueConvention:'sensor shaft torque; no assumed gearbox efficiency'};
}
export function positioning(visits,references = {},instrument = {}) {
  const aligned = instrument.aligned === true && !!instrument.name;
  const result = {feedback:[],external:[],externalReferenceReady:aligned,referenceCount:Object.keys(references).length,visitCount:visits.length,instrument};
  for (const direction of ['forward','reverse']) {
    for (const target of [...new Set(visits.filter(visit => visit.direction === direction).map(visit => visit.nominalDeg))].sort((a,b) => a - b)) {
      const group = visits.filter(visit => visit.direction === direction && visit.nominalDeg === target);
      result.feedback.push({targetDeg:target,direction,...stats(group.filter(visit => finite(visit.meanDeg)).map(visit => visit.meanDeg - visit.targetDeg))});
      result.external.push({targetDeg:target,direction,...stats(aligned ? group.filter(visit => Object.hasOwn(references,String(visit.visitId))).map(visit => references[visit.visitId] - target - (instrument.zeroDeg || 0)) : [])});
    }
  }
  for (const key of ['feedback','external']) {
    const valid = result[key].filter(row => row.count), repeated = valid.filter(row => row.count >= 2);
    result[key + 'MaxAbsMeanErrorDeg'] = valid.length ? Math.max(...valid.map(row => Math.abs(row.mean))) : null;
    result[key + 'WorstRepeatRangeDeg'] = repeated.length ? Math.max(...repeated.map(row => row.range)) : null;
    const pairs = valid.flatMap(a => valid.filter(b => a.targetDeg === b.targetDeg && a.direction !== b.direction).map(b => Math.abs(a.mean - b.mean)));
    result[key + 'ReversalDifferenceDeg'] = pairs.length ? Math.max(...pairs) : null;
  }
  result.externalCoverageComplete = aligned && Object.keys(references).length === visits.length && !!visits.length;
  result.claim = result.externalCoverageComplete ? 'external_direct_measurement' : 'feedback_only_or_partial_reference'; return result;
}
export function thermal(points,config) {
  const valid = points.filter(point => finite(point.temperatureC));
  if (!valid.length) return {count:0,stable:false,measuringPoint:'existing motor NTC'};
  const latest = valid.at(-1), window = config.thermalWindowS || 600, tail = valid.filter(point => point.t >= latest.t - window && point.phase !== 'cooling');
  const span = tail.length ? tail.at(-1).t - tail[0].t : 0;
  let slope = null;
  if (span >= window * .95 && tail.length >= 3) {
    const t = stats(tail.map(point => point.t)), c = stats(tail.map(point => point.temperatureC));
    const denominator = tail.reduce((sum,point) => sum + (point.t - t.mean) ** 2,0);
    if (denominator) slope = tail.reduce((sum,point) => sum + (point.t - t.mean) * (point.temperatureC - c.mean),0) / denominator * 60;
  }
  const operating = [['iq',.1],['speedRpm',1],['torqueNm',.05]].every(([field,floor]) => {
    const values = stats(tail.map(point => point[field]));
    return values.count >= 2 && values.count === tail.length && values.range <= Math.max(floor,Math.abs(values.mean) * .1);
  });
  return {count:valid.length,initialC:valid[0].temperatureC,lastC:latest.temperatureC,maxC:Math.max(...valid.map(point => point.temperatureC)),riseK:finite(config.ambientC) ? latest.temperatureC - config.ambientC : null,changeFromInitialK:latest.temperatureC - valid[0].temperatureC,slopeCPerMin:slope,stable:slope != null && Math.abs(slope) <= (config.thermalSlopeCPerMin ?? .1) && operating,operatingPointStable:operating,windowS:window,measuringPoint:'existing motor NTC; not assumed winding temperature'};
}
