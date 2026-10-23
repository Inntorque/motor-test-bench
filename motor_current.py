"""Current-mode-only adapter to the existing xige_foc CAN owner on localhost.

No calibration, firmware, parameter saving, speed or position control routes.
Full-current mode alone sets volatile ID7 to the verified firmware ceiling.
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
FULL_IQ_TARGET_A = 10.0  # Test setpoint; independent of the 30 A phase protection.


class CurrentError(ValueError):
    pass


class CurrentMotor:
    def __init__(self):
        self.lock = threading.RLock()
        self.io_lock = threading.RLock()
        self.action_lock = threading.Lock()
        self.stop_lock = threading.RLock()
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
        self.start_guard = None
        self.feedback_after = None
        self.output_hold = None
        self.maximum_snapshot = None
        self.maximum_refreshed = 0
        self.maximum_window = None
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
        raw_received_at = time.time()
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
            raw_received_at = time.time()
        control = self._request('/api/control/status')
        # The control read explicitly refreshes MOTOR_STAT. Prefer that result
        # over the earlier /api/status cache; old services retain the fallback.
        motor = control.get('motor') if 'motor' in control else raw.get('motor')
        motor = motor or {}
        if not motor.get('available') or motor.get('age_s', 99) > 1.5:
            raise CurrentError('电机状态反馈超时')
        if self.feedback_after and motor.get('updated_at',0)<=self.feedback_after():
            raise CurrentError('重新上电后尚未收到新的电机反馈，请稍候重试')
        feedback = control.get('feedback') or {}
        if motor.get('mode')==7 and time.monotonic()-self.maximum_refreshed>=1:
            self.maximum_refreshed=time.monotonic()
            try:
                self.maximum_snapshot=self._request('/api/max-output/status?cached=1').get('maximumOutput')
                key=((self.maximum_snapshot or {}).get('mcuTimeUs'),(self.maximum_snapshot or {}).get('windowId'))
                if self.maximum_snapshot and key!=self.maximum_window:
                    self.maximum_window=key
                    self._diagnostic('maximum_output_window',self.maximum_snapshot)
            except (CurrentError,KeyError) as error:
                self._diagnostic('maximum_output_diagnostic_error',{'error':str(error)})

        telem = raw.get('telem') or {}
        phase_age = telem.get('age_s')
        phase_at = raw_received_at - phase_age if isinstance(phase_age, (int, float)) and math.isfinite(phase_age) and phase_age >= 0 else None
        with self.lock:
            if self.feedback_after and motor.get('updated_at',0)<=self.feedback_after():
                raise CurrentError('重新上电后尚未收到新的电机反馈，请稍候重试')
            self.snapshot = {
                'version': raw.get('version'), 'versionRaw': raw.get('version_raw'),
                'adapter': raw.get('adapter'), 'node': raw.get('node'),
                'motorProfile': (raw.get('motor_profile') or {}).get('data'),
                'motor': {key: motor.get(key) for key in ('available', 'mode', 'state', 'state_name', 'output_on', 'fault', 'fault_reason', 'fault_peak_ma', 'updated_at', 'age_s')},
                'encoder': {key: (raw.get('encoder') or {}).get(key) for key in ('valid', 'stale', 'electrical_calibrated', 'electrical_angle_valid')},
                'ntc': {key: (raw.get('ntc') or {}).get(key) for key in ('temp_c', 'limit_c', 'valid', 'over', 'fault', 'tripped', 'tripped_latched', 'age_s')},
                'feedback': {key: feedback.get(key) for key in ('id_a', 'iq_a', 'stale', 'updated_at', 'speed_rpm', 'position_deg', 'position_stale', 'position_at')},
                'phaseCurrent': {**{key: telem.get(key) for key in ('ia_mA', 'ib_mA', 'ic_mA', 'stale')}, 'updated_at': phase_at},
                'control': {key: (control.get('control') or {}).get(key) for key in ('mode', 'state', 'iq_target_ma', 'updated_at')}, 'receivedAt': time.time(),
                'maximumOutput': dict(self.maximum_snapshot) if self.maximum_snapshot else None,
                'limitA': (self.snapshot or {}).get('limitA'),
                'supportedMaxA': (self.snapshot or {}).get('supportedMaxA'),
                'fullTargetA': (self.snapshot or {}).get('fullTargetA'),
            }
            try:
                self._healthy(self.snapshot, starting=True)
                self.snapshot['startBlockReason'] = ''
            except CurrentError as error:
                self.snapshot['startBlockReason'] = str(error)
            self.refreshed = time.monotonic()
            self.connected = True
            if (not self.run.get('running') and motor.get('state')==0 and motor.get('output_on') is False
                    and motor.get('updated_at',0)>self.run.get('stopRequestedAt',float('inf'))):
                self.run['stopConfirmed']=True
        return self.snapshot

    @staticmethod
    def _healthy(snapshot, *, starting=False):
        motor = snapshot.get('motor') or {}
        encoder = snapshot.get('encoder') or {}
        ntc = snapshot.get('ntc') or {}
        feedback = snapshot.get('feedback') or {}
        if ntc.get('fault'):
            raise CurrentError('NTC 传感器故障，温度反馈不可用')
        # Firmware bit 1 means restart-inhibited (trip limit minus hysteresis).
        # Running protection uses the reported trip temperature below.
        if starting and ntc.get('over'):
            raise CurrentError('NTC 温度尚未降至允许重新启动的范围')
        if ntc.get('tripped') or ntc.get('tripped_latched'):
            raise CurrentError('NTC 历史过温锁存尚未复归')
        if motor.get('fault') or motor.get('state') == 3:
            code = motor.get('fault_reason')
            if code is None:
                raise CurrentError('电机保护已触发（设备未上报具体原因）')
            names = {1: 'ADC 采样陈旧', 2: '通用过流', 3: 'NTC 保护（过温或读数无效）',
                     4: '闭环过流', 5: '硬过流', 6: '编码器失效',
                     7: '校准过流', 10: '控制计算超时', 11: '相电流采样超量程（ADC 贴轨）'}
            reason = names.get(code, '未识别保护原因')
            peak = motor.get('fault_peak_ma')
            detail = f'，锁存相电流峰值 {peak / 1000:.3f} A' if code != 11 and isinstance(peak, (int, float)) else ''
            raise CurrentError(f'电机保护已触发：{reason}（代码 {code}{detail}）')
        if not encoder.get('valid') or encoder.get('stale'):
            raise CurrentError('编码器反馈无效或超时')
        if not encoder.get('electrical_calibrated') or not encoder.get('electrical_angle_valid'):
            raise CurrentError('电机电角校准无效，请在原西格工程完成校准')
        if not ntc.get('valid') or ntc.get('age_s', 99) > 1.5:
            raise CurrentError('温度反馈无效')
        temperature,temperature_limit=ntc.get('temp_c'),ntc.get('limit_c')
        if not isinstance(temperature,(int,float)) or not math.isfinite(temperature):raise CurrentError('温度读数无效')
        if isinstance(temperature_limit,(int,float)) and temperature>=temperature_limit:raise CurrentError('达到设备 NTC 温度保护边界')
        if feedback.get('stale') or any(not isinstance(feedback.get(key), (int, float))
                                       or not math.isfinite(feedback[key])
                                       for key in ('id_a', 'iq_a')):
            raise CurrentError('电流 Id/Iq 反馈缺失或超时')
        limit=snapshot.get('limitA')
        if not starting and limit and max(abs(feedback['id_a']),abs(feedback['iq_a']))>limit*1.1:
            raise CurrentError('实际电流超过设备电流保护限值')
        if starting and (motor.get('state') != 0 or motor.get('output_on') is not False):
            raise CurrentError('电机未处于 IDLE/输出关闭，先停止电机')

    def _limit(self):
        param = self._request('/api/get?id=7')
        value = param.get('value')
        version = (self.snapshot or {}).get('versionRaw')
        if not isinstance(version, int) or version < 0x0A00:
            raise CurrentError('未确认西格电流环固件版本')
        # 0x0A31 allows ID7 and MODE 5 up to 30 A; older firmware keeps its ceiling.
        maximum = 1.0 if version <= 0x0A10 else 5.0 if version < 0x0A2C else 10.0 if version < 0x0A31 else 30.0
        if not isinstance(value, int) or isinstance(value, bool) or not 100 <= value <= maximum * 1000:
            raise CurrentError('设备 ID7 电流保护限值无效')
        limit = value / 1000
        with self.lock:
            self.snapshot['limitA'] = limit
            self.snapshot['supportedMaxA'] = maximum
            self.snapshot['fullTargetA'] = min(FULL_IQ_TARGET_A, maximum)
        return limit

    def status(self):
        with self.lock:
            connected = self.connected and time.monotonic() - self.refreshed <= 2
            return {'connected': bool(connected), 'snapshot': self.snapshot if connected else None,
                    'error': self.error, 'run': dict(self.run)}

    def powered_on(self):
        with self.lock:
            if not self.run.get('running'):
                self.connected=False;self.snapshot=None;self.refreshed=0
                self.reconnect_enabled=True;self.retry_after=0;self.session=None

    def read(self, *, heartbeat=True):
        if heartbeat:self.lease = time.monotonic()
        if not self.run['running'] and (self.connected or (self.reconnect_enabled and time.monotonic() >= self.retry_after)):
            try:
                self._refresh()
                if (self.snapshot or {}).get('limitA') is None:self._limit()
                self.error = ''
            except Exception as error:
                with self.lock:
                    self.connected = False
                    self.error = str(error)
                    self.retry_after = time.monotonic() + 1
        return self.status()

    def connect(self, *, cancel=None):
        with self.action_lock:
            if cancel and cancel.is_set():raise CurrentError('连接已取消')
            if self.run['running']:
                raise CurrentError('电流步进正在运行')
            self.reconnect_enabled = False
            self.session = None
            try:
                raw = self._request('/api/status')
                if Path(raw.get('project_root', '')).resolve() != XIGE_ROOT.resolve():
                    raise CurrentError('8892 端口不是指定的 xige_foc 工程')
                if not raw.get('connected'):
                    if (raw.get('ota') or {}).get('running'):
                        raise CurrentError('电机正在升级，无法重新连接')
                    if not (raw.get('adapter') or {}).get('open'):
                        self.session = None
                        self._request('/api/reconnect', write=True, allow_cancel=True)
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if cancel and cancel.is_set():raise CurrentError('连接已取消')
                        raw = self._request('/api/status')
                        if raw.get('connected') and (raw.get('motor') or {}).get('available'):
                            break
                        time.sleep(.2)
                    if not raw.get('connected') and (raw.get('adapter') or {}).get('open'):
                        raise CurrentError(f"USB-CAN 已打开，但节点 {raw.get('node', 0)} 无有效心跳；请检查驱动板供电、CAN 接线与节点地址")
                self._refresh()
                for pid in (0, 1, 2):
                    if cancel and cancel.is_set():raise CurrentError('连接已取消')
                    self._request(f'/api/get?id={pid}')
                self._refresh()
                self._limit()
                if cancel and cancel.is_set():raise CurrentError('连接已取消')
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
        if data.get('mode') == 'maximum_output':
            CurrentMotor.maximum_config(data)
            return 0, 1, 0
        if data.get('mode') == 'full_current':
            # Send the capped target once; the firmware retains its 100 A/s slew.
            return FULL_IQ_TARGET_A, 1, 0
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
        if 'maxTargetA' in data:
            try:maximum=float(data['maxTargetA'])
            except (TypeError,ValueError):raise CurrentError('本次目标电流上限无效') from None
            if not math.isfinite(maximum) or not step<=maximum<=5 or abs(maximum*1000-round(maximum*1000))>1e-6:
                raise CurrentError('本次目标电流上限需不小于步长、不超过 5 A，分辨率 0.001 A')
        return step, interval, duration

    @staticmethod
    def maximum_config(data):
        # Runtime only, reset explicitly on each maximum-output start.
        specs={'max_bus_a':('busLimitA',5,.1,20), 'max_bus_v':('busVoltageV',24,6,26.4),
               'max_phase_a':('maxPhaseA',0,0,30), 'max_id_min_a':('negativeIdMinA',0,-30,0),
               'max_speed_rpm':('maxSpeedRpm',0,0,20000)}
        values={}
        for field,(key,default,lo,hi) in specs.items():
            try:value=float(data.get(key,default))
            except (TypeError,ValueError):raise CurrentError(f'{key} 无效') from None
            if not math.isfinite(value) or not lo<=value<=hi:raise CurrentError(f'{key} 超出范围')
            values[field]=value
        return values

    def _prepare(self, data):
        step, interval, duration = self.validate(data)
        if self.run['running'] or (self.thread and self.thread.is_alive()):
            raise CurrentError('已有步进任务，不能重复启动')
        snapshot = self._refresh()
        self._healthy(snapshot, starting=True)
        limit = self._limit()
        if data.get('mode') == 'maximum_output':
            if (snapshot.get('versionRaw') or 0)<0x0A3C:raise CurrentError('最大输出需要先升级至 0x0A3C 固件')
            return step, interval, duration, self.snapshot['supportedMaxA']
        if data.get('mode') == 'full_current':
            return step, interval, duration, self.snapshot['fullTargetA']
        if 'maxTargetA' in data:
            maximum=float(data['maxTargetA'])
            if maximum>limit:raise CurrentError('本次目标电流上限超过设备保护限值')
            limit=maximum
        if step > limit:
            raise CurrentError(f'步长超过设备当前电流上限 {limit:g} A')
        return step, interval, duration, limit

    def prepare(self, data):
        with self.action_lock:
            return self._prepare(data)

    def start(self, data, *, synchronization=None):
        with self.action_lock:
            # A concurrent stop during preflight must cancel this start, rather
            # than being erased by allocating a new Event after the checks.
            if self.run['running'] or (self.thread and self.thread.is_alive()):raise CurrentError('已有步进任务')
            self.cancel = threading.Event()
            if self.start_guard:self.start_guard()
            step, interval, duration, limit = self._prepare(data)
            if self.start_guard:self.start_guard()
            if self.cancel.is_set() or (synchronization and synchronization.cancel.is_set()):raise CurrentError('启动已取消')
            self.lease = time.monotonic()
            self.error = ''
            with self.lock:
                self.run = {'phase': 'starting', 'running': True, 'reason': '', 'targetA': 0,
                            'elapsed': 0, 'steps': 0, 'stopConfirmed': None,
                            'stepA': step, 'intervalS': interval, 'durationS': duration,
                            'limitA': limit, 'deviceLimitA':self.snapshot.get('limitA'), 'limitReached': False, 'peakIqA': 0}
                self.run['mode'] = data.get('mode', 'step')
                if self.run['mode']=='maximum_output':
                    self.run['maximumConfig']=self.maximum_config(data)
                    self.maximum_snapshot=None;self.maximum_refreshed=0;self.maximum_window=None
                if synchronization:
                    self.run['coupledId'] = synchronization.id
            self.thread = threading.Thread(target=self._run, args=(step, interval, duration, limit, synchronization), daemon=True)
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
        # Multiple stop sources share one fresh-confirmation sequence; ordinary
        # CAN traffic still cannot take this lock ahead of a stop.
        with self.stop_lock:return self._stop_output_once(on_command=on_command)

    def _stop_output_once(self, *, on_command=None):
        threshold = time.time()
        with self.lock:
            self.run['stopConfirmed'] = False
            self.run['stopRequestedAt'] = threshold
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

    def _run(self, step, interval, duration, limit, synchronization=None):
        reason = '持续时间到期'
        began = None
        try:
            if synchronization and synchronization.cancel.is_set():return
            unlimited = duration == 0
            maximum = self.run.get('mode')=='maximum_output'
            control_mode = 7 if maximum else 5
            if unlimited:
                # Only this explicitly requested mode raises the volatile ID7.
                # Never save it to flash; ACK alone cannot authorize enabling.
                protection = self.snapshot['supportedMaxA']
                if self.snapshot['limitA'] != protection:
                    self._request(f'/api/set?id=7&value={round(protection*1000)}', write=True)
                confirmed = self._limit()
                if confirmed != protection:
                    raise CurrentError('相电流保护限值读回不一致，禁止开启电机')
                with self.lock:self.run['deviceLimitA'] = confirmed
            self._request('/api/telem?enable=1&period_ms=20', write=True)
            self._set_current(0)
            if maximum:
                for field,value in self.run['maximumConfig'].items():
                    self._request('/api/control/set?'+urlencode({'field':field,'value':value}),write=True)
            if synchronization and synchronization.cancel.is_set():return
            # seconds=0 is the supported no-duration protocol. The owner lease,
            # reference-service watchdog and firmware host heartbeat still stop
            # on lost control; no periodic ENABLE/restart is sent.
            watchdog = 0 if unlimited else math.ceil(duration + (15 if synchronization else 0))
            with self.lock:self.run['deviceWatchdogS'] = watchdog
            self._request(f'/api/motor?enable=1&mode={control_mode}&seconds={watchdog}', write=True)
            if synchronization or unlimited:
                deadline = time.monotonic() + 2
                while True:
                    if self.cancel.is_set() or (synchronization and synchronization.cancel.is_set()):return
                    snapshot = self._refresh()
                    self._healthy(snapshot)
                    state = snapshot['motor']
                    if state.get('state') == 2 and state.get('output_on') and state.get('mode') == control_mode:break
                    if state.get('state') != 1 or time.monotonic() >= deadline:
                        raise CurrentError('电流环未在预充期限内准备就绪，联动取消')
                    self.cancel.wait(.02)
            began = synchronization.ready('motor') if synchronization else time.monotonic()
            next_step = began if unlimited else began + interval
            with self.lock:
                self.run['phase'] = 'running'
                if maximum:self.run.update(maximumRequested=True,maximumRequestedAt=time.monotonic())
                if synchronization:
                    self.run['startedAt'] = synchronization.started_at
            while not self.cancel.is_set():
                now = time.monotonic()
                elapsed = now - began
                with self.lock:
                    self.run['elapsed'] = round(elapsed, 3)
                if duration and elapsed >= duration:
                    break
                if now - self.lease > 2:
                    raise CurrentError('工作台页面心跳超时，停止电流输出')
                snapshot = self._refresh()
                with self.lock:
                    iq = (snapshot.get('feedback') or {}).get('iq_a')
                    if isinstance(iq, (int, float)):
                        self.run['peakIqA'] = max(self.run['peakIqA'], abs(iq))
                self._healthy(snapshot)
                if snapshot['motor'].get('state') not in (1, 2) or not snapshot['motor'].get('output_on') or snapshot['motor'].get('mode') != control_mode:
                    raise CurrentError('电机输出已停止或模式变化，步进终止')
                now = time.monotonic()
                if snapshot['motor'].get('state') == 1:
                    if elapsed > 1:
                        raise CurrentError('电流环预充状态超时')
                    self.cancel.wait(0.05)
                    continue
                if self.output_hold and self.output_hold():
                    self.cancel.wait(.05)
                    continue
                if maximum:
                    with self.lock:
                        self.run['targetA']=(snapshot.get('maximumOutput') or {}).get('iqReferenceA',0)
                    self.cancel.wait(.05)
                    continue
                if now >= next_step and self.run['targetA'] < limit:
                    if not unlimited and now - next_step > interval:
                        raise CurrentError('通信耗时超过步进周期，请增大步进间隔')
                    if (duration and now - began >= duration) or self.cancel.is_set() or (synchronization and synchronization.cancel.is_set()):
                        break
                    target = min(limit, round((self.run['steps'] + 1) * step, 3))
                    self._set_current(target)
                    with self.lock:
                        self.run['steps'] += 1
                        self.run['limitReached'] = target >= limit
                        if target >= limit:self.run['limitReachedAt'] = time.monotonic()
                    # A slow transaction must not compress the configured hold
                    # or cause catch-up bursts in automatic full-Iq preparation.
                    next_step = time.monotonic() + interval if unlimited else next_step + interval
                self.cancel.wait(0.05)
            if self.cancel.is_set():
                reason = '用户停止'
        except Exception as error:
            reason = str(error)
            with self.lock:
                self.error = reason
        finally:
            if synchronization:
                synchronization.fail('motor', reason)
            self.cancel.set()
            with self.lock:
                self.run['endFeedback'] = dict((self.snapshot or {}).get('feedback') or {})
                self.run['protectionSeen'] = bool(((self.snapshot or {}).get('motor') or {}).get('fault'))
                self.run['endProtection'] = {key:dict((self.snapshot or {}).get(key) or {}) for key in ('motor','ntc')}
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
