export const SUPPLY_VOLTAGE = 24;
export const MOTOR_SUPPLY_CURRENT_LIMIT = 10;
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
    current: id === 'motor-supply' ? String(MOTOR_SUPPLY_CURRENT_LIMIT) : point.current ?? '' };
}

export function supplyReadbackPoint(id, previous, settings, config, applied = false) {
  const point = supplyPoint(id, previous);
  if (id === 'brake-supply' && (applied || point.current === '') && Number.isFinite(settings?.[2])) {
    point.current = (settings[2] / 10 ** Number(config.currentDecimals ?? 2)).toFixed(Number(config.currentDecimals ?? 2));
  }
  return point;
}
