export const SETUP_CSV_FIELDS = [
  ['id', 'test_setup_id'], ['name', 'test_setup_name'], ['gearboxName', 'gearbox_name'],
  ['hasGearbox', 'has_gearbox'], ['ratio', 'reduction_ratio'], ['axis', 'measurement_axis'],
  ['massKg', 'measured_object_mass_kg'], ['volumeL', 'measured_object_volume_L'],
];

export function setupText(setup) {
  if (!setup) return '测试配置未选择';
  return `${setup.name} · ${setup.ratio}:1 · ${setup.axis === 'motor' ? '电机轴直测' : '减速箱输出端'}${setup.ratioConfirmed === false ? '（减速比待确认）' : ''}`;
}

export function subjectText(subject) {
  if (!subject?.motor && !subject?.driverBoard) return '未标注';
  return `电机：${subject.motor?.name || '未选择'} · 驱动板：${subject.driverBoard?.name || '未选择'} · ${subject.testSetup ? setupText(subject.testSetup) : '测试配置未记录'}`;
}
