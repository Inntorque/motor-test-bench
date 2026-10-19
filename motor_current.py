"""Current-mode-only adapter to the existing xige_foc CAN owner on localhost.

No calibration, firmware, parameter saving, speed or position control routes.
"""
import json
import math
import os
import uuid
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

from workbench_config import xige_root

XIGE_ROOT = xige_root()
BASE = 'http://127.0.0.1:8892'


class CurrentError(ValueError):
    pass


class CurrentMotor:
    def __init__(self):
        self.lock = threading.RLock()
        self.io_lock = threading.RLock()
        self.action_lock = threading.Lock()
        self.session = None
        self.connected = False
        self.snapshot = None
        self.error = ''
        self.refreshed = 0
        self.reconnect_enabled = False
        self.retry_after = 0
        self.lease = 0
        self.cancel = threading.Event()
        self.thread = None
        self.write_observer = None
        self.diagnostic_observer = None
        self.run = {'phase': 'idle', 'running': False, 'reason': '', 'targetA': 0,
                    'elapsed': 0, 'steps': 0, 'stopConfirmed': None}

    def _diagnostic(self, kind, payload):
        if self.diagnostic_observer:
            try:self.diagnostic_observer(kind, payload)
            except Exception:pass

    def _request(self, route, *, write=False, allow_cancel=False):
        with self.io_lock:
            if write:
                if not allow_cancel and self.cancel.is_set():
                    raise CurrentError('步进任务已取消')
                if self.session is None:
                    self.session = self._request('/api/session')
                req = urllib.request.Request(BASE + route, data=b'', method='POST', headers={
                    'X-Control-Token': self.session['token'],
                    'X-Command-Epoch': str(self.session['command_epoch']),
                })
            else:
                req = urllib.request.Request(BASE + route)
            began = time.monotonic()
            command = {'commandId':uuid.uuid4().hex, 'route':route}
            if write:self._diagnostic('motor_command_request', command)
            try:
                with urllib.request.urlopen(req, timeout=1.5) as response:
                    result = json.load(response)
                    epoch = response.headers.get('X-Command-Epoch')
                    if epoch is not None and self.session is not None:
                        self.session['command_epoch'] = max(self.session['command_epoch'], int(epoch))
            except urllib.error.HTTPError as error:
                try:
                    message = json.load(error).get('error', str(error))
                except Exception:
                    message = str(error)
                self._diagnostic('motor_command_error' if write else 'motor_read_error',
                                 {**command, 'error':message, 'durationS':time.monotonic()-began})
                raise CurrentError(message) from None
            except (OSError, ValueError) as error:
                self._diagnostic('motor_command_error' if write else 'motor_read_error',
                                 {**command, 'error':str(error), 'durationS':time.monotonic()-began})
                raise CurrentError(f'西格 CAN 服务通信失败：{error}') from None
            if write:self._diagnostic('motor_command_response', {**command, 'response':result, 'durationS':time.monotonic()-began})
            elif route != '/api/session':
                self._diagnostic('motor_read_response', {**command, 'response':result, 'durationS':time.monotonic()-began})
            if result.get('ok') is False:
                raise CurrentError(result.get('error') or '设备拒绝操作')
            if write and self.write_observer:
                try:self.write_observer(route,result)
                except Exception:pass
            return result

    def _refresh(self):
        raw = self._request('/api/status')
        if Path(raw.get('project_root', '')).resolve() != XIGE_ROOT.resolve():
            raise CurrentError('8892 端口不是指定的 xige_foc 工程')
        if not raw.get('connected'):
            raise CurrentError(raw.get('reason') or 'CAN 电机未应答')
        if (raw.get('ota') or {}).get('running'):
            raise CurrentError('电机正在升级，无法接入电流控制')
        # A malformed parameter reply once cached enc_dir=17484033. Re-read
        # invalid calibration parameters once; never bypass calibration checks.
        ranges = {0: (1, 64), 1: (0, 16383), 2: (1, 2)}
        invalid = [p['id'] for p in raw.get('params', []) if p.get('id') in ranges
                   and (not isinstance(p.get('value'), int)
                        or not ranges[p['id']][0] <= p['value'] <= ranges[p['id']][1])]
        for pid in invalid:
            value = self._request(f'/api/get?id={pid}').get('value')
            self._diagnostic('calibration_parameter_recheck', {'id':pid, 'readback':value,
                             'cached':next(p for p in raw['params'] if p.get('id') == pid)})
            if not isinstance(value, int) or not ranges[pid][0] <= value <= ranges[pid][1]:
                raise CurrentError(f'编码器参数 ID{pid} 读回异常，禁止启动或继续输出')
        if invalid:
            raw = self._request('/api/status')
        control = self._request('/api/control/status')
        motor = raw.get('motor') or {}
        if not motor.get('available') or motor.get('age_s', 99) > 1.5:
            raise CurrentError('电机状态反馈超时')
        feedback = control.get('feedback') or {}
        with self.lock:
            self.snapshot = {
                'version': raw.get('version'), 'versionRaw': raw.get('version_raw'),
                'adapter': raw.get('adapter'), 'node': raw.get('node'),
                'motorProfile': (raw.get('motor_profile') or {}).get('data'),
                'motor': {key: motor.get(key) for key in ('available', 'mode', 'state', 'state_name', 'output_on', 'fault', 'updated_at', 'age_s')},
                'encoder': {key: (raw.get('encoder') or {}).get(key) for key in ('valid', 'stale', 'electrical_calibrated', 'electrical_angle_valid')},
                'ntc': {key: (raw.get('ntc') or {}).get(key) for key in ('temp_c', 'limit_c', 'valid', 'over', 'fault', 'tripped', 'tripped_latched', 'age_s')},
                'feedback': {key: feedback.get(key) for key in ('id_a', 'iq_a', 'stale', 'updated_at', 'speed_rpm', 'position_deg', 'position_stale', 'position_at')},
                'control': {key: (control.get('control') or {}).get(key) for key in ('mode', 'state', 'iq_target_ma', 'updated_at')}, 'receivedAt': time.time(),
                'limitA': (self.snapshot or {}).get('limitA'),
            }
            try:
                self._healthy(self.snapshot, starting=True)
                self.snapshot['startBlockReason'] = ''
            except CurrentError as error:
                self.snapshot['startBlockReason'] = str(error)
            self.refreshed = time.monotonic()
            self.connected = True
        return self.snapshot

    @staticmethod
    def _healthy(snapshot, *, starting=False):
        motor = snapshot.get('motor') or {}
        encoder = snapshot.get('encoder') or {}
        ntc = snapshot.get('ntc') or {}
        feedback = snapshot.get('feedback') or {}
        if motor.get('fault') or motor.get('state') == 3 or ntc.get('over') or ntc.get('fault') or ntc.get('tripped') or ntc.get('tripped_latched'):
            raise CurrentError('电机/温度保护已触发')
        if not encoder.get('valid') or encoder.get('stale'):
            raise CurrentError('编码器反馈无效或超时')
        if not encoder.get('electrical_calibrated') or not encoder.get('electrical_angle_valid'):
            raise CurrentError('电机电角校准无效，请在原西格工程完成校准')
        if not ntc.get('valid') or ntc.get('age_s', 99) > 1.5:
            raise CurrentError('温度反馈无效')
        if feedback.get('stale') or any(not isinstance(feedback.get(key), (int, float))
                                       for key in ('id_a', 'iq_a')):
            raise CurrentError('电流 Id/Iq 反馈缺失或超时')
        if starting and (motor.get('state') != 0 or motor.get('output_on') is not False):
            raise CurrentError('电机未处于 IDLE/输出关闭，先停止电机')

    def _limit(self):
        param = self._request('/api/get?id=7')
        value = param.get('value')
        if not isinstance(value, int) or not 100 <= value <= 5000:
            raise CurrentError('设备 ID7 电流保护限值无效')
        version = (self.snapshot or {}).get('versionRaw')
        if not isinstance(version, int) or version < 0x0A00:
            raise CurrentError('未确认西格电流环固件版本')
        limit = min(value / 1000, 1.0 if version <= 0x0A10 else 5.0)
        with self.lock:
            self.snapshot['limitA'] = limit
        return limit

    def status(self):
        with self.lock:
            connected = self.connected and time.monotonic() - self.refreshed <= 2
            return {'connected': bool(connected), 'snapshot': self.snapshot if connected else None,
                    'error': self.error, 'run': dict(self.run)}

    def read(self):
        self.lease = time.monotonic()
        if not self.run['running'] and (self.connected or (self.reconnect_enabled and time.monotonic() >= self.retry_after)):
            try:
                self._refresh()
                self.error = ''
            except Exception as error:
                with self.lock:
                    self.connected = False
                    self.error = str(error)
                    self.retry_after = time.monotonic() + 1
        return self.status()

    def connect(self):
        with self.action_lock:
            if self.run['running']:
                raise CurrentError('电流步进正在运行')
            self.reconnect_enabled = False
            try:
                self._refresh()
                for pid in (0, 1, 2):
                    self._request(f'/api/get?id={pid}')
                self._refresh()
                self._limit()
            except Exception as error:
                self.connected = False
                self.error = str(error)
                raise
            self.error = ''
            self.reconnect_enabled = True
            self.lease = time.monotonic()
            return self.status()

    @staticmethod
    def validate(data):
        try:
            values = [float(data[key]) for key in ('stepA', 'intervalS', 'durationS')]
        except (KeyError, TypeError, ValueError):
            raise CurrentError('请填写步长、步进间隔和持续时间') from None
        if not all(math.isfinite(value) for value in values):
            raise CurrentError('步进参数必须为有限数值')
        step, interval, duration = values
        if not 0.001 <= step <= 5 or abs(step * 1000 - round(step * 1000)) > 1e-6:
            raise CurrentError('步长需为 0.001–5 A，分辨率为 0.001 A')
        if not 0.2 <= interval <= 10:
            raise CurrentError('步进间隔需为 0.2–10 s')
        if not 1 <= duration <= 255:
            raise CurrentError('持续时间需为 1–255 秒')
        return step, interval, duration

    def start(self, data):
        step, interval, duration = self.validate(data)
        with self.action_lock:
            if self.run['running'] or (self.thread and self.thread.is_alive()):
                raise CurrentError('已有步进任务，不能重复启动')
            snapshot = self._refresh()
            self._healthy(snapshot, starting=True)
            limit = self._limit()
            if step > limit:
                raise CurrentError(f'步长超过设备当前电流上限 {limit:g} A')
            self.cancel = threading.Event()
            self.lease = time.monotonic()
            self.error = ''
            with self.lock:
                self.run = {'phase': 'starting', 'running': True, 'reason': '', 'targetA': 0,
                            'elapsed': 0, 'steps': 0, 'stopConfirmed': None,
                            'stepA': step, 'intervalS': interval, 'durationS': duration,
                            'limitA': limit, 'limitReached': False, 'peakIqA': 0}
            self.thread = threading.Thread(target=self._run, args=(step, interval, duration, limit), daemon=True)
            self.thread.start()
            return self.status()

    def _set_current(self, value):
        self._request('/api/control/set?' + urlencode({'field': 'current_a', 'value': value}), write=True)
        reply = self._request('/api/control/status')
        if not reply.get('control'):
            raise CurrentError('电流控制状态无有效应答')
        # 0xA5 reports the firmware ramp reference, not the commanded setpoint.
        # The command is confirmed by ACK; measured Id/Iq are separate feedback.
        with self.lock:
            self.run['targetA'] = value
            self.run['lastTargetA'] = value

    def _stop_output(self, *, on_command=None):
        threshold = time.time()
        with self.lock:
            self.run['stopConfirmed'] = False
        with self.io_lock:
            self.session = self._request('/api/session')
        reply=self._request('/api/motor?enable=0', write=True, allow_cancel=True)
        if on_command:on_command('/api/motor?enable=0',reply)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            # Stopping must not depend on encoder, NTC or current telemetry.
            raw = self._request('/api/status')
            motor = raw.get('motor') or {}
            if (raw.get('connected') and Path(raw.get('project_root', '')).resolve() == XIGE_ROOT.resolve()
                    and motor.get('available') and motor.get('updated_at', 0) > threshold
                    and motor.get('state') == 0 and motor.get('output_on') is False):
                route='/api/control/set?field=current_a&value=0'
                reply=self._request(route, write=True, allow_cancel=True)
                if on_command:on_command(route,reply)
                with self.lock:
                    self.run['targetA'] = 0
                    self.run['stopConfirmed'] = True
                try:
                    self._refresh()
                except CurrentError as error:
                    with self.lock:
                        self.connected = False
                        self.error = f'电机输出已关闭；反馈读取失败：{error}'
                return
            time.sleep(0.05)
        raise CurrentError('未收到新鲜的输出关闭确认，请切断电机供电')

    def _run(self, step, interval, duration, limit):
        reason = '持续时间到期'
        began = None
        try:
            self._request('/api/telem?enable=1&period_ms=20', write=True)
            self._set_current(0)
            self._request(f'/api/motor?enable=1&mode=5&seconds={math.ceil(duration)}', write=True)
            began = time.monotonic()
            next_step = began + interval
            with self.lock:
                self.run['phase'] = 'running'
            while not self.cancel.is_set():
                now = time.monotonic()
                elapsed = now - began
                with self.lock:
                    self.run['elapsed'] = round(elapsed, 3)
                if elapsed >= duration:
                    break
                if now - self.lease > 2:
                    raise CurrentError('工作台页面心跳超时，停止电流输出')
                snapshot = self._refresh()
                with self.lock:
                    iq = (snapshot.get('feedback') or {}).get('iq_a')
                    if isinstance(iq, (int, float)):
                        self.run['peakIqA'] = max(self.run['peakIqA'], abs(iq))
                self._healthy(snapshot)
                if snapshot['motor'].get('state') not in (1, 2) or not snapshot['motor'].get('output_on') or snapshot['motor'].get('mode') != 5:
                    raise CurrentError('电机输出已停止或模式变化，步进终止')
                now = time.monotonic()
                if snapshot['motor'].get('state') == 1:
                    if elapsed > 1:
                        raise CurrentError('电流环预充状态超时')
                    self.cancel.wait(0.05)
                    continue
                if now >= next_step and self.run['targetA'] < limit:
                    if now - next_step > interval:
                        raise CurrentError('通信耗时超过步进周期，请增大步进间隔')
                    if now - began >= duration or self.cancel.is_set():
                        break
                    target = min(limit, round((self.run['steps'] + 1) * step, 3))
                    self._set_current(target)
                    with self.lock:
                        self.run['steps'] += 1
                        self.run['limitReached'] = target >= limit
                    next_step += interval
                self.cancel.wait(0.05)
            if self.cancel.is_set():
                reason = '用户停止'
        except Exception as error:
            reason = str(error)
            with self.lock:
                self.error = reason
        finally:
            self.cancel.set()
            with self.lock:
                self.run['endFeedback'] = dict((self.snapshot or {}).get('feedback') or {})
                self.run['protectionSeen'] = bool(((self.snapshot or {}).get('motor') or {}).get('fault'))
            try:
                self._stop_output()
            except Exception as error:
                with self.lock:
                    self.error = f'{reason}；{error}'
                    self.run['stopConfirmed'] = False
                    self.connected = False
            with self.lock:
                self.run.update(running=False, phase='stopped', reason=reason)
                if began is not None:
                    self.run['elapsed'] = round(time.monotonic() - began, 3)

    def stop(self):
        self.cancel.set()
        try:
            self._stop_output()
        except Exception as error:
            with self.lock:
                self.error = str(error)
                self.run['stopConfirmed'] = False
            raise
        with self.lock:
            if not self.run['running']:
                self.run.update(phase='stopped', reason='用户停止')
            if self.connected:
                self.error = ''
        return self.status()

    def disconnect(self):
        if self.run['running']:
            raise CurrentError('先停止电流步进，再断开连接')
        with self.lock:
            if self.snapshot and self.snapshot['motor'].get('output_on'):
                raise CurrentError('设备仍有输出，先停止电机')
            self.connected = False
            self.reconnect_enabled = False
            self.snapshot = None
        return self.status()
