export const TORQUE_SWEEP_DEFAULTS = {startNm:'0',stepNm:'0.1',intervalS:'2'};

export function torqueSweepError(form, calibration={}) {
  const values=Object.fromEntries(Object.keys(TORQUE_SWEEP_DEFAULTS).map(key=>[key,Number(form[key])]));
  if(Object.keys(values).some(key=>String(form[key]??'').trim()===''||!Number.isFinite(values[key])))return '请填写完整、有限的测试参数。';
  const min=calibration.minTargetNm??0,max=calibration.maxTargetNm??99.9;
  if(values.startNm<min||values.startNm>max)return `起始力矩为 ${min}–${max} N·m，不能达到传感器量程边界。`;
  if(values.stepNm<.01||values.stepNm>1)return '力矩步长为 0.01–1 N·m。';
  if(values.intervalS<.5||values.intervalS>30)return '每级力矩保持时间为 0.5–30 s。';
  if((form.motorMode??'maximum_output')==='maximum_output') {
    for(const [key,low,high] of [['negativeIdMinA',-30,0],['maxPhaseA',0,30]]) {
      const value=Number(form[key]??0);
      if(!Number.isFinite(value)||value<low||value>high)return '最大输出的 Id / 相电流设置无效。';
    }
  }
  return '';
}

export function torqueSweepRequest(form) {
  return {mode:'torque_sweep', motorMode:form.motorMode??'maximum_output',
    negativeIdMinA:Number(form.negativeIdMinA??0),maxPhaseA:Number(form.maxPhaseA??0),
    brake:{...Object.fromEntries(['startNm','stepNm','intervalS'].map(key=>[key,Number(form[key])])),maxNm:99.9}};
}
