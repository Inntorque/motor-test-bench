// A bounded display trace for the whole trial. Full raw data remains in the
// existing user-controlled recorder. Keep per-20ms torque/speed extrema.
export class LoadTrial {
  constructor(run) { this.run=run; this.points=[]; this.bucket=null; this.lastWall=-Infinity; }
  update(run) {
    if (run.startedAt && run.startedAt!==this.run.startedAt) { this.points=[]; this.bucket=null; this.lastWall=-Infinity; }
    this.run=run;
    if(run.measurementEndedAt && this.lastWall>run.measurementEndedAt) {
      this.flush();
      this.points=this.points.filter(p=>Date.parse(p.receivedAt)/1000<=run.measurementEndedAt);
      this.lastWall=this.points.length?Date.parse(this.points.at(-1).receivedAt)/1000:-Infinity;
    }
  }
  append(incoming) {
    // Accept delayed sensor batches only up to the fixed measurement cutoff.
    if ((!this.run.running && !this.run.measurementEndedAt) || !this.run.startedAt) return false;
    let changed=false;
    for (const p of incoming) {
      const wall=Date.parse(p.receivedAt)/1000;
      if (!Number.isFinite(wall) || wall<=this.lastWall || wall<(this.run.curveStartedAt||this.run.startedAt) || (this.run.measurementEndedAt && wall>this.run.measurementEndedAt)) continue;
      this.lastWall=wall;
      changed=true;
      const key=Math.floor((wall-this.run.startedAt)*50);
      if (this.bucket?.key!==key) {
        this.flush(); this.bucket={key,first:p,last:p,min:p,max:p,slow:p,fast:p};
      } else {
        const b=this.bucket;b.last=p;
        if(p.torque<b.min.torque)b.min=p;
        if(p.torque>b.max.torque)b.max=p;
        if(p.speed<b.slow.speed)b.slow=p;
        if(p.speed>b.fast.speed)b.fast=p;
      }
    }
    return changed;
  }
  flush() {
    if (!this.bucket)return;
    const b=this.bucket;
    this.points.push(...[...new Set([b.first,b.min,b.max,b.slow,b.fast,b.last])].sort((a,c)=>a.t-c.t));
    this.bucket=null;
  }
  snapshot() {
    this.flush();
    return this.points.filter(p=>Date.parse(p.receivedAt)/1000>=(this.run.curveStartedAt||this.run.startedAt)
      && (!this.run.measurementEndedAt || Date.parse(p.receivedAt)/1000<=this.run.measurementEndedAt));
  }
}

// This is transmitted shaft torque, not a current-to-rated-brake-capacity map.
export function measuredBrakeLoad(samples) {
  const last=samples.at(-1);
  if (!last)return null;
  const valid=samples.filter(p=>p.t>=last.t-1 && p.loadEnabled===true && Number.isFinite(p.torque));
  if (!valid.length)return null;
  const speed=valid.filter(p=>Number.isFinite(p.speed)).map(p=>p.speed);
  const mean=values=>values.reduce((a,b)=>a+b,0)/values.length;
  const moving=speed.length===valid.length && Math.min(...speed)>.1;
  const steady=moving && valid.at(-1).t-valid[0].t>=.8 && Math.max(...speed)-Math.min(...speed)<=Math.max(.2,mean(speed)*.05);
  const fresh=Number.isFinite(last.brakeSupplyReceivedAt) && Math.abs(Date.parse(last.receivedAt)/1000-last.brakeSupplyReceivedAt)<=2;
  return {torque:mean(valid.map(p=>Math.abs(p.torque))),steady,moving,
    current:fresh?last.brakeSupplyCurrent:null,voltage:fresh?last.brakeSupplyVoltage:null};
}
