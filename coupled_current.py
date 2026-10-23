"""Coordinated motor/brake ramp using one monotonic epoch and a joint stop lifecycle."""
import copy
import math
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import uuid
from collections import deque
from brake_torque import BrakeTorque
from safety_control import SafetyControl
from power_limits import power_problem, MOTOR_SUPPLY_CURRENT_LIMIT, MOTOR_SUPPLY_PRESET_CURRENT_A


class StandstillConfirmation:
    """Confirm motion stopped using independent, advancing encoder feedback.

    A zero from the torque sensor alone never stops or unloads the bench.
    Units below are motor-rotor rpm/degrees, not gearbox output units.
    """
    DURATION_S = 1.5
    MAX_ROTOR_RPM = .1
    MAX_POSITION_SPAN_DEG = .5

    def __init__(self):
        self.samples = deque()
        self.pending = False
        self.evidence = {}

    def update(self, measured, motor_status, *, eligible, now):
        snapshot = motor_status.get('snapshot') or {}
        motor = snapshot.get('motor') or {}
        feedback = snapshot.get('feedback') or {}
        encoder = snapshot.get('encoder') or {}
        resolution = 10**-int(measured.get('speedDecimals', 1))
        sensor_zero = abs(measured['speed']) < resolution * .5
        previous = self.evidence
        self.evidence = dict(sensorSpeedRpm=measured['speed'], confirmed=False)
        if not eligible or not sensor_zero:
            self.samples.clear();self.pending = False
            return False
        rpm, position = feedback.get('speed_rpm'), feedback.get('position_deg')
        times = (motor.get('updated_at'), feedback.get('updated_at'), feedback.get('position_at'))
        fresh = all(isinstance(t, (int, float)) and math.isfinite(t) and -.25 <= now-t <= 1 for t in times)
        valid = all(isinstance(v, (int, float)) and math.isfinite(v) for v in (rpm, position))
        if (not fresh or not valid or feedback.get('stale') or feedback.get('position_stale')
                or not encoder.get('valid') or encoder.get('stale')
                or motor.get('state') != 2 or motor.get('output_on') is not True):
            self.samples.clear();self.pending = False
            self.evidence['state'] = 'encoder_unconfirmed'
            return False
        self.evidence['motorRotorRpm'] = rpm
        if abs(rpm) > self.MAX_ROTOR_RPM:
            self.samples.clear();self.pending = False
            self.evidence['state'] = 'encoder_moving'
            return False
        stamp = times[2]
        # Re-reading one cached position cannot advance the confirmation timer.
        if self.samples and stamp <= self.samples[-1][0]:
            self.evidence = {**previous, **self.evidence}
            return False
        self.samples.append((stamp, position))
        positions = [p for _, p in self.samples]
        span = max(positions)-min(positions)
        if span > self.MAX_POSITION_SPAN_DEG:
            self.samples.clear();self.samples.append((stamp, position));self.pending = False
            self.evidence.update(state='encoder_position_moving', positionSpanDeg=span)
            return False
        self.pending = True
        duration = stamp-self.samples[0][0]
        # Bound storage even if the encoder reports an unusually high rate.
        while len(self.samples) > 128:
            del self.samples[1]
        confirmed = len(self.samples) >= 3 and duration >= self.DURATION_S
        self.evidence.update(state='confirmed' if confirmed else 'confirming',
            positionSpanDeg=span, durationS=duration, samples=len(self.samples), confirmed=confirmed)
        return confirmed


class RampSynchronization:
    def __init__(self, run_id, timeout=12):
        self.id = run_id
        self.timeout = timeout
        self.cancel = threading.Event()
        self.armed = threading.Event()
        self.lock = threading.Lock()
        self.ready_at = {}
        self.began = None
        self.started_at = None
        self.reason = ''
        self.failed_at = None

    def ready(self, role):
        with self.lock:
            self.ready_at[role] = time.time()
            if len(self.ready_at) == 2:
                self.began = time.monotonic() + .1
                self.started_at = time.time() + .1
                self.armed.set()
        deadline = time.monotonic() + self.timeout
        while not self.armed.wait(.02):
            if self.cancel.is_set():raise ValueError(self.reason or '联动启动已取消')
            if time.monotonic() > deadline:raise ValueError('另一输出未准备就绪，联动启动超时')
        if self.cancel.wait(max(0, self.began-time.monotonic())):
            raise ValueError(self.reason or '联动启动已取消')
        return self.began

    def fail(self, role, reason):
        with self.lock:
            if not self.reason:
                self.failed_at = time.time()
                label = {'motor':'电机', 'brake':'制动'}.get(role, role)
                self.reason = reason if reason == '持续时间到期' else f'{label}：{reason}'
        self.cancel.set()


class CoupledCurrent:
    def __init__(self, motor, brake, sensor, motor_supply, blocked, observe, *, recovery=False, recorder=None):
        self.motor, self.brake, self.sensor, self.motor_supply = motor, brake, sensor, motor_supply
        self.blocked, self.observe = blocked, observe
        self.recovery = recovery
        self.recorder = recorder
        self.control = threading.RLock()
        self.lock = threading.RLock()
        self.thread = None
        self.sync = None
        self.participants = []
        self.lease = 0
        self.cancel = threading.Event()
        self.run = {'running': False, 'phase': 'idle', 'reason': '', 'elapsed': 0, 'stopConfirmed': None}

    def active(self):
        with self.lock:return self.run['running']

    def blocks_controls(self):
        with self.lock:return self.run['running'] or self.run.get('stopConfirmed') is False

    def cancel_requests(self, reason='用户停止'):
        self.cancel.set()
        if self.sync:
            with self.sync.lock:
                self.sync.reason=reason
                self.sync.failed_at=self.sync.failed_at or time.time()
            self.sync.cancel.set()
        for engine in (self.motor,self.brake):engine.cancel.set()

    def status(self, heartbeat=False):
        with self.lock:
            if heartbeat:self.lease = time.monotonic()
            run = copy.deepcopy(self.run)
            sync = self.sync
            if sync:
                with sync.lock:
                    run['startedAt'] = sync.started_at
                    run['outputReadyAt'] = dict(sync.ready_at)
                    if sync.began:
                        run['elapsed'] = round(max(0, time.monotonic()-sync.began), 3) if run['running'] else run['elapsed']
            return {'run': run, 'motor': self.motor.status(), 'brake': self.brake.status(),
                    'capabilities': {'torqueSweep': True, 'fullIqRamp': True, 'maximumOutput': True}, 'torqueCalibration': BrakeTorque.metadata()}

    def _event(self, kind, payload):
        try:self.observe(kind, payload)
        except Exception:pass

    def start(self, data):
        with self.control, self.lock:
            if self.blocks_controls() or (self.thread and self.thread.is_alive()):raise ValueError('联动正在运行或输出关闭未确认，请先停止并确认')
            if self.blocked():raise ValueError('已有自动试验或连接扫描，请先结束任务')
            if data.get('mode') == 'torque_sweep':return self._start_sweep(data)
            motor = data.get('motor', {})
            brake = data.get('brake', {})
            if brake.get('mode') != 'hold':raise ValueError('联动测试需要固定制动电流模式，请刷新网页')
            m = self.motor.validate(motor)
            b = self.brake.validate(brake)
            if m[2] != b[2]:raise ValueError('联动两路必须使用相同持续时间')
            if m[2] > 240:raise ValueError('联动持续时间为 1–240 s，保留设备启动保护时间')
            if m[2] <= m[1]:raise ValueError('联动持续时间必须大于电机步进间隔')
            if self.motor.status()['run']['running'] or self.brake.active():raise ValueError('请先停止单独步进，再启动联动')
            self.sync = RampSynchronization(uuid.uuid4().hex)
            self.cancel = threading.Event()
            self.participants = []
            self.lease = time.monotonic()
            self.run = {'id': self.sync.id, 'running': True, 'phase': 'checking', 'reason': '', 'elapsed': 0,
                        'stopConfirmed': None, 'motor': dict(zip(('stepA','intervalS','durationS'), m)),
                        'brake': {'mode':'hold','currentA':b[0],'durationS':b[2]}}
            if 'maxTargetA' in motor:self.run['motor']['maxTargetA']=float(motor['maxTargetA'])
            self.thread = threading.Thread(target=self._run, args=(self.sync,), daemon=True)
            self.thread.start()
            return self.status()

    def _run(self, sync):
        if self.run.get('mode') == 'torque_sweep':
            return self._run_sweep(sync)
        reason,launched,confirmations,errors = self._attempt(sync)
        # Only a failed startup, before either positive-current step, is eligible.
        recoverable=any(word in reason for word in ('没有新鲜有效的编码器读数','编码器反馈无效或超时','CAN 电机未应答','电机状态反馈超时','无有效心跳'))
        snapshot=self.motor.snapshot or {}
        protected=bool((snapshot.get('motor') or {}).get('fault') or any((snapshot.get('ntc') or {}).get(k) for k in ('over','fault','tripped','tripped_latched')))
        progressed=any(engine.run.get('coupledId')==sync.id and engine.run.get('steps') for engine in (self.motor,self.brake))
        if (self.recovery and recoverable and not protected and not self.cancel.is_set()
                and not progressed):
            try:
                with self.lock:self.run.update(phase='recovering',recoveryCount=1,recoveryReason=reason)
                self._event('coupled_recovery_start',self.status()['run'])
                self._recover()
                if self.cancel.is_set():raise ValueError('恢复已取消')
                sync=RampSynchronization(sync.id)
                self.sync=sync
                reason,launched,confirmations,errors=self._attempt(sync)
            except Exception as error:
                reason=f'启动恢复未完成：{error}'
                # A failure after re-energizing must leave neither ramp enabled.
                self.motor.cancel.set();self.brake.cancel.set()
                off_errors=[]
                try:self.motor_supply.set_output(False)
                except Exception as off_error:off_errors.append(f'电机电源：{off_error}')
                confirmations,errors=self._stop_engines([('motor',self.motor),('brake',self.brake)])
                errors.extend(off_errors)
        if launched and not confirmations.get('motor'):
            try:
                self.motor_supply.set_output(False)
                SafetyControl.wait_discharged(self.motor_supply)
                with self.lock:self.run['motorIsolated']=True
            except Exception as error:errors.append(f'电机断电兜底：{error}')
        with self.lock:
            self.run.update(running=False,phase='stopped',reason=reason,
                            elapsed=round(max(0,time.monotonic()-sync.began),3) if sync.began else 0,
                            stopConfirmed=all(confirmations.values()) if launched else None,
                            stopResults=confirmations,stopErrors=errors)
        self._event('coupled_end',self.status()['run'])

    def _start_sweep(self, data):
        try:
            config = {key:float(data['brake'][key]) for key in ('startNm','stepNm','maxNm','intervalS')}
        except (KeyError,TypeError,ValueError):raise ValueError('请填写力矩加载参数') from None
        if not all(math.isfinite(v) for v in config.values()):raise ValueError('测试参数必须为有限数值')
        if not 0<=config['startNm']<=BrakeTorque.MAX_TARGET_NM or not 0<config['maxNm']<=BrakeTorque.MAX_TARGET_NM:
            raise ValueError('起始力矩为 0–99.9 N·m，结束力矩需大于零且低于传感器量程')
        if config['maxNm']<config['startNm']:raise ValueError('起始加载力矩不能超过结束力矩')
        if not .01 <= config['stepNm'] <= 1:raise ValueError('力矩步长为 0.01–1 N·m')
        if not .5 <= config['intervalS'] <= 30:raise ValueError('每级力矩保持时间为 0.5–30 s')
        if self.motor.status()['run']['running'] or self.brake.active():raise ValueError('请先停止单独输出任务')
        motor_mode=data.get('motorMode','full_current')
        if motor_mode not in ('full_current','maximum_output'):raise ValueError('电机测试模式无效')
        maximum_options={key:data[key] for key in ('negativeIdMinA','maxPhaseA','maxSpeedRpm') if key in data}
        self.sync = RampSynchronization(uuid.uuid4().hex)
        self.cancel = threading.Event();self.load_requested = threading.Event()
        self.participants = [];self.lease = time.monotonic()
        self.run = dict(id=self.sync.id, mode='torque_sweep', running=True, phase='checking',
                        reason='', elapsed=0, stopConfirmed=None, motorMode=motor_mode, maximumOptions=maximum_options,
                        torqueConfig=config, targetNm=0., measuredNm=None, brakeCurrentA=0., loadSteps=0)
        self.thread = threading.Thread(target=self._run, args=(self.sync,), daemon=True)
        self.thread.start()
        return self.status()

    def begin_loading(self, data):
        with self.control, self.lock:
            if (not self.run['running'] or self.run.get('mode')!='torque_sweep'
                    or self.run.get('phase')!='awaiting_load' or self.cancel.is_set()
                    or self.sync.cancel.is_set() or time.monotonic()-self.lease>2.5):
                raise ValueError('需先完成 Iq 满转准备，才能开始力矩加载')
            if data.get('id')!=self.run['id']:raise ValueError('试验已变化，请刷新状态后重新操作')
            if self.load_requested.is_set():raise ValueError('力矩加载已请求，请勿重复启动')
            self.load_requested.set()
            self.run['loadRequested']=True
            self._event('torque_loading_requested', {'id':self.run['id']})
            return self.status()

    def _run_sweep(self, sync):
        """Capped 10 A target with firmware slew; brake stays OFF until requested.

        Native motor supervision/watchdog and the joint owner lease stay active
        through spin-up, the waiting period, and closed-loop torque loading.
        No power-cycle recovery or automatic restart is attempted after motion.
        """
        launched=[];reason='任务结束';samples=deque();standstill=StandstillConfirmation()
        measurement_end=None
        recording_id=None
        controller=BrakeTorque();target=0.;holding_since=None
        load_started=None;next_poll=0.;last_sensor=None;rotated=False;at_current_limit_since=None
        try:
            self._check_sensor()
            supply=self.motor_supply.read()
            power=supply.get('snapshot') or {};settings=power.get('settings');pc=supply.get('config') or {}
            if not supply.get('connected') or not power.get('status',0)&128:raise ValueError('请先开启电机电源并确认实际供电')
            issue=power_problem('motor-supply',pc,power)
            if issue:raise ValueError('电机电源：'+issue['message'])
            if abs(settings[2]/10**pc['currentDecimals']-MOTOR_SUPPLY_PRESET_CURRENT_A)>1/10**pc['currentDecimals']+1e-9:
                raise ValueError(f"电机电源预设为 {settings[2]/10**pc['currentDecimals']:g} A，请在面板设为 24 V / {MOTOR_SUPPLY_PRESET_CURRENT_A:g} A；软件不改写预设")
            config=self.run['torqueConfig']
            motor_data=dict(mode=self.run.get('motorMode','full_current'))
            if motor_data['mode']=='maximum_output':
                motor_data.update(self.run.get('maximumOptions',{}),
                    busLimitA=settings[2]/10**pc['currentDecimals'],
                    busVoltageV=settings[1]/10**pc['voltageDecimals'])
            _,_,_,limit=self.motor.prepare(motor_data)
            # Brake's native timed task is not started; this only checks its
            # connection, configuration and OFF state before joint ownership.
            brake_preflight=self.brake.prepare(dict(mode='hold',currentA=0,durationS=1))
            if brake_preflight[3]<BrakeTorque.MAX_CURRENT_A:raise ValueError('制动电源限幅低于本轮所需边界')
            if self.cancel.is_set() or sync.cancel.is_set():raise ValueError('启动已取消')
            launched=[('motor',self.motor),('brake',self.brake)]
            with self.lock:self.participants=list(launched);self.run.update(phase='ramping',motor=motor_data,motorLimitA=limit)
            self.brake.cancel=self.cancel
            with self.brake.lock:
                self.brake.run=dict(running=True,phase='zero_load',mode='torque',targetA=0.,targetNm=0.,steps=0,
                                    elapsed=0.,limitA=BrakeTorque.MAX_CURRENT_A,stopConfirmed=None,coupledId=sync.id,acquisitionManaged=True)
            # Zero *excitation*: mechanical bearing/residual torque is not tared.
            self.brake._command('set',{'voltage':24,'current':0})
            if self.cancel.is_set() or sync.cancel.is_set():raise ValueError('启动已取消')
            if motor_data['mode']=='maximum_output' and self.recorder:
                try:
                    own_recording=not self.recorder.active()
                    recording=self.recorder.start({'name':'最大输出力矩测试','range':100}) if own_recording else self.recorder.status()
                    if recording:
                        if own_recording:recording_id=recording['id']
                        with self.lock:self.run['recordingId']=recording['id']
                except Exception as error:
                    with self.lock:self.run['recordingError']=str(error)
                    self._event('recording_error',{'error':str(error)})
            self._event('coupled_start',self.status()['run'])
            self.motor.start(motor_data,synchronization=sync)
            sync.ready('brake')
            next_poll=time.monotonic()
            while not sync.cancel.is_set():
                now=time.monotonic()
                if self.cancel.is_set():raise ValueError('操作者停止')
                if now-self.lease>2.5:raise ValueError('联动页面心跳中断，停止两路输出')
                self.motor.lease=now
                measured=self._check_sensor()
                power_status=self.motor_supply.cached_status()
                power=power_status.get('snapshot') or {}
                if not power_status.get('connected') or not power.get('status',0)&128:raise ValueError('电机电源已关闭或通信中断')
                if time.time()-power.get('receivedAt',0)>2:raise ValueError('电机电源反馈过期')
                settings=power.get('settings')
                issue=power_problem('motor-supply',pc,power)
                if issue:raise ValueError('电机电源：'+issue['message'])
                if measured['receivedAt']!=last_sensor:
                    last_sensor=measured['receivedAt'];samples.append((time.monotonic(),abs(measured['torque'])))
                while samples and samples[0][0]<now-.5:samples.popleft()
                actual=sum(v for _,v in samples)/len(samples) if samples else abs(measured['torque'])
                moving=abs(measured['speed'])>10**-int(measured.get('speedDecimals',1))
                rotated=rotated or moving
                with self.motor.lock:
                    full=self.motor.run.get('maximumRequested',False) if motor_data['mode']=='maximum_output' else self.motor.run.get('limitReached',False)
                    full_at=self.motor.run.get('maximumRequestedAt') if motor_data['mode']=='maximum_output' else self.motor.run.get('limitReachedAt')
                eligible=rotated or (full and full_at is not None and now-full_at>=2)
                stopped=standstill.update(measured,self.motor.status(),eligible=eligible,now=time.time())
                with self.lock:self.run['stopConfirmation']=dict(standstill.evidence)
                if standstill.pending:
                    with self.lock:self.run.setdefault('stallDetectedAt',measured['receivedAt'])
                    if stopped:
                        # Keep the real zero-speed confirmation samples. The
                        # cutoff precedes OFF/coastdown, not the first zero.
                        measurement_end=measured['receivedAt']
                        endpoint=dict(measured)
                        endpoint.update(motorSupplyReceivedAt=power['receivedAt'],
                            motorSupplyVoltage=power['measured'][0]/10**pc['voltageDecimals'],
                            motorSupplyCurrent=power['measured'][1]/10**pc.get('measuredCurrentDecimals',pc['currentDecimals']))
                        endpoint['encoderConfirmation']=dict(standstill.evidence)
                        with self.lock:self.run['stallEndpoint']=endpoint
                        raise ValueError('编码器连续确认电机轴静止，且扭矩传感器报零；结束加载并保留实测终点')
                else:
                    with self.lock:self.run.pop('stallDetectedAt',None)
                with self.lock:
                    self.run.update(measuredNm=actual,measuredSpeedRpm=measured['speed'],elapsed=round(now-sync.began,3))
                    if full and moving and self.run['phase']=='ramping':self.run['phase']='awaiting_load'
                if self.load_requested.is_set() and load_started is None:
                    load_started=now;target=config['startNm']
                    with self.lock:
                        self.run.update(phase='loading',loadStartedAt=time.time(),curveStartedAt=max(sync.started_at,time.time()-1),
                                        targetNm=target,loadSteps=1)
                    self._event('torque_loading_start',self.status()['run'])
                # Never increase brake excitation while confirming a stopped
                # shaft. Freeze at the first stopped sample if it persists.
                if standstill.pending or getattr(self.motor_supply,'verifying_power',False):
                    self.cancel.wait(.03)
                    continue
                if now>=next_poll:
                    with self.brake.channel.lock:
                        if self.cancel.is_set() or sync.cancel.is_set():break
                        if load_started is None:
                            bs=self.brake.channel.read()['snapshot']
                            if bs['status']&128:raise ValueError('空载阶段制动输出意外开启')
                        else:
                            command=controller.update(target,actual)
                            with self.brake.lock:self.brake.run.update(targetA=command,targetNm=target,phase='torque_tracking')
                            result=self.brake._command('set',{'voltage':24,'current':command})
                            if self.cancel.is_set() or sync.cancel.is_set():break
                            if target==0:
                                if result['status']&128:raise ValueError('零负载阶段制动输出意外开启')
                            elif not result['status']&128:
                                if self.run.get('brakeEnabled'):raise ValueError('制动输出意外关闭')
                                result=self.brake._command('output',{'on':True,'voltage':24,'current':command})
                                with self.lock:self.run['brakeEnabled']=True
                            if target>0:self.brake._check_snapshot(result)
                            with self.lock:self.run['brakeCurrentA']=command
                            self._event('torque_feedback',dict(id=sync.id,targetNm=target,measuredNm=actual,currentA=command))
                            # This is an ascending load sweep, not a stability
                            # qualification. Latch the first arrival/crossing;
                            # mechanical ripple must not restart the dwell.
                            # Slow tracking stays supervised without a timer
                            # that unloads a still-moving motor after 10 s.
                            if holding_since is None and (target==0 or actual>=target-.1):
                                holding_since=time.monotonic()
                            if holding_since is not None and time.monotonic()-holding_since>=config['intervalS']:
                                if target>=config['maxNm']:reason='设定力矩阶梯完成，保留实测结果';break
                                target=round(min(config['maxNm'],target+config['stepNm']),6)
                                holding_since=None
                                with self.lock:self.run.update(targetNm=target,loadSteps=self.run['loadSteps']+1)
                                self._event('torque_target_step',self.status()['run'])
                            if command>=BrakeTorque.MAX_CURRENT_A:
                                at_current_limit_since=at_current_limit_since or time.monotonic()
                            else:at_current_limit_since=None
                            if at_current_limit_since and time.monotonic()-at_current_limit_since>=2 and actual<target-.1:
                                reason='达到制动安全电流 1.999 A 边界，目标力矩未达到；保留实测结果，未确认电机极限';break
                    # No catch-up bursts when serial transactions take longer.
                    next_poll=time.monotonic()+.5
                self.cancel.wait(.03)
            reason=sync.reason or reason
        except Exception as error:reason=sync.reason or str(error)
        finally:
            cutoff=min(t for t in (measurement_end,sync.failed_at,time.time()) if t is not None)
            with self.lock:self.run.update(measurementEndedAt=cutoff,phase='stopping')
            sync.cancel.set()
            for _,engine in launched:engine.cancel.set()
            confirmations,errors=self._stop_engines(launched)
            if launched and not confirmations.get('motor'):
                try:
                    self.motor_supply.set_output(False)
                    SafetyControl.wait_discharged(self.motor_supply)
                    with self.lock:self.run['motorIsolated']=True
                except Exception as error:errors.append(f'电机断电兜底：{error}')
            with self.lock:
                self.run.update(running=False,phase='stopped',reason=reason,
                                elapsed=round(max(0,time.monotonic()-sync.began),3) if sync.began else 0,
                                stopConfirmed=all(confirmations.values()) if launched else None,
                                stopResults=confirmations,stopErrors=errors)
            self._event('coupled_end',self.status()['run'])
            if recording_id:
                try:self.recorder.stop(recording_id)
                except Exception as error:
                    with self.lock:self.run['recordingError']=str(error)

    def _recover(self):
        if self.cancel.is_set() or time.monotonic()-self.lease>2.5:raise ValueError('操作者已离线或取消')
        try:self.brake.stop()
        finally:self.motor_supply.set_output(False)
        SafetyControl.wait_discharged(self.motor_supply,self.cancel)
        SafetyControl.wait_discharged(self.brake.channel,self.cancel)
        if self.cancel.wait(.5):raise ValueError('恢复已取消')
        options={'cancel':self.cancel} if hasattr(self.motor_supply,'cancel_pending') else {}
        self.motor_supply.set_output(True,{'voltage':24,'current':MOTOR_SUPPLY_PRESET_CURRENT_A},**options)
        if self.cancel.wait(1):raise ValueError('恢复已取消')
        powered_at=time.time()
        deadline=time.monotonic()+5
        while True:
            if self.cancel.is_set() or time.monotonic()-self.lease>2.5:raise ValueError('恢复已取消或操作者心跳中断')
            try:
                self.motor.connect(cancel=self.cancel)
                snapshot=self.motor._refresh()
                self.motor._healthy(snapshot,starting=True)
                if (snapshot.get('motor') or {}).get('updated_at',0)>powered_at:break
            except ValueError as error:
                transient=any(word in str(error) for word in ('编码器反馈无效或超时','温度反馈无效','电机状态反馈超时','电机未应答','尚未收到新的电机反馈','无有效心跳'))
                if not transient:raise
            if time.monotonic()>=deadline:raise ValueError('重新上电后未获得新鲜电机反馈')
            self.cancel.wait(.1)
        self.brake.channel.set_values({'voltage':24,'current':0})
        self._event('coupled_recovery_ready',{'id':self.sync.id})

    def _check_sensor(self):
        sensor=self.sensor.status()
        if not sensor.get('connected'):raise ValueError('扭矩传感器掉线，停止两路输出')
        snapshot=sensor.get('snapshot') or {}
        torque=snapshot.get('torque')
        # Both the brake nameplate and the sensor's 100 N·m range are confirmed.
        if not isinstance(torque,(int,float)) or not math.isfinite(torque) or abs(torque)>=100:
            raise ValueError('扭矩无效或达到 100 N·m 边界，停止两路输出')
        if time.time()-snapshot.get('receivedAt',0)>1.5:raise ValueError('扭矩传感器读数过期，停止两路输出')
        speed=snapshot.get('speed')
        if not isinstance(speed,(int,float)) or not math.isfinite(speed):raise ValueError('扭矩传感器转速无效')
        return snapshot

    def _attempt(self, sync):
        launched = []
        reason = '持续时间到期'
        moving_since = None
        standstill = StandstillConfirmation()
        rotated = False
        with self.lock:self.run.pop('measurementEndedAt',None)
        try:
            if not self.sensor.status()['connected']:raise ValueError('请先连接动态扭矩传感器并取得有效数据')
            supply = self.motor_supply.read()
            if not supply['connected'] or not supply['snapshot']['status'] & 128:
                raise ValueError('请先开启电机电源并确认实际供电')
            settings=supply['snapshot'].get('settings')
            config=supply.get('config') or {}
            if settings and (settings[1]/10**config.get('voltageDecimals',2)!=24 or settings[2]/10**config.get('currentDecimals',2)>MOTOR_SUPPLY_CURRENT_LIMIT):
                raise ValueError('电机电源设定不符合 24 V / 最大 20 A，禁止启动')
            self._check_sensor()
            # Both read-only preflights complete before either output is enabled.
            self.motor.prepare(self.run['motor'])
            self.brake.prepare(self.run['brake'])
            if sync.cancel.is_set():
                reason = sync.reason or '用户停止'
                return reason,launched,{},[]
            # Both preflights passed: own both OFF operations even if only one
            # enable succeeds. A failed second startup must still confirm both OFF.
            launched = [('motor', self.motor), ('brake', self.brake)]
            with self.lock:self.participants = list(launched)
            with self.lock:self.run['phase'] = 'starting'
            self._event('coupled_start', self.status()['run'])
            self.motor.start(self.run['motor'], synchronization=sync)
            if not sync.cancel.is_set():
                self.brake.start(self.run['brake'], synchronization=sync)
            while not sync.cancel.is_set():
                if self.cancel.is_set():raise ValueError('操作者停止')
                if time.monotonic()-self.lease > 2.5:raise ValueError('联动页面心跳中断，停止两路输出')
                measured=self._check_sensor()
                if not self.motor_supply.cached_status()['connected']:raise ValueError('电机电源通信中断，停止两路输出')
                if sync.began:
                    with self.lock:
                        self.run['phase'] = 'running'
                        self.run['elapsed'] = round(max(0, time.monotonic()-sync.began), 3)
                    # Engines retain their native I/O, feedback, limit and protection checks.
                    # Only this live joint lease renews their individual watchdogs.
                    self.motor.lease = self.brake.lease = time.monotonic()
                    now=time.monotonic()
                    resolution=10**-int(measured.get('speedDecimals',1))
                    if abs(measured['speed']) > resolution:
                        moving_since=moving_since or now
                        rotated=rotated or now-moving_since>=.5
                    else:
                        moving_since=None
                    target=self.motor.run.get('targetA',0)
                    at_limit=target>=self.motor.run.get('limitA',float('inf'))
                    eligible=target>0 and (rotated or (at_limit and now-sync.began>=2))
                    if standstill.update(measured,self.motor.status(),eligible=eligible,now=time.time()):
                        reason='编码器连续确认电机轴静止，且扭矩传感器报零；保留实测结果'
                        sync.fail('电机',reason)
                sync.cancel.wait(.03)
            reason = sync.reason or '用户停止'
        except Exception as error:
            reason = sync.reason or str(error)
            sync.fail('联动', reason)
        finally:
            with self.lock:self.run['measurementEndedAt']=time.time()
            sync.cancel.set()
            for _, engine in launched:engine.cancel.set()
            with self.lock:self.run['phase'] = 'stopping'
            confirmations, errors = self._stop_engines(launched)
        return reason,launched,confirmations,errors

    @staticmethod
    def _stop_engines(launched):
        errors = []
        confirmations = {}
        def stop_engine(entry):
            role, engine = entry
            try:
                result = engine.stop()
                thread = engine.thread
                if thread and thread is not threading.current_thread():thread.join(5)
                if thread and thread.is_alive():raise ValueError('输出线程尚未退出')
                if engine.status()['run'].get('stopConfirmed') is not True:raise ValueError('输出关闭未确认')
                return role, True, None
            except Exception as error:return role, False, str(error)
        if launched:
            with ThreadPoolExecutor(max_workers=2) as pool:
                for role, confirmed, error in pool.map(stop_engine, launched):
                    confirmations[role] = confirmed
                    if error:errors.append(f'{role}：{error}')
        return confirmations, errors

    def stop(self, wait=False):
        self.cancel_requests()
        with self.control:
            if self.sync and self.active():
                with self.sync.lock:
                    self.sync.reason = '用户停止'
                self.sync.cancel.set()
                # Set cancellation before any potentially blocking device I/O.
                for _, engine in self.participants:engine.cancel.set()
                with self.lock:self.run['phase'] = 'stopping'
            elif self.sync and self.run.get('stopConfirmed') is False:
                launched = list(self.participants)
                with self.lock:self.run.update(running=True,phase='stopping')
                self.thread=threading.Thread(target=self._retry_stop,args=(launched,),daemon=True)
                self.thread.start()
            thread = self.thread
        if wait and thread:
            thread.join(12)
            if thread.is_alive():raise ValueError('两路停机确认仍在处理中，请检查联动状态')
        return self.status()

    def _retry_stop(self, launched):
        confirmations, errors = self._stop_engines(launched)
        with self.lock:
            self.run.update(running=False,phase='stopped',stopConfirmed=bool(launched) and all(confirmations.values()),
                            stopResults=confirmations,stopErrors=errors)
        self._event('coupled_stop_retry', self.status()['run'])
