export interface EncoderPoint {torque: number | null; temp?: number | null; sensorRpm?: number | null; encoderRpm?: number | null}
interface Bucket {points: (EncoderPoint | undefined)[]; start: number; row: EncoderPoint | null}
// Rebuild only changed torque bins, preserving the original summation order.
export class EncoderBins {
  declare bins: Map<number, Bucket>;
  declare capacity: number;
  declare clipped: number;
  declare length: number;
  declare limit: number;
  declare points: (EncoderPoint | undefined)[];
  declare start: number;

  constructor(limit: number,capacity=12000) {
    this.capacity=capacity;this.points=new Array(capacity);this.start=0;this.length=0;
    this.limit=limit;this.bins=new Map();this.clipped=0;
  }
  update(point: EncoderPoint,direction: number) {
    const x=point.torque!;
    if(!Number.isFinite(x))return;
    if(x>this.limit) {this.clipped+=direction;return;}
    const key=Math.floor(x/Math.max(.001,this.limit/80));
    let bin=this.bins.get(key);
    if(!bin) {bin={points:[],start:0,row:null};this.bins.set(key,bin);}
    if(direction>0)bin.points.push(point);
    else {
      bin.points[bin.start++]=undefined;
      if(bin.start===bin.points.length) {this.bins.delete(key);return;}
      if(bin.start>=64&&bin.start*2>=bin.points.length) {bin.points=bin.points.slice(bin.start);bin.start=0;}
    }
    bin.row=null;
  }
  append(point: EncoderPoint) {
    if(this.length===this.capacity) {
      this.update(this.points[this.start]!,-1);this.points[this.start]=point;this.start=(this.start+1)%this.capacity;
    } else {this.points[(this.start+this.length)%this.capacity]=point;this.length++;}
    this.update(point,1);
  }
  setLimit(limit: number) {
    if(limit===this.limit)return;
    this.limit=limit;this.bins.clear();this.clipped=0;
    for(let i=0;i<this.length;i++)this.update(this.points[(this.start+i)%this.capacity]!,1);
  }
  rows() {
    return [...this.bins.values()].map(bucket=>{
      if(!bucket.row) {
        const bin={x:0,count:0,temp:0,tempN:0,sensor:0,sensorN:0,encoder:0,encoderN:0};
        for(let i=bucket.start;i<bucket.points.length;i++) {
          const point=bucket.points[i]!;bin.x+=point.torque!;bin.count++;
          if(Number.isFinite(point.temp)){bin.temp+=point.temp!;bin.tempN++;}
          if(Number.isFinite(point.sensorRpm)){bin.sensor+=point.sensorRpm!;bin.sensorN++;}
          if(Number.isFinite(point.encoderRpm)){bin.encoder+=point.encoderRpm!;bin.encoderN++;}
        }
        bucket.row={torque:bin.x/bin.count,temp:bin.tempN?bin.temp/bin.tempN:null,
          sensorRpm:bin.sensorN?bin.sensor/bin.sensorN:null,encoderRpm:bin.encoderN?bin.encoder/bin.encoderN:null};
      }
      return bucket.row!;
    }).sort((a,b)=>a.torque!-b.torque!);
  }
}
