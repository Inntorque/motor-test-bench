"""Explicit one-click discovery and per-host verified serial bindings. Never enables outputs."""
import copy
import json
import os
import threading
import time
from pathlib import Path
import serial
from serial.tools import list_ports
from sensor_hex import HexParser

NAMES = {'motor-supply': '电机电源', 'brake-supply': '制动电源',
         'torque-sensor': '动态扭矩传感器', 'current-motor': '电机驱动板'}


class ConnectionManager:
    def __init__(self, api, path):
        self.api, self.path = api, Path(path)
        self.lock = threading.RLock()
        self.job = {'running': False, 'report': [], 'candidates': []}

    def active(self):
        with self.lock:
            return self.job['running']

    def status(self):
        with self.lock:
            return copy.deepcopy(self.job)

    def profiles(self):
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding='utf-8'))

    def remember(self, key, config):
        with self.lock:
            profiles = self.profiles()
            item = next((p for p in list_ports.comports() if p.device == config.get('device')), None)
            identity = ({'vid': item.vid, 'pid': item.pid, 'serial': item.serial_number,
                         'location': item.location} if item else None)
            profiles[key] = {'config': dict(config), 'identity': identity}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temporary, self.path)

    @staticmethod
    def resolve(profile, ports):
        config = dict(profile['config'])
        if config.get('transport', 'serial') != 'serial':
            return config
        ident = profile.get('identity') or {}
        if ident.get('serial'):
            matches = [p for p in ports if (p.vid, p.pid, p.serial_number) ==
                       (ident['vid'], ident['pid'], ident['serial'])]
            if len(matches) != 1:
                raise ValueError('已绑定的 USB 设备不在或标识重复，请检查插线/重新选择串口')
            config['device'] = matches[0].device
        elif not any(p.device == config.get('device') for p in ports):
            raise ValueError('已配置串口不在，请重新选择串口')
        return config

    def start(self, hints):
        with self.lock:
            if self.job['running']:
                return self.status()
            a = self.api
            if (a['AUTOMATIC'].active() or a['BRAKE_CURRENT'].active() or
                    a['CURRENT_MOTOR'].status()['run']['running'] or a['WORKBENCH_RECORDINGS'].active()):
                raise ValueError('试验或记录运行期间不重新扫描接口，请先结束任务')
            self.job = {'running': True, 'report': ['正在枚举设备…'], 'candidates': []}
            threading.Thread(target=self._run, args=(copy.deepcopy(hints),), daemon=True).start()
            return self.status()

    def _run(self, hints):
        a = self.api
        devices = {**a['CHANNELS'], 'torque-sensor': a['TORQUE_SENSOR'], 'current-motor': a['CURRENT_MOTOR']}
        messages = {}
        def report(key, message):
            messages[key] = f'{NAMES[key]}：{message}'
            with self.lock:
                self.job['report'] = list(messages.values())
        try:
            ports = list(list_ports.comports())
            profiles = self.profiles()
            used = {d.config['device'] for k, d in devices.items() if k != 'current-motor'
                    and d.connected and d.config and d.config.get('device')}
            unresolved = []
            # Manual connection confirms new bindings. Auto-connect prefers host identity
            # over another browser's stale port name after USB enumeration changes.
            for key in ('motor-supply', 'brake-supply', 'torque-sensor'):
                channel = devices[key]
                if channel.connected:
                    report(key, '已连接，保持现有连接')
                    continue
                hint = hints.get(key) or {}
                profile = profiles.get(key)
                if not profile and (hint.get('device') or hint.get('host')):
                    profile = {'config': hint}
                if not profile:
                    unresolved.append(key)
                    continue
                report(key, '正在恢复已保存连接…')
                try:
                    config = self.resolve(profile, ports)
                    config = (a['normalized_sensor_config'](config) if key == 'torque-sensor'
                              else a['normalized_config'](config, key))
                    if config.get('device') in used:
                        raise ValueError('串口已被另一通道占用')
                    with a['CHANNELS_LOCK']:
                        result = channel.connect(config)
                    if not result['connected']:
                        raise ValueError('未收到有效设备应答')
                    used.add(config.get('device'))
                    self.remember(key, result['config'])
                    report(key, '已连接 ' + str(config.get('device') or config.get('host')))
                except Exception as error:
                    report(key, '未连接，' + str(error))
            # Exclude built-in UARTs and the CAN adapter owned by the motor service.
            available = [p for p in ports if p.vid is not None and p.vid != 0x3562 and p.device not in used]
            streaming = set()
            if unresolved:
                for p in available:
                    try:
                        parser = HexParser('hex6', a['crc16'])
                        with serial.Serial(p.device, baudrate=115200, timeout=.05,
                                           **({'exclusive': True} if os.name != 'nt' else {})) as stream:
                            deadline = time.monotonic() + .6
                            while time.monotonic() < deadline:
                                parser.feed(stream.read(min(8192, max(1, stream.in_waiting))))
                        if parser.crc_profile:
                            streaming.add(p.device)
                    except Exception as error:
                        with self.lock:
                            self.job['candidates'].append({'port': p.device, 'error': str(error)})
                if 'torque-sensor' in unresolved:
                    if len(streaming) == 1:
                        config = a['normalized_sensor_config'](dict(device=next(iter(streaming)), protocol='hex6',
                            baud=115200, stopbits=1, torqueDecimals=2, speedDecimals=1))
                        try:
                            result = devices['torque-sensor'].connect(config)
                            self.remember('torque-sensor', result['config'])
                            used.add(config['device'])
                            report('torque-sensor', '已连接 ' + config['device'] + '（HEX 115200，有效 CRC）')
                        except Exception as error:
                            report('torque-sensor', '未连接，' + str(error))
                    else:
                        report('torque-sensor', '未发现唯一有效 HEX 115200 数据源，请核对供电和通信参数')
                # Read-only identification cannot distinguish two identical supplies' wiring roles.
                missing = [k for k in unresolved if k in a['CHANNELS']]
                candidates = []
                if missing:
                    for p in available:
                        if p.device in streaming or p.device in used:
                            continue
                        probe = a['PowerSupplyChannel']('discovery')
                        try:
                            config = a['normalized_config'](dict(device=p.device, baud=9600, stopbits=1,
                                address=1, voltageDecimals=2, currentDecimals=2))
                            result = probe.connect(config)
                            candidates.append(p.device)
                            with self.lock:
                                self.job['candidates'].append({'port': p.device, 'type': 'ssj',
                                    'settings': result['snapshot']['settings']})
                        except Exception as error:
                            with self.lock:
                                self.job['candidates'].append({'port': p.device, 'error': str(error)})
                        finally:
                            probe.disconnect()
                    for key in missing:
                        report(key, ('发现电源 ' + '、'.join(candidates) + '；首次请选择对应串口并连接，随后自动记住'
                                     if candidates else '未发现有效 SSJ 应答，请检查权限、接线或手动配置协议'))
            report('current-motor', '正在恢复 CAN 并等待真实反馈…')
            try:
                if not devices['current-motor'].status()['connected']:
                    supply = devices['motor-supply'].status()
                    if supply['connected'] and not (supply.get('snapshot') or {}).get('status', 0) & 128:
                        report('current-motor', '未连接，电机电源输出关闭；请先开启电机电源，再点击一键连接')
                        return
                    devices['current-motor'].connect()
                report('current-motor', '已连接（真实反馈有效）')
            except Exception as error:
                report('current-motor', '未连接，' + str(error))
        except Exception as error:
            with self.lock:
                self.job['report'].append('自动连接失败：' + str(error))
        finally:
            with self.lock:
                self.job['running'] = False
