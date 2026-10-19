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
      const values = [Number.isFinite(s.shaftPowerKw) ? s.shaftPowerKw * 1000 : null,
        s.efficiencyPercent, s.current, s.speed];
      const row = this.bins.get(key) || { torque: 0, count: 0, sums: [0,0,0,0], counts: [0,0,0,0] };
      row.torque += torque; row.count++;
      for (let j=0;j<4;j++) if (Number.isFinite(values[j])) { row.sums[j]+=values[j]; row.counts[j]++; }
      this.bins.set(key,row);
      this.queue.push({ sample:s, key, torque, values });
    }
    this.last = samples.at(-1) ?? null;
    while (this.head < this.queue.length && (this.queue[this.head].sample.t < start || this.queue.length-this.head > 60000)) {
      const old=this.queue[this.head++], row=this.bins.get(old.key);
      row.torque-=old.torque; row.count--;
      for (let j=0;j<4;j++) if (Number.isFinite(old.values[j])) { row.sums[j]-=old.values[j]; row.counts[j]--; }
      if (!row.count) this.bins.delete(old.key);
    }
    if (this.head > 4096) { this.queue=this.queue.slice(this.head); this.head=0; }
    return [...this.bins.values()];
  }
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
