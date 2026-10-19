"""Measurement-only test sessions; no actuator commands or sensor writes."""
import csv
import json
import math
import re
import threading
import time
import uuid
from pathlib import Path

KINDS = {'baseline': '零加载基线', 'ripple': '稳态波动', 'temperature': 'NTC 温度记录',
         'loaded_peak': '自动带载逐帧扭矩记录'}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class Stats:
    def __init__(self):
        self.n = 0
        self.mean = self.m2 = 0.
        self.minimum = self.maximum = self.first = self.last = None

    def add(self, value):
        if not finite(value):
            return
        if not self.n:
            self.first = self.minimum = self.maximum = value
        self.n += 1
        delta = value - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (value - self.mean)
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)
        self.last = value

    def result(self, floor=0):
        if not self.n:
            return {'count': 0}
        std = math.sqrt(max(0, self.m2 / self.n))
        normalized = abs(self.mean) > floor
        return {'count': self.n, 'mean': self.mean, 'min': self.minimum, 'max': self.maximum,
                'first': self.first, 'last': self.last, 'std': std,
                'peakToPeak': self.maximum - self.minimum,
                'rms': math.sqrt(std * std + self.mean * self.mean),
                'peakToPeakPercent': (self.maximum - self.minimum) / abs(self.mean) * 100 if normalized else None,
                'cvPercent': std / abs(self.mean) * 100 if normalized else None}


class SpecializedTests:
    def __init__(self, root, source, frames):
        self.root = Path(root)
        self.source = source
        self.frames = frames
        self.lock = threading.RLock()
        self.control = threading.Lock()
        self.thread = None
        self.cancel = threading.Event()
        self.live = None
    def recover_interrupted(self):
        # Called only after the production server has bound its port.
        if self.root.exists():
            for path in self.root.glob('*/report.json'):
                try:
                    report = json.loads(path.read_text(encoding='utf-8'))
                    if report.get('state') in ('running', 'settling'):
                        report.update(state='interrupted', reason='采集服务重启，测试中断', finishedAt=time.time(), valid=False)
                        self._save(path, report)
                except (ValueError, OSError):
                    pass

    @staticmethod
    def _save(path, report):
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        temporary.replace(path)

    @staticmethod
    def config(data):
        if not isinstance(data, dict):
            raise ValueError('测试参数需为对象')
        kind = data.get('kind')
        if kind not in KINDS:
            raise ValueError('未知专项测试')
        def number(key, default, low, high):
            value = data.get(key, default)
            if not finite(value) or not low <= value <= high:
                raise ValueError(f'{key} 需在 {low}～{high} 之间')
            return value
        ambient = data.get('ambientC')
        if ambient is not None and (not finite(ambient) or not -40 <= ambient <= 100):
            raise ValueError('环境温度需为 -40～100 °C，或留空')
        return {'kind': kind, 'name': str(data.get('name') or KINDS[kind])[:100],
                'durationS': number('durationS', 10, 1, 3600),
                'settleS': number('settleS', 0, 0, 300), 'ambientC': ambient,
                'torqueMeanFloorNm': number('torqueMeanFloorNm', .01, 0, 50),
                'speedMeanFloorRpm': number('speedMeanFloorRpm', 1, 0, 10000),
                'manualBrakeOff': data.get('manualBrakeOff') is True,
                'steadyConfirmed': data.get('steadyConfirmed') is True}

    @staticmethod
    def _fresh(channel, age):
        snapshot = channel.get('snapshot') or {}
        stamp = snapshot.get('receivedAt')
        return channel.get('connected') is True and finite(stamp) and 0 <= time.time() - stamp <= age

    def _check(self, config, source):
        kind = config['kind']
        if kind != 'temperature' and not self._fresh(source['sensor'], 1.5):
            raise ValueError('扭矩传感器未连接或读数已过期')
        if kind == 'baseline':
            brake = source['brake']
            known = self._fresh(brake, 3) and isinstance((brake.get('snapshot') or {}).get('status'), int)
            if known and brake['snapshot']['status'] & 0x80:
                raise ValueError('制动电源仍在输出，不能进行零加载基线测试')
            if not known and not config['manualBrakeOff']:
                raise ValueError('请连接制动电源确认 OFF，或在现场确认励磁已关闭并勾选人工确认')
        if kind == 'ripple' and not config['steadyConfirmed']:
            raise ValueError('请先保持工况稳定并确认测试期间不调整目标')
        motor = source['motor']
        if kind == 'temperature':
            snapshot = motor.get('snapshot') or {}
            ntc = snapshot.get('ntc') or {}
            if not self._fresh(motor, 1.5) or not ntc.get('valid') or ntc.get('fault') or not finite(ntc.get('temp_c')) or not finite(ntc.get('age_s')) or ntc['age_s'] < 0 or ntc['age_s'] + max(0, time.time() - snapshot.get('receivedAt', 0)) > 1.5:
                raise ValueError('电机未连接或 NTC 温度无效/过期')

    def availability(self):
        source = self.source()
        brake = source['brake']
        known = self._fresh(brake, 3) and isinstance((brake.get('snapshot') or {}).get('status'), int)
        temperature = True
        try:
            self._check({'kind': 'temperature'}, source)
        except ValueError:
            temperature = False
        return {'sensor': self._fresh(source['sensor'], 1.5), 'temperature': temperature,
                'brakeOutput': bool(brake['snapshot']['status'] & 0x80) if known else None}

    def status(self):
        with self.lock:
            return json.loads(json.dumps(self.live)) if self.live else None

    def history(self):
        reports = []
        if self.root.exists():
            for path in sorted(self.root.glob('*/report.json'), reverse=True):
                try:
                    reports.append(json.loads(path.read_text(encoding='utf-8')))
                except (ValueError, OSError):
                    continue
        return sorted(reports, key=lambda report: report['startedAt'], reverse=True)[:200]

    def file(self, test_id, name):
        if not re.fullmatch(r'[a-f0-9]{32}', test_id) or name not in ('data.csv', 'report.json'):
            raise ValueError('无效记录文件')
        path = self.root / test_id / name
        if not path.is_file():
            raise ValueError('记录文件不存在')
        return path

    def start(self, data):
        with self.control:
            if self.thread and self.thread.is_alive():
                raise ValueError('已有专项测试正在采集，请先结束当前采集')
            config = self.config(data)
            source = self.source()
            self._check(config, source)
            packet = self.frames(0) if config['kind'] != 'temperature' else None
            sensor = source['sensor'].get('snapshot') or {}
            self.cancel = threading.Event()
            now = time.time()
            report = {'id': uuid.uuid4().hex, 'kind': config['kind'], 'name': config['name'],
                      'config': config, 'state': 'settling' if config['settleS'] else 'running',
                      'startedAt': now, 'elapsedS': 0, 'reason': '', 'valid': False,
                      'sensorSamples': 0, 'temperatureSamples': 0,
                      'stats': {}, 'source': {'sensor': source['sensor'].get('config'),
                      'motorVersion': (source['motor'].get('snapshot') or {}).get('version'),
                      'motorAtStart': source['motor'].get('snapshot'),
                      'targetIqAtStart': source['motor'].get('run', {}).get('targetA'),
                      'brakeSettingsAtStart': (source['brake'].get('snapshot') or {}).get('settings'),
                      'motorConnected': self._fresh(source['motor'], 1.5),
                      'brakeConnected': self._fresh(source['brake'], 3),
                      'brake': source['brake'].get('config'),
                      'brakeOffConfirmation': ('device' if self._fresh(source['brake'], 3) else 'manual') if config['kind'] == 'baseline' else 'not-required'},
                      'notes': ['测量任务不控制电机或电源输出。', '仅统计，不自动给出合格结论。',
                                '时间戳为 PC 接收时间，1000 帧/秒不代表 1 kHz 测量带宽。',
                                'NTC 按电机状态快照更新计数，不将缓存温度当作千次每秒独立测量。']}
            directory = self.root / report['id']
            directory.mkdir(parents=True, exist_ok=False)
            self._save(directory / 'report.json', report)
            with self.lock:
                self.live = report
            cursor = packet['frames'][-1][0] if packet and packet.get('frames') else 0
            self.thread = threading.Thread(target=self._run, args=(report, directory, cursor, sensor.get('receivedAt')), daemon=True)
            self.thread.start()
            return self.status()

    def stop(self, test_id):
        with self.control:
            if self.live and self.live['id'] != test_id:
                raise ValueError('测试记录已变化，请刷新后重试')
            self.cancel.set()
            if self.thread:
                self.thread.join(3)
                if self.thread.is_alive():
                    raise ValueError('采集尚未结束，请稍后重试')
            return self.status()

    def _run(self, initial, directory, cursor, last_sensor):
        config = initial['config']
        stats = {key: Stats() for key in ('torque', 'speed', 'powerW', 'actualIq', 'targetIq', 'temperature')}
        report = json.loads(json.dumps(initial))
        begin = time.monotonic()
        last_temp = last_current = None
        last_flush = 0
        raw_count = temp_count = 0
        preview = {key: [] for key in ('torque', 'speed', 'temperature')}
        latest_points = {}
        initial_target = initial_brake = None
        terminal, reason = 'completed', '已达到设定采集时长'
        def publish(elapsed, save=False):
            for key, point in latest_points.items():
                series = preview[key]
                if not series or point[0] > series[-1][0]:
                    if len(series) >= 300:
                        series[:] = series[::2]
                    series.append(point)
            report['preview'] = preview
            report.update(elapsedS=elapsed, sensorSamples=raw_count, temperatureSamples=temp_count,
                          stats={key: stat.result(config['torqueMeanFloorNm'] if key == 'torque' else config['speedMeanFloorRpm'] if key == 'speed' else 0) for key, stat in stats.items()})
            temp = report['stats']['temperature']
            if temp.get('count'):
                report['temperatureChangeC'] = temp['last'] - temp['first']
                report['endRiseAboveAmbientC'] = temp['last'] - config['ambientC'] if config['ambientC'] is not None else None
            with self.lock:
                self.live = json.loads(json.dumps(report))
            if save:
                self._save(directory / 'report.json', report)
        try:
            with (directory / 'data.csv').open('w', newline='', encoding='utf-8-sig') as file:
                writer = csv.writer(file)
                writer.writerow(['sample_type', 'elapsed_s', 'pc_received_unix_s', 'sensor_sequence', 'included_in_statistics',
                                 'torque_Nm', 'speed_rpm', 'shaft_power_W', 'target_iq_A', 'actual_iq_A', 'actual_id_A',
                                 'motor_snapshot_unix_s', 'motor_ntc_C'])
                while True:
                    elapsed = time.monotonic() - begin
                    if self.cancel.is_set():
                        terminal, reason = 'stopped', '操作者结束采集；设备输出由原控制面板管理'
                        break
                    if elapsed >= config['durationS'] + config['settleS']:
                        break
                    source = self.source()
                    self._check(config, source)
                    motor = source['motor']
                    snapshot = motor.get('snapshot') or {}
                    ntc = snapshot.get('ntc') or {}
                    motor_fresh = self._fresh(motor, 1.5)
                    feedback = snapshot.get('feedback') or {}
                    target = motor.get('run', {}).get('targetA') if motor_fresh else None
                    iq = feedback.get('iq_a') if motor_fresh and not feedback.get('stale') else None
                    id_a = feedback.get('id_a') if motor_fresh and not feedback.get('stale') else None
                    measured = elapsed >= config['settleS']
                    report['state'] = 'running' if measured else 'settling'
                    if config['kind'] == 'ripple' and measured:
                        brake = (source['brake'].get('snapshot') or {}).get('settings')
                        if initial_target is None and finite(target):
                            initial_target = target
                        if finite(target) and initial_target is not None and target != initial_target:
                            raise ValueError('采集期间目标 Iq 改变，稳态波动记录中断')
                        if initial_brake is None and brake:
                            initial_brake = list(brake)
                        if brake and initial_brake is not None and brake != initial_brake:
                            raise ValueError('采集期间制动电源设定改变，稳态波动记录中断')
                    if config['kind'] == 'ripple' and measured and initial['source']['motorConnected'] and not motor_fresh:
                        raise ValueError('电机反馈中断，无法确认稳态工况')
                    if config['kind'] == 'ripple' and measured and initial['source']['brakeConnected'] and not self._fresh(source['brake'], 3):
                        raise ValueError('制动电源反馈中断，无法确认稳态工况')
                    if config['kind'] not in ('temperature', 'loaded_peak') and measured and motor_fresh and (snapshot.get('motor') or {}).get('fault'):
                        raise ValueError('电机保护触发，记录中断并保留已有数据')
                    if motor_fresh and ntc.get('valid') and not ntc.get('fault') and finite(ntc.get('age_s')) and 0 <= ntc['age_s'] + max(0, time.time()-snapshot.get('receivedAt', 0)) <= 1.5 and finite(ntc.get('temp_c')) and snapshot.get('receivedAt') != last_temp:
                        last_temp = snapshot['receivedAt']
                        temp_count += 1
                        writer.writerow(['temperature', elapsed, last_temp, '', measured, '', '', '', target, iq, id_a, last_temp, ntc['temp_c']])
                        if measured:
                            stats['temperature'].add(ntc['temp_c'])
                            latest_points['temperature'] = [elapsed, ntc['temp_c']]
                    if measured and motor_fresh and snapshot.get('receivedAt') != last_current:
                        last_current = snapshot['receivedAt']
                        stats['actualIq'].add(iq)
                        stats['targetIq'].add(target)
                    if config['kind'] != 'temperature':
                        packet = self.frames(cursor)
                        if packet is not None:
                            if packet.get('reset') or packet.get('dropped'):
                                raise ValueError('传感器帧序号重置或缓存溢出，记录中断')
                            sensor_config = packet.get('config') or {}
                            if sensor_config != (initial['source']['sensor'] or {}):
                                raise ValueError('传感器通信配置改变，记录中断')
                            rows = packet.get('frames') or []
                            for seq, wall, raw_torque, raw_speed in rows:
                                if cursor and seq != cursor + 1:
                                    raise ValueError('传感器帧序号不连续，记录中断')
                                cursor = seq
                                last_sensor = wall
                                if wall < initial['startedAt'] or wall > initial['startedAt'] + config['settleS'] + config['durationS']:
                                    continue
                                torque = raw_torque / 10 ** sensor_config.get('torqueDecimals', 2)
                                speed = raw_speed / 10 ** sensor_config.get('speedDecimals', 1)
                                used = wall >= initial['startedAt'] + config['settleS']
                                power = abs(torque * speed) / 9550 * 1000
                                raw_count += 1
                                writer.writerow(['sensor', wall - initial['startedAt'], wall, seq, used, torque, speed, power, target, iq, id_a, snapshot.get('receivedAt') if motor_fresh else '', ''])
                                if used:
                                    stats['torque'].add(torque); stats['speed'].add(speed); stats['powerW'].add(power)
                                    latest_points['torque'] = [wall-initial['startedAt'], torque]
                                    latest_points['speed'] = [wall-initial['startedAt'], speed]
                        else:
                            sensor = source['sensor']['snapshot']
                            wall = sensor['receivedAt']
                            if wall != last_sensor:
                                last_sensor = wall
                                torque, speed = sensor['torque'], sensor['speed']
                                power = abs(torque * speed) / 9550 * 1000
                                raw_count += 1
                                writer.writerow(['sensor', wall-initial['startedAt'], wall, '', measured, torque, speed, power, target, iq, id_a, snapshot.get('receivedAt') if motor_fresh else '', ''])
                                if measured:
                                    stats['torque'].add(torque); stats['speed'].add(speed); stats['powerW'].add(power)
                                    latest_points['torque'] = [wall-initial['startedAt'], torque]
                                    latest_points['speed'] = [wall-initial['startedAt'], speed]
                        if last_sensor is None or time.time() - last_sensor > 1.5:
                            raise ValueError('超过 1.5 秒没有新增传感器数据')
                    if elapsed - last_flush >= .5:
                        publish(elapsed, save=True)
                        file.flush()
                        last_flush = elapsed
                    self.cancel.wait(.02)
        except Exception as error:
            terminal, reason = 'interrupted', str(error)
        finally:
            report.update(state=terminal, reason=reason, finishedAt=time.time())
            needed = 'temperature' if config['kind'] == 'temperature' else 'torque'
            enough = stats[needed].n >= (2 if needed == 'temperature' else 10)
            rotating = stats['speed'].maximum is not None and stats['speed'].maximum > config['speedMeanFloorRpm']
            report['valid'] = terminal == 'completed' and enough and (config['kind'] != 'ripple' or rotating)
            if terminal == 'completed' and not report['valid']:
                report['reason'] = '采集结束，但有效测量点不足或未观察到有效转动，不能评定该项目'
            try:
                publish(time.monotonic() - begin, save=True)
            except OSError as error:
                report.update(state='interrupted', valid=False, reason=f'保存报告失败：{error}')
                with self.lock:
                    self.live = report
