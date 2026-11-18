import {shaftPowerKw} from '../web/src/model.ts';

export interface RecordingContext {
  runId?: string; kind?: string; phase?: string;
  targetNm?: number; measuredNm?: number; currentA?: number;
}
export const alignmentLimits = {'current-motor':1.5,'torque-sensor':1,'motor-supply':3.5,'brake-supply':3.5};
const number = value => Number.isFinite(value) ? value : null;
const scaled = (value, decimals) => Number.isFinite(value) && Number.isFinite(decimals) ? value / 10 ** decimals : null;
export function sensorColumns(torque, speed) {
  const kw = shaftPowerKw(torque,speed);
  return {torque_Nm:number(torque),sensor_speed_rpm:number(speed),shaft_power_W:kw == null ? null : kw * 1000,
    shaft_power_signed_W:Number.isFinite(torque) && Number.isFinite(speed) ? torque * speed / 9550 * 1000 : null};
}
export function motorColumns(snapshot, ratio) {
  const feedback = snapshot.feedback || {}, live = feedback.stale === false;
  const phase = snapshot.phaseCurrent || {}, phaseLive = phase.stale === false;
  const position = feedback.position_stale === false ? number(feedback.position_deg) : null;
  const speed = live ? number(feedback.speed_rpm) : null;
  return {commanded_iq_A:scaled(snapshot.control?.iq_target_ma,3),
    actual_iq_A:live ? number(feedback.iq_a) : null,actual_id_A:live ? number(feedback.id_a) : null,
    reported_iq_reference_A:live ? number(feedback.iq_reference_a) : null,
    phase_a_A:phaseLive ? scaled(phase.ia_mA,3) : null,phase_b_A:phaseLive ? scaled(phase.ib_mA,3) : null,phase_c_A:phaseLive ? scaled(phase.ic_mA,3) : null,
    rotor_position_deg:position,output_position_deg:position != null && ratio > 0 ? position / ratio : null,
    rotor_speed_rpm:speed,output_speed_rpm:speed != null && ratio > 0 ? speed / ratio : null,
    motor_ntc_C:snapshot.ntc?.valid === true ? number(snapshot.ntc.temp_c) : null,
    motor_output_on:snapshot.motor?.output_on ?? null,motor_mode:snapshot.motor?.mode ?? null,motor_fault:snapshot.motor?.fault ?? null,
    motor_sample_sequence:feedback.sampleSeq ?? null,motor_device_time_us:feedback.deviceTimeUs ?? null,motor_boot_id:snapshot.bootId ?? null};
}
export function supplyColumns(snapshot) {
  const config = snapshot.config || {}, measured = snapshot.measured || [], settings = snapshot.settings || [];
  const voltage = scaled(measured[0],config.voltageDecimals), current = scaled(measured[1],config.measuredCurrentDecimals ?? config.currentDecimals);
  return {supply_target_voltage_V:scaled(settings[1],config.voltageDecimals),supply_target_current_A:scaled(settings[2],config.currentDecimals),
    supply_actual_voltage_V:voltage,supply_actual_current_A:current,
    supply_actual_power_W:voltage != null && current != null ? voltage * current : null,
    supply_output_on:Number.isFinite(snapshot.status) ? Boolean(snapshot.status & 128) : null,
    supply_max_voltage_V:number(config.maxVoltage),supply_max_current_A:number(config.maxCurrent)};
}
export function brakeColumns(context: RecordingContext) {
  return {brake_target_torque_Nm:number(context.targetNm),brake_feedback_torque_Nm:number(context.measuredNm),brake_command_current_A:number(context.currentA)};
}
export const motorFields = Object.keys(motorColumns({},null));
export const sensorFields = Object.keys(sensorColumns(null,null));
export const supplyFields = Object.keys(supplyColumns({}));
export const qualityFields = ['connected','valid','received_unix_s','age_s'];
export const channelPrefixes = {'current-motor':'motor','torque-sensor':'sensor','motor-supply':'motor_supply','brake-supply':'brake_supply'};
export const measurementFields = [...motorFields,...sensorFields,...Object.keys(brakeColumns({})),
  ...Object.values(channelPrefixes).flatMap(prefix=>qualityFields.map(field=>`${prefix}_${field}`)),
  ...['motor','brake'].flatMap(prefix=>supplyFields.map(field=>`${prefix}_${field}`))];
export function alignedColumns(devices, stamp, ratio, context: RecordingContext) {
  const row: Record<string, unknown> = {...brakeColumns(context)};
  for (const [id,prefix] of Object.entries(channelPrefixes)) {
    const device = devices[id], snapshot = device?.snapshot;
    const received = number(snapshot?.receivedAt), age = received == null ? null : stamp - received;
    const limit = id === 'torque-sensor' && device?.config?.protocol === 'modbus' ? 3.5 : alignmentLimits[id];
    const valid = !!device?.connected && age != null && age >= 0 && age <= limit;
    Object.assign(row,{[`${prefix}_connected`]:!!device?.connected,[`${prefix}_valid`]:valid,[`${prefix}_received_unix_s`]:received,[`${prefix}_age_s`]:age});
    if (!valid) continue;
    if (id === 'current-motor') Object.assign(row,motorColumns(snapshot,ratio));
    else if (id === 'torque-sensor') Object.assign(row,sensorColumns(snapshot.torque,snapshot.speed));
    else for (const [field,value] of Object.entries(supplyColumns(snapshot))) row[`${id === 'motor-supply' ? 'motor' : 'brake'}_${field}`] = value;
  }
  return row;
}
