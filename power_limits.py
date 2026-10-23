"""Workbench supply limits and readback checks; device OVP/OCP are unchanged."""
import math

MOTOR_SUPPLY_CURRENT_LIMIT = 20
MOTOR_SUPPLY_PRESET_CURRENT_A = 20  # Operator's fixed preset; independent of the unchanged safety ceiling.


def voltage_setting_matches(raw, decimals):
    return isinstance(raw,(int,float)) and math.isfinite(raw) and abs(raw-24*10**decimals)<=1


def power_problem(key, config, snapshot):
    def problem(kind, message, **values):
        return dict(kind=kind,message=message,**values)
    status=snapshot.get('status')
    if not isinstance(status,int):return problem('invalid','电源状态读回无效')
    if config.get('protocol','ssj')=='ssj':
        # SSJ manual §2.4: bits 2/3 = OCP/OVP. CC/CV and low-voltage
        # indication are not protection trips (zero-excitation CC is valid).
        if status&12:return problem('hardware','设备报告'+('过流' if status&4 else '')+('过压' if status&8 else '')+'保护',status=status)
    elif any((snapshot.get('ruStatus') or {}).get(k) for k in ('ovp','ocp','otp')):
        return problem('hardware','设备报告 OVP/OCP/OTP 保护',status=snapshot['ruStatus'])
    if not status&128:return None
    settings=snapshot.get('settings') or [];measured=snapshot.get('measured') or []
    if len(settings)<3 or len(measured)<2 or any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v)
                                                 for v in (*settings[1:3],*measured[:2])):
        return problem('invalid','电源 V/A 读回不完整或无效')
    sv,sa=10**config['voltageDecimals'],10**config['currentDecimals']
    measured_sa=10**config.get('measuredCurrentDecimals',config['currentDecimals'])
    cap=MOTOR_SUPPLY_CURRENT_LIMIT if key=='motor-supply' else 2.3
    measured_cap=cap if key=='motor-supply' else cap*1.1
    if not voltage_setting_matches(settings[1],config['voltageDecimals']):
        return problem('setting_voltage',f'设定电压 {settings[1]/sv:g} V，要求 24 V（允许 1 个显示计数差异）',value=settings[1]/sv,limit=24,unit='V')
    if settings[2]<0 or settings[2]/sa>cap+1/sa+1e-9:
        return problem('setting_current',f'设定限流 {settings[2]/sa:g} A 超出 {cap:g} A',value=settings[2]/sa,limit=cap,unit='A')
    if measured[0]<0 or measured[1]<0:return problem('invalid','电源 V/A 读回为负，协议不支持该值')
    if measured[0]/sv>26.4+1e-9:
        return problem('measured_voltage',f'实测电压 {measured[0]/sv:g} V > 26.4 V',value=measured[0]/sv,limit=26.4,unit='V')
    if measured[1]/measured_sa>measured_cap+1e-9:
        return problem('measured_current',f'实测电流 {measured[1]/measured_sa:g} A > {measured_cap:g} A',value=measured[1]/measured_sa,limit=measured_cap,unit='A')
    return None


def power_evidence(snapshot):
    # Preserve original registers and RTU replies before an OFF overwrites them.
    return {key:snapshot.get(key) for key in ('receivedAt','status','settings','measured','ruStatus','logs')}
