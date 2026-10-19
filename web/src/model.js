// Data export and display helpers. Live measurements are supplied by device
// adapters; this module never invents equipment readings.
const CSV_FIELDS = [
  ["source", "source"],
  ["receivedAt", "receivedAt"],
  ["t", "elapsedSeconds"],
  ["torque", "gearboxOutputTorqueNm"],
  ["speed", "gearboxOutputSpeedRpm"],
  ["shaftPowerKw", "calculatedOutputPowerKW"],
  ["current", "brakeCurrentA"],
  ["motorSupplyVoltage", "motorSupplyVoltageV"],
  ["motorSupplyCurrent", "motorSupplyCurrentA"],
  ["motorSupplyPower", "motorSupplyPowerW"],
  ["brakeSupplyVoltage", "brakeSupplyVoltageV"],
  ["brakeSupplyCurrent", "brakeSupplyCurrentA"],
  ["brakeSupplyPower", "brakeSupplyPowerW"],
  ["targetSpeed", "motorTargetSpeedRpm"],
  ["targetCurrent", "brakeTargetCurrentA"],
  ["motorEnabled", "motorEnabled"],
  ["loadEnabled", "brakeOutputEnabled"],
  ["range", "torqueSensorRangeNm"],
];

export function validateSetpoint(value, kind, { max = Infinity, step = 0.01 } = {}) {
  const label = kind === "current" ? "电流" : "转速";
  const numeric = typeof value === "number" || (typeof value === "string" && value.trim() !== "")
    ? Number(value)
    : NaN;
  if (!Number.isFinite(numeric)) {
    return { ok: false, value: null, error: `请输入有效的${label}数值` };
  }
  if (numeric < 0 || numeric > max) {
    return { ok: false, value: null, error: `${label}必须在 0–${Number.isFinite(max) ? max : "设备可设范围"} 范围内` };
  }
  const rounded = Math.round(numeric / step) * step;
  const tolerance = Number.EPSILON * 8 * Math.max(1, Math.abs(numeric));
  if (Math.abs(numeric - rounded) > tolerance) {
    return { ok: false, value: null, error: `设定步进为 ${step}` };
  }
  return { ok: true, value: Number(rounded.toFixed(8)), error: "" };
}

function csvCell(value) {
  if (value == null) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function toCsv(samples) {
  if (!Array.isArray(samples)) throw new TypeError("Samples must be an array");
  const rows = [CSV_FIELDS.map(([, heading]) => heading).join(",")];
  const origin = samples[0]?.t ?? 0;
  for (const sample of samples) {
    rows.push(CSV_FIELDS.map(([field]) => csvCell(
      field === "t" && Number.isFinite(sample.t) ? sample.t - origin : sample[field],
    )).join(","));
  }
  return `\uFEFF${rows.join("\r\n")}\r\n`;
}

export function formatDuration(seconds) {
  const total = Number.isFinite(seconds) ? Math.floor(Math.max(0, seconds)) : 0;
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

// EVG-D200 manual §12.1: T = 9550 * P(kW) / n(rpm).
export function shaftPowerKw(torqueNm, speedRpm) {
  return Number.isFinite(torqueNm) && Number.isFinite(speedRpm)
    ? Math.abs(torqueNm * speedRpm) / 9550 : null;
}

// Motor reference project reports NTC temperature in °C via CAN 0xA2.
export function motorTemperatureC(device) {
  const ntc = device?.connected ? device.snapshot?.ntc : null;
  return ntc?.valid === true && !ntc.fault && Number.isFinite(ntc.temp_c) &&
    Number.isFinite(ntc.age_s) && ntc.age_s >= 0 && ntc.age_s <= 1.5
    ? ntc.temp_c : null;
}

export function motorTargetCurrentA(device) {
  if (!device?.connected) return null;
  if (device.run?.running && Number.isFinite(device.run.targetA)) return device.run.targetA;
  const snapshot = device.snapshot;
  const control = snapshot?.control;
  const age = snapshot?.receivedAt - control?.updated_at;
  return Number.isFinite(control?.iq_target_ma) && Number.isFinite(age) && age >= 0 && age <= 1.5
    ? control.iq_target_ma / 1000 : null;
}

export function motorOutputFeedback(device) {
  const snapshot = device?.connected ? device.snapshot : null;
  const ratio = snapshot?.motorProfile?.nameplate?.reduction_ratio;
  const feedback = snapshot?.feedback;
  if (!Number.isFinite(ratio) || ratio <= 0 || feedback?.position_stale !== false)
    return {positionDeg:null, speedRpm:null};
  return {
    positionDeg:Number.isFinite(feedback.position_deg) ? feedback.position_deg / ratio : null,
    speedRpm:feedback.stale === false && Number.isFinite(feedback.speed_rpm) ? feedback.speed_rpm / ratio : null,
  };
}

export function motorDeviceState(device) {
  const motor = device?.connected ? device.snapshot?.motor : null;
  if (!motor || motor.available !== true || !Number.isFinite(motor.age_s) || motor.age_s > 1.5)
    return '状态未知';
  if (motor.fault || motor.state === 3) return '保护触发';
  if (motor.output_on === true) return motor.state === 1 ? '启动中 · 输出开启' : '运行中 · 输出开启';
  if (motor.state === 0 && motor.output_on === false) return '已停止 · 输出关闭';
  return '状态待确认';
}

export function motorStepInputError(form, limitA = 5) {
  const fields = [['stepA', '电流步长'], ['intervalS', '步进间隔'], ['durationS', '持续时间']];
  const missing = fields.filter(([key]) => String(form[key] ?? '').trim() === '').map(([, label]) => label);
  if (missing.length) return `请填写${missing.join('、')}后启动。`;
  const step = Number(form.stepA), interval = Number(form.intervalS), duration = Number(form.durationS);
  if (!Number.isFinite(step) || step < 0.001 || step > limitA || Math.abs(step * 1000 - Math.round(step * 1000)) >= 1e-6)
    return `电流步长需为 0.001–${limitA} A，最多三位小数。`;
  if (!Number.isFinite(interval) || interval < 0.2 || interval > 10) return '步进间隔需为 0.2–10 s。';
  if (!Number.isFinite(duration) || duration < 1 || duration > 255) return '持续时间需为 1–255 s。';
  return '';
}

export function characteristicAxisMax(maximum) {
  if (!Number.isFinite(maximum) || maximum <= 0) return 1;
  if (maximum >= 10) return Math.ceil(maximum / 10) * 10;
  const magnitude = 10 ** Math.floor(Math.log10(maximum));
  const normalized = maximum / magnitude;
  return [1, 2, 2.5, 5, 10].find(step => step >= normalized) * magnitude;
}
