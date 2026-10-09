// This entire module is a UI demonstration. Its response curves, current limits,
// and torque relationship are invented; they are not equipment specifications.
const DEMO_LIMITS = {
  speed: { min: 0, max: 1500, label: "转速", unit: "rpm" },
  current: { min: 0, max: 2, label: "电流", unit: "A" },
};

const CSV_FIELDS = [
  ["source", "source"],
  ["receivedAt", "receivedAt"],
  ["t", "elapsedSeconds"],
  ["torque", "gearboxOutputTorqueNm"],
  ["speed", "gearboxOutputSpeedRpm"],
  ["current", "brakeCurrentA"],
  ["targetSpeed", "motorTargetSpeedRpm"],
  ["targetCurrent", "brakeTargetCurrentA"],
  ["motorEnabled", "motorEnabled"],
  ["loadEnabled", "brakeOutputEnabled"],
  ["range", "torqueSensorRangeNm"],
];

export function createDemoState(range = 50) {
  return {
    range: range === 100 ? 100 : 50,
    t: 0,
    torque: 0,
    speed: 0,
    current: 0,
    targetSpeed: 300,
    targetCurrent: 0.5,
    motorEnabled: false,
    loadEnabled: false,
    sensorConnected: true,
    motorConnected: true,
    supplyConnected: true,
  };
}

export function validateSetpoint(value, kind) {
  const limits = DEMO_LIMITS[kind];
  if (!limits) return { ok: false, value: null, error: "未知的设定类型" };
  const numeric =
    typeof value === "number" ||
    (typeof value === "string" && value.trim() !== "")
      ? Number(value)
      : NaN;
  if (!Number.isFinite(numeric)) {
    return { ok: false, value: null, error: `请输入有效的${limits.label}数值` };
  }
  if (numeric < limits.min || numeric > limits.max) {
    return {
      ok: false,
      value: null,
      error: `演示${limits.label}范围为 ${limits.min}–${limits.max} ${limits.unit}`,
    };
  }
  if (kind === "current") {
    const rounded = Math.round(numeric * 100) / 100;
    const tolerance = Number.EPSILON * 8 * Math.max(1, Math.abs(numeric));
    if (Math.abs(numeric - rounded) > tolerance) {
      return {
        ok: false,
        value: null,
        error: "电流设定最多保留两位小数（步进 0.01 A）",
      };
    }
    return { ok: true, value: rounded, error: "" };
  }
  return { ok: true, value: numeric, error: "" };
}

function approach(value, target, dt, timeConstant) {
  const next = value + (target - value) * -Math.expm1(-dt / timeConstant);
  return Math.abs(next) < 1e-10 ? 0 : next;
}

export function stepDemo(state, dt) {
  if (!Number.isFinite(dt) || dt < 0)
    throw new RangeError("dt must be a finite, non-negative number");
  if (dt === 0) return { ...state };

  const speedInput = validateSetpoint(state.targetSpeed, "speed");
  const currentInput = validateSetpoint(state.targetCurrent, "current");
  const wantedSpeed =
    state.motorEnabled && state.motorConnected && speedInput.ok
      ? speedInput.value
      : 0;
  const wantedCurrent =
    state.loadEnabled && state.supplyConnected && currentInput.ok
      ? currentInput.value
      : 0;
  const t = state.t + dt;
  const speed = approach(state.speed, wantedSpeed, dt, 0.85);
  const current = approach(state.current, wantedCurrent, dt, 0.45);

  // Invented visual response only. 0–2 A is a fixed demo input range for both
  // 50 and 100 Nm profiles; neither profile implies a real coil current rating.
  const rotationFactor = Math.min(speed / 80, 1);
  const ripple = 1 + 0.018 * Math.sin(t * 5.4) + 0.008 * Math.sin(t * 13.2);
  const wantedTorque =
    state.range * 0.72 * (current / 2) * rotationFactor * ripple;
  const torque = approach(state.torque, wantedTorque, dt, 0.22);
  return { ...state, t, speed, current, torque };
}

export function sampleFromState(state, wallTime = Date.now()) {
  return {
    source: "simulation",
    receivedAt: new Date(wallTime).toISOString(),
    t: state.t,
    torque: state.sensorConnected ? state.torque : null,
    speed: state.sensorConnected ? state.speed : null,
    current: state.supplyConnected ? state.current : null,
    targetSpeed: state.targetSpeed,
    targetCurrent: state.targetCurrent,
    motorEnabled: state.motorEnabled,
    loadEnabled: state.loadEnabled,
    range: state.range,
  };
}

function csvCell(value) {
  if (value == null) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function toCsv(samples) {
  // Preserve stored v1 sample keys while making exported shaft location and
  // electrical source explicit. Legacy demo recordings use the same mapping.
  const rows = [CSV_FIELDS.map(([, heading]) => heading).join(",")];
  const origin = samples[0]?.t ?? 0;
  for (const sample of samples) {
    rows.push(
      CSV_FIELDS.map(([field]) =>
        csvCell(field === "t" ? sample.t - origin : sample[field]),
      ).join(","),
    );
  }
  return `\uFEFF${rows.join("\r\n")}\r\n`;
}

export function formatDuration(seconds) {
  const total = Number.isFinite(seconds) ? Math.floor(Math.max(0, seconds)) : 0;
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}
