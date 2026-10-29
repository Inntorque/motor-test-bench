"""Motor test strategies over the native MBP device interface."""
import math
import threading
import time
from protocol import generated as p
from workbench.devices.motor import MotorDevice

FULL_IQ_TARGET_A=10.0

class CurrentError(ValueError):
    pass

class CurrentMotor:
    def __init__(self,device=None):
        self.lock=threading.RLock();self.action_lock=threading.Lock();self.stop_lock=threading.RLock()
        self.connected=False;self.snapshot=None;self.error='';self.refreshed=0
        self.cancel=threading.Event();self.thread=None
        self.diagnostic_observer=None;self.start_guard=None
        self.subjects=None
        self.feedback_after=None;self.output_hold=None
        self.device=device or MotorDevice(observe=self._diagnostic)
        self.run={'phase':'idle','running':False,'reason':'','targetA':0,'elapsed':0,'steps':0,'stopConfirmed':None}
        self.prepared_id=None

    def _diagnostic(self,kind,payload):
        if self.diagnostic_observer:self.diagnostic_observer(kind,payload)

    def _refresh(self):
        try:snapshot=self.device.latest_snapshot()
        except (ValueError,OSError) as error:raise CurrentError(str(error)) from None
        if self.feedback_after and snapshot['motor']['updated_at']<=self.feedback_after():
            raise CurrentError('重新上电后尚未收到新的电机反馈')
        with self.lock:
            self.snapshot=snapshot;self.refreshed=time.monotonic();self.connected=True
            try:self._healthy(snapshot,starting=True);self.snapshot['startBlockReason']=''
            except CurrentError as error:self.snapshot['startBlockReason']=str(error)
        return snapshot

    @staticmethod
    def _healthy(snapshot,*,starting=False):
        motor=snapshot.get('motor') or {};ready=snapshot.get('readiness') or {}
        if motor.get('fault') or motor.get('state')==4:
            raise CurrentError(f"驱动板保护：{ready.get('detail') or 'fault'}（分类 {ready.get('faultCategory')}，厂家代码 {ready.get('vendorCode')}）")
        fields=snapshot.get('capabilities',{}).get('fields',0)
        ntc=snapshot.get('ntc') or {}
        if fields&(1<<10):
            if ntc.get('fault') or not ntc.get('valid') or ntc.get('age_s',99)>1.5:raise CurrentError('温度反馈无效或超时')
            if ntc.get('tripped_latched'):raise CurrentError('NTC 历史过温锁存尚未复归')
            temperature,limit=ntc.get('temp_c'),ntc.get('limit_c')
            if not isinstance(temperature,(int,float)) or not math.isfinite(temperature):raise CurrentError('温度读数无效')
            if isinstance(limit,(int,float)) and temperature>=limit:raise CurrentError('达到设备 NTC 温度保护边界')
        if fields&0xc0==0xc0 and (not (snapshot.get('encoder') or {}).get('valid') or (snapshot.get('encoder') or {}).get('stale')):
            raise CurrentError('编码器反馈无效或超时')
        if starting:
            if not ready.get('flags',0)&1:raise CurrentError(ready.get('detail') or '驱动板未就绪')
            if motor.get('state')!=0 or motor.get('output_on') is not False:raise CurrentError('电机输出尚未关闭')
        else:
            feedback=snapshot.get('feedback') or {}
            if feedback.get('stale') or any(not isinstance(feedback.get(k),(int,float)) or not math.isfinite(feedback[k]) for k in (('iq_a','id_a') if motor.get('mode')==5 else ('iq_a',))):
                raise CurrentError('电流 Id/Iq 反馈缺失或超时')
            limit=snapshot.get('limitA')
            if limit and max(abs(feedback.get('id_a') or 0),abs(feedback['iq_a']))>limit*1.1:
                raise CurrentError('实际电流超过设备电流保护限值')

    def _limit(self):
        value=(self.snapshot or {}).get('limitA')
        if not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:raise CurrentError('设备未声明有效相电流保护值')
        return value

    def status(self):
        with self.lock:
            connected=False
            if self.connected:
                try:
                    self.snapshot=self.device.latest_snapshot();connected=True
                except (ValueError,OSError):pass
            result={'connected':connected,'snapshot':self.snapshot if connected else None,'error':self.error,'run':dict(self.run)}
        if self.subjects:result['testSubject']=self.subjects()
        return result

    def powered_on(self):
        if not self.run.get('running'):
            self.connected=False;self.snapshot=None;self.refreshed=0
            threading.Thread(target=self._reconnect,daemon=True,name='motor-identify').start()

    def _reconnect(self):
        try:self.connect()
        except (ValueError,OSError) as error:self.error=str(error)

    def read(self,*,heartbeat=False):
        return self.status()

    def connect(self,*,cancel=None):
        with self.action_lock:
            if self.run['running']:raise CurrentError('电流测试正在运行')
            if cancel and cancel.is_set():raise CurrentError('连接已取消')
            try:
                self.device.connect();self._refresh()
                if self.snapshot['wireProtocol'][0]==p.MAJOR:self._limit()
                if cancel and cancel.is_set():raise CurrentError('连接已取消')
            except (OSError,ValueError) as error:
                self.connected=False;self.error=str(error);raise CurrentError(str(error)) from None
            self.error='';return self.status()

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
               'max_phase_a':('maxPhaseA',0,0,2147483), 'max_id_min_a':('negativeIdMinA',0,-2147483,0),
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
        caps=snapshot.get('capabilities',{})
        if not caps.get('modes',0)&(1<<(5 if data.get('mode')=='maximum_output' else 1)):
            raise CurrentError('驱动板未声明本次控制模式')
        fields=caps.get('fields')
        required_fields=(1<<2)|((1<<4) if data.get('mode')=='maximum_output' else 0)
        if fields is not None and fields&required_fields!=required_fields:
            raise CurrentError('缺少本次测试必需的电流反馈能力')
        if data.get('mode') == 'maximum_output':
            requested=self.maximum_config(data)
            negative_min=snapshot.get('limits',{}).get(str(p.OBJ_ALLOWED_NEGATIVE_ID_MIN),0)
            if requested['max_id_min_a']*1000<negative_min:raise CurrentError('设备未声明该负 Id 范围；弱磁保持关闭')
            if requested['max_phase_a']>snapshot['supportedMaxA']:raise CurrentError('相电流请求超过设备声明的运行范围')
            if not snapshot.get('capabilities',{}).get('maximumOutput'):raise CurrentError('驱动板未声明 MAX_AVAILABLE_IQ 能力')
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
            self.error = ''
            with self.lock:
                self.run = {'phase': 'starting', 'running': True, 'reason': '', 'targetA': 0,
                            'elapsed': 0, 'steps': 0, 'stopConfirmed': None,
                            'stepA': step, 'intervalS': interval, 'durationS': duration,
                            'limitA': limit, 'deviceLimitA':self.snapshot.get('limitA'), 'limitReached': False, 'peakIqA': 0}
                self.run['mode'] = data.get('mode', 'step')
                if self.run['mode']=='maximum_output':
                    self.run['maximumConfig']=self.maximum_config(data)
                if synchronization:
                    self.run['coupledId'] = synchronization.id
            self.thread = threading.Thread(target=self._run, args=(step, interval, duration, limit, synchronization), daemon=True)
            self.thread.start()
            return self.status()

    def _set_current(self,value):
        if self.cancel.is_set():raise CurrentError('测试已取消')
        self.device.set_target(value)
        with self.lock:self.run.update(targetA=value,lastTargetA=value)

    def _stop_output(self,*,on_command=None):
        with self.stop_lock:
            with self.lock:self.run.update(stopConfirmed=False,stopRequestedAt=time.time())
            result=self.device.stop()
            if on_command:on_command('MBP/STOP',result)
            with self.lock:self.run.update(targetA=0,stopConfirmed=True,stopMethod='device-disabled')

    def _run(self, step, interval, duration, limit, synchronization=None):
        reason = '持续时间到期'
        began = None
        try:
            if synchronization and synchronization.cancel.is_set():return
            unlimited = duration == 0
            maximum = self.run.get('mode')=='maximum_output'
            control_mode=5 if maximum else 1
            config={p.OBJ_RUN_MODE:control_mode,p.OBJ_DIRECTION:1,p.OBJ_IQ_TARGET:0,
                    p.OBJ_PHASE_RUN_LIMIT:0,p.OBJ_BUS_CURRENT_BUDGET:0,p.OBJ_BUS_VOLTAGE_BASIS:0,
                    p.OBJ_NEGATIVE_ID_MIN:0,p.OBJ_SPEED_SOFT_LIMIT:0}
            if unlimited and not maximum:config[p.OBJ_PHASE_RUN_LIMIT]=round(self.snapshot['supportedMaxA']*1000)
            if maximum:
                mx=self.run['maximumConfig']
                config.update({p.OBJ_PHASE_RUN_LIMIT:round(mx['max_phase_a']*1000),
                  p.OBJ_BUS_CURRENT_BUDGET:round(mx['max_bus_a']*1000),p.OBJ_BUS_VOLTAGE_BASIS:round(mx['max_bus_v']*1000),
                  p.OBJ_NEGATIVE_ID_MIN:round(mx['max_id_min_a']*1000),p.OBJ_SPEED_SOFT_LIMIT:round(mx['max_speed_rpm']*1000)})
            if self.cancel.is_set() or (synchronization and synchronization.cancel.is_set()):return
            self.prepared_id=self.device.prepare_run(config)
            self._refresh()
            with self.lock:self.run['deviceLimitA']=self._limit()
            if self.cancel.is_set() or (synchronization and synchronization.cancel.is_set()):return
            if self.start_guard:self.start_guard()
            self.device.start(self.prepared_id)
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
                        self.run['targetA']=(snapshot.get('feedback') or {}).get('iq_reference_a')
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
            if not self.thread or not self.thread.is_alive():
                self.run.update(running=False,phase='stopped', reason='用户停止')
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
            self.snapshot = None
            self.device.close()
        return self.status()
