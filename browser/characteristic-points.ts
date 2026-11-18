interface SensorObservation {receivedAt: number; torque: number; speed: number}
interface SupplyObservation {connected?: boolean; config?: {voltageDecimals?: number; currentDecimals?: number; measuredCurrentDecimals?: number}; snapshot?: {receivedAt: number; measured?: number[]}}

export interface CharacteristicPoint {
  runId: string; stage: number; kind: 'stage' | 'stall'; torque: number; measuredTorque: number;
  speed: number; power: number; voltage: number | null; current: number | null;
  inputPower: number | null; efficiency: number | null; receivedAt: number;
  startedAt: number; sampleCount: number; electricalCount: number;
  scheduledAt?: number;
}
export const characteristicFields = ['runId','stage','kind','torque','speed','power','voltage','current','inputPower','efficiency','startedAt','receivedAt','sampleCount','electricalCount','toleranceNm','scheduledAt'];
export function isOutputPoint(point: {kind?: string; torque?: number}) {
  return (point.kind === 'stage' || point.kind === 'stall') && Number.isFinite(point.torque);
}
export const CHARACTERISTIC_INTERVAL_SECONDS = 2;
export const CHARACTERISTIC_WINDOW_SECONDS = .5;
export const CHARACTERISTIC_TIMING_TOLERANCE = .1;

export function characteristicObservation(sensor: SensorObservation, supply: SupplyObservation) {
  const power=Math.abs(sensor.torque*sensor.speed)/9550*1000;
  const age=supply.snapshot?sensor.receivedAt-supply.snapshot.receivedAt:Infinity;
  const scaled=(index: number,decimals: number | undefined)=>{
    const raw=supply.snapshot?.measured?.[index];
    return supply.connected&&age>=0&&age<=2&&raw!=null&&Number.isFinite(raw)&&raw>=0&&decimals!=null&&Number.isFinite(decimals)?raw/10**decimals:null;
  };
  const voltage=scaled(0,supply.config?.voltageDecimals),current=scaled(1,supply.config?.measuredCurrentDecimals??supply.config?.currentDecimals);
  return {receivedAt:sensor.receivedAt,measuredTorque:Math.abs(sensor.torque),speed:Math.abs(sensor.speed),power,
    voltage,current,inputPower:voltage!=null&&current!=null?voltage*current:null};
}

export class StagePointCollector {
  private count = 0;
  private firstStamp = 0;
  private lastStamp = -Infinity;
  private complete = false;
  private torqueSum = 0;
  private speedSum = 0;
  private powerSum = 0;
  private voltageSum = 0;
  private voltageCount = 0;
  private currentSum = 0;
  private currentCount = 0;
  private inputSum = 0;
  private pairedPowerSum = 0;
  private pairedCount = 0;
  readonly runId: string; readonly stage: number; readonly target: number;
  readonly startedAt: number; readonly deadline: number;
  constructor(runId: string,stage: number,target: number,startedAt: number,intervalSeconds: number) {
    this.runId=runId;this.stage=stage;this.target=target;this.startedAt=startedAt;this.deadline=startedAt+intervalSeconds;
  }
  update(sensor: SensorObservation, supply: SupplyObservation): void {
    const stamp = sensor.receivedAt;
    if (this.complete || !Number.isFinite(stamp) || stamp < Math.max(this.startedAt,this.deadline-CHARACTERISTIC_WINDOW_SECONDS)
      || stamp > this.deadline || stamp <= this.lastStamp || !Number.isFinite(sensor.torque) || !Number.isFinite(sensor.speed) || sensor.speed === 0) return;
    const row = characteristicObservation(sensor,supply);
    if (!this.count) this.firstStamp=stamp;
    this.lastStamp=stamp;
    this.count++;
    this.torqueSum+=row.measuredTorque;
    this.speedSum+=row.speed;
    this.powerSum+=row.power;
    if (row.voltage!=null) {this.voltageSum+=row.voltage;this.voltageCount++;}
    if (row.current!=null) {this.currentSum+=row.current;this.currentCount++;}
    if (row.inputPower!=null) {this.inputSum+=row.inputPower;this.pairedPowerSum+=row.power;this.pairedCount++;}
  }
  finish(): CharacteristicPoint {
    if(this.complete)throw new Error('本级特性点已完成');
    this.complete=true;
    if(!this.count)throw new Error(`固定 ${CHARACTERISTIC_INTERVAL_SECONDS} 秒周期内，${this.target.toFixed(2)} N·m 级末段无有效轴端读数；停止加载，不延长周期`);
    return {runId:this.runId,stage:this.stage,kind:'stage',torque:this.target,measuredTorque:this.torqueSum/this.count,
      speed:this.speedSum/this.count,power:this.powerSum/this.count,
      voltage:this.voltageCount?this.voltageSum/this.voltageCount:null,current:this.currentCount?this.currentSum/this.currentCount:null,
      inputPower:this.pairedCount?this.inputSum/this.pairedCount:null,efficiency:this.inputSum>0?this.pairedPowerSum/this.inputSum*100:null,
      startedAt:this.firstStamp,receivedAt:this.lastStamp,scheduledAt:this.deadline,sampleCount:this.count,electricalCount:this.pairedCount};
  }
  stall(sensor: SensorObservation,supply: SupplyObservation): CharacteristicPoint | null {
    if(this.complete || !Number.isFinite(sensor.receivedAt) || sensor.receivedAt<this.startedAt || sensor.speed!==0 || !Number.isFinite(sensor.torque))return null;
    this.complete=true;
    const row=characteristicObservation(sensor,supply);
    return {...row,runId:this.runId,stage:this.stage,kind:'stall',torque:this.target,
      efficiency:row.inputPower!=null && row.inputPower>0?0:null,startedAt:row.receivedAt,sampleCount:1,electricalCount:row.inputPower!=null?1:0};
  }
}
