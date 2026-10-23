export const CHARACTERISTIC_COLUMNS=['power','efficiency','current','speed','voltage','inputPower'];

// A confirmed measured stop is a separate observation, never a torque-bin
// average or an extrapolated zero. It also works before delayed frames arrive.
export function measuredStopPoint(run) {
  const s=run?.stallEndpoint;
  if(run?.running || !s || !Number.isFinite(s.torque) || !Number.isFinite(s.speed)
    || !Number.isFinite(s.receivedAt) || !Number.isFinite(run.measurementEndedAt) || s.receivedAt>run.measurementEndedAt
    || Math.abs(s.speed)>10**-Number(s.speedDecimals??1)) return null;
  const power=Math.abs(s.torque*s.speed)/9550*1000;
  return {torque:Math.abs(s.torque),speed:Math.abs(s.speed),power,
    ...characteristicElectrical({...s,receivedAt:new Date(s.receivedAt*1000).toISOString()},power)};
}

// Use synchronized measured DC-bus V/A, never nominal 24 V / 10 A or Iq.
export function characteristicElectrical(sample, outputW) {
  const wall=Date.parse(sample.receivedAt)/1000;
  const fresh=Number.isFinite(wall)&&Number.isFinite(sample.motorSupplyReceivedAt)
    &&Math.abs(wall-sample.motorSupplyReceivedAt)<=2;
  const voltage=fresh&&Number.isFinite(sample.motorSupplyVoltage)&&sample.motorSupplyVoltage>=0?sample.motorSupplyVoltage:null;
  const current=fresh&&Number.isFinite(sample.motorSupplyCurrent)&&sample.motorSupplyCurrent>=0?sample.motorSupplyCurrent:null;
  const inputPower=voltage!==null&&current!==null?voltage*current:null;
  return {voltage,current,inputPower,efficiency:inputPower>0&&Number.isFinite(outputW)?outputW/inputPower*100:null};
}

// Sliding statistics visit only incoming/expired samples, not the entire 60k window.
export class CharacteristicWindow {
  constructor() { this.clear(); }
  clear() { this.queue = []; this.head = 0; this.bins = new Map(); this.last = null; this.window = null; }
  update(samples, seconds) {
    let lastIndex = this.last ? samples.lastIndexOf(this.last) : -1;
    if (lastIndex < 0 || this.window !== seconds) { this.clear(); lastIndex = -1; }
    this.window = seconds;
    const end = samples.at(-1)?.t ?? 0;
    const start = Math.max(end - seconds, samples[0]?.t ?? 0);
    for (let i = lastIndex + 1; i < samples.length; i++) {
      const s = samples[i];
      if (s.t < start || !Number.isFinite(s.torque)) continue;
      const torque = Math.abs(s.torque);
      const key = torque.toFixed(Math.min(4, Math.max(0, s.torqueDecimals ?? 2)));
      const power=Number.isFinite(s.shaftPowerKw)?s.shaftPowerKw*1000:null;
      const electric=characteristicElectrical(s,power);
      const values = [power,electric.efficiency,electric.current,s.speed,electric.voltage,electric.inputPower];
      const row = this.bins.get(key) || { torque: 0, count: 0, sums: Array(6).fill(0), counts: Array(6).fill(0) };
      row.torque += torque; row.count++;
      for (let j=0;j<values.length;j++) if (Number.isFinite(values[j])) { row.sums[j]+=values[j]; row.counts[j]++; }
      this.bins.set(key,row);
      this.queue.push({ sample:s, key, torque, values });
    }
    this.last = samples.at(-1) ?? null;
    while (this.head < this.queue.length && (this.queue[this.head].sample.t < start || (seconds!==Infinity && this.queue.length-this.head > 60000))) {
      const old=this.queue[this.head++], row=this.bins.get(old.key);
      row.torque-=old.torque; row.count--;
      for (let j=0;j<old.values.length;j++) if (Number.isFinite(old.values[j])) { row.sums[j]-=old.values[j]; row.counts[j]--; }
      if (!row.count) this.bins.delete(old.key);
    }
    if (this.head > 4096) { this.queue=this.queue.slice(this.head); this.head=0; }
    return [...this.bins.values()];
  }
}

// Local quadratic (LOESS) fit inside measured torque coverage. rate 0..100:
// higher follows the measured bins more closely, lower smooths over more
// neighbours. It never extrapolates and never degenerates to a global line.
export function curveCharacteristic(points, key, rate = 60) {
  const valid = points
    .filter(p => Number.isFinite(p.torque) && Number.isFinite(p[key]))
    .map(p => ({torque: p.torque, value: p[key]}))
    .sort((a, b) => a.torque - b.torque);
  if (valid.length < 3) return valid.map(p => ({torque: p.torque, [key]: p.value}));
  const first = valid[0].torque, last = valid.at(-1).torque;
  if (!(last > first)) return [];
  const fitRate = Math.min(100, Math.max(0, Number(rate) || 0));
  const most = Math.min(valid.length, 120), least = Math.min(valid.length, 6);
  const neighbours = Math.max(3, Math.round(most - (most - least) * fitRate / 100));
  let low = Infinity, high = -Infinity;
  for (const p of valid) { low = Math.min(low, p.value); high = Math.max(high, p.value); }
  const count = 96, curve = [];
  for (let i = 0; i < count; i++) {
    const x = first + (last - first) * i / (count - 1);
    let lo = 0, hi = valid.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (valid[mid].torque < x) lo = mid + 1; else hi = mid;
    }
    const start = Math.max(0, Math.min(lo - Math.floor(neighbours / 2), valid.length - neighbours));
    let span = 0;
    for (let j = start; j < start + neighbours; j++) span = Math.max(span, Math.abs(valid[j].torque - x));
    let value;
    if (span === 0) value = valid[start].value;
    else {
      // Weighted least squares for value = a + b*u + c*u^2, u=(t-x)/span; only
      // the intercept is evaluated at u=0. The tricube kernel ends at u=+-1.
      let sw = 0, su = 0, su2 = 0, su3 = 0, su4 = 0, sy = 0, suy = 0, su2y = 0;
      for (let j = start; j < start + neighbours; j++) {
        const p = valid[j], u = (p.torque - x) / span, distance = Math.abs(u);
        if (distance >= 1) continue;
        const u2 = u * u, base = 1 - distance * distance * distance, w = base * base * base;
        sw += w; su += w * u; su2 += w * u2; su3 += w * u2 * u; su4 += w * u2 * u2;
        sy += w * p.value; suy += w * u * p.value; su2y += w * u2 * p.value;
      }
      const det = sw * (su2 * su4 - su3 * su3) - su * (su * su4 - su3 * su2) + su2 * (su * su3 - su2 * su2);
      value = Math.abs(det) > 1e-12
        ? (sy * (su2 * su4 - su3 * su3) - su * (suy * su4 - su3 * su2y) + su2 * (suy * su3 - su2 * su2y)) / det
        : sw > 0 ? sy / sw : valid[start].value;
      value = Math.min(high, Math.max(low, value));
    }
    curve.push({torque: x, [key]: value});
  }
  return curve;
}

// Least-squares trend only within measured torque coverage; never extrapolate.
export function linearCharacteristic(points, key) {
  const valid = points.filter(p => Number.isFinite(p.torque) && Number.isFinite(p[key]));
  if (valid.length < 2) return [];
  const x = valid.reduce((sum,p)=>sum+p.torque,0)/valid.length;
  const y = valid.reduce((sum,p)=>sum+p[key],0)/valid.length;
  let xx=0, xy=0;
  for (const p of valid) { xx+=(p.torque-x)**2; xy+=(p.torque-x)*(p[key]-y); }
  if (xx <= Number.EPSILON) return [];
  const slope=xy/xx;
  return [valid[0].torque,valid.at(-1).torque].map(torque=>({torque,[key]:y+slope*(torque-x)}));
}
