export const SUPPLY_VOLTAGE = 24;
export const MOTOR_SUPPLY_CURRENT_LIMIT = 20;
export const MOTOR_SUPPLY_PRESET_CURRENT_A = 20;
export const BRAKE_SUPPLY_CURRENT_LIMIT = 2.3;

export function supplyOperatingMode(device) {
  const snapshot = device?.connected ? device.snapshot : null;
  if (!Number.isInteger(snapshot?.status)) return 'unknown';
  if (!(snapshot.status & 0x80)) return 'off';
  const ru = (device.config || snapshot.config)?.protocol === 'ru';
  const cc = ru ? snapshot.ruStatus?.cc : Boolean(snapshot.status & 0x20);
  const cv = ru ? snapshot.ruStatus?.cv : Boolean(snapshot.status & 0x40);
  if (cc === true && cv === false) return 'cc';
  if (cv === true && cc === false) return 'cv';
  return 'unknown';
}

export function brakeModeText(device) {
  return {
    cc: '实际状态：恒流 CC',
    cv: '实际状态：恒压 CV，尚未进入恒流；请核对目标电流与负载，电压上限保持 24 V。',
    off: '实际状态：输出关闭，待开启后确认恒流状态',
    unknown: '实际状态：CC/CV 未确认',
  }[supplyOperatingMode(device)];
}

export function supplyPoint(id, point = {}) {
  return { voltage: String(SUPPLY_VOLTAGE),
    current: id === 'motor-supply' ? String(MOTOR_SUPPLY_PRESET_CURRENT_A) : point.current ?? '' };
}

export function supplyReadbackPoint(id, previous, settings, config, applied = false) {
  const point = supplyPoint(id, previous);
  if (id === 'brake-supply' && (applied || point.current === '') && Number.isFinite(settings?.[2])) {
    point.current = (settings[2] / 10 ** Number(config.currentDecimals ?? 2)).toFixed(Number(config.currentDecimals ?? 2));
  }
  return point;
}

// Readback precision can differ from the setpoint register precision.
export function supplyMeasurements(device) {
  const snapshot=device?.snapshot;
  if (!device?.connected || !snapshot) return null;
  const config=snapshot.config || device.config || {};
  const voltageDecimals=Number(config.voltageDecimals ?? 2);
  const currentDecimals=Number(config.measuredCurrentDecimals ?? config.currentDecimals ?? 2);
  const measured=snapshot.measured || [];
  return {
    voltage:Number.isFinite(measured[0])?measured[0]/10**voltageDecimals:null,
    current:Number.isFinite(measured[1])?measured[1]/10**currentDecimals:null,
    power:Number.isFinite(snapshot.calculatedPowerW)?snapshot.calculatedPowerW:Number.isFinite(measured[2])?measured[2]:null,
    voltageDecimals,currentDecimals,
    output:Number.isFinite(snapshot.status)?(snapshot.status&0x80?'输出开启':'输出关闭'):'状态未知',
  };
}
