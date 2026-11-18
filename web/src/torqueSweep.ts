export const TORQUE_SWEEP_DEFAULTS = {startNm:'0',stepNm:'0.1'};

export function torqueSweepError(form, calibration: {minTargetNm?: number; maxTargetNm?: number}={}, capabilities?: {currentLoopMinA?:number; currentLoopMaxA?:number}) {
  const values=Object.fromEntries(Object.keys(TORQUE_SWEEP_DEFAULTS).map(key=>[key,Number(form[key])]));
  if(Object.keys(values).some(key=>String(form[key]??'').trim()===''||!Number.isFinite(values[key])))return '请填写完整、有限的测试参数。';
  const min=calibration.minTargetNm??0,max=calibration.maxTargetNm??99.9;
  if(values.startNm<min||values.startNm>max)return `起始力矩须在 ${min}–${max} N·m 范围内。`;
  if(values.stepNm<.01||values.stepNm>1)return '力矩步长为 0.01–1 N·m。';
  if(form.motorMode==='current_loop') {
    const value=Number(form.targetA);
    const minA=capabilities?.currentLoopMinA,maxA=capabilities?.currentLoopMaxA;
    if(!Number.isFinite(minA)||!Number.isFinite(maxA))return '等待读取驱动板电流范围。';
    if(String(form.targetA??'').trim()===''||!Number.isFinite(value)||value<minA||value>maxA)return `目标 Iq 为 ${minA} 至 ${maxA} A 峰值。`;
  }
  return '';
}

export function torqueSweepRequest(form, calibration: {maxTargetNm?: number}={}) {
  return {mode:'torque_sweep',motorMode:form.motorMode==='current_loop'?'current_loop':'dyno',
    targetA:form.motorMode==='current_loop'?Number(form.targetA):0,
    brake:{...Object.fromEntries(['startNm','stepNm'].map(key=>[key,Number(form[key])])),maxNm:calibration.maxTargetNm??99.9}};
}
