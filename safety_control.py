"""One browser owns the live lease. Stop paths never wait for the command gate."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import threading
import time
from power_limits import power_problem, power_evidence


class SafetyControl:
    def __init__(self, channels, motor, brake, coupled, path):
        self.channels, self.motor, self.brake, self.coupled = channels, motor, brake, coupled
        self.path = Path(path)
        self.lock = threading.RLock()
        self.journal_lock = threading.RLock()
        self.context = threading.local()
        self.owner = None
        self.lease = 0
        self.epoch = 0
        self.started = False
        self.blocked = False
        self.reason = ''
        self.errors = []
        self.stop_thread = None
        self.stopping = False
        self.power_off = set()
        self.closed = threading.Event()
        self.pollers = []
        self.reconciled = False
        self.motor_isolated = False
        self.isolated_run = None
        self.journal_thread = None
        self.expected = set()
        self.automatic = None
        self.observe = None
        self.last_power_fault = None
        self.fault_thread = None
        self.fault_lock = threading.Lock()
        self.handled_device_faults = {}
        try:self.last_power_fault=json.loads(self.path.with_name('power-fault.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):pass

    def _event(self, kind, payload):
        if self.observe:
            try:self.observe(kind,payload)
            except Exception:pass

    def status(self):
        with self.lock:
            return dict(epoch=self.epoch, owned=bool(self.owner), blocked=self.blocked,
                        stopping=self.stopping,
                        reason=self.reason, errors=list(self.errors),motorIsolated=self.motor_isolated and
                        not (self.channels['motor-supply'].snapshot or {}).get('status',128)&128,
                        unusedChannels=[key for key,c in self.channels.items() if not c.config and key not in self.expected and not c.output_uncertain],
                        lastPowerFault=copy.deepcopy(self.last_power_fault))

    def _persist(self, dirty):
        # Energizing is refused if its restart journal cannot be written.
        # OFF never depends on this file; a stale dirty marker is conservative.
        with self.journal_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'outputsMayBeOn':dirty,'expectedKeys':sorted(self.expected) if dirty else []}), encoding='utf-8')
            os.replace(temporary,self.path)

    def check(self):
        if not self.started:return
        with self.lock:
            if self.blocked or getattr(self.context,'epoch',self.epoch)!=self.epoch:
                raise ValueError('指令已被停止取消，输出关闭确认前禁止启动')

    def claim(self, client, epoch, *, energizing=False, restoring=False, power_keys=None):
        if not self.started:return
        with self.lock:
            if not client or not 16 <= len(client) <= 128:raise ValueError('请刷新网页以建立控制会话')
            if str(epoch) != str(self.epoch):raise ValueError('停止已取消旧指令，请读取状态后重新操作')
            if self.blocked:
                if restoring:return
                raise ValueError('停机未确认，请先重试停止并确认两路输出关闭')
            if self.owner and self.owner != client:raise ValueError('另一浏览器正在控制设备；请先停止输出再移交')
            if energizing or self.owner==client:self.owner,self.lease = client,time.monotonic()
            if energizing:self.expected.update(self.channels if power_keys is None else power_keys)
            generation=self.epoch
            self.context.epoch=generation
        if energizing:self._persist(True)
        with self.lock:
            if self.epoch!=generation or self.blocked:raise ValueError('启动已被停止取消')

    def heartbeat(self, client):
        with self.lock:
            if client and client == self.owner:
                self.lease = time.monotonic()
                self.motor.lease = self.brake.lease = self.coupled.lease = self.lease
            return self.status()

    def invalidate(self, reason):
        with self.lock:
            self.epoch += 1
            self.blocked = True
            self.reason = reason
        # No serial, CAN, disk or ordinary command lock here.
        self.motor.cancel.set()
        self.brake.cancel.set()
        self.coupled.cancel_requests(reason)
        for channel in self.channels.values():channel.cancel_pending()

    def stop(self, reason='操作者停止', power_keys=()):
        self.invalidate(reason)
        self._event('safety_stop_request',{'reason':reason,'epoch':self.epoch,'powerKeys':list(power_keys)})
        with self.lock:
            self.power_off.update(power_keys)
            if not self.stopping:
                self.stopping=True
                self.stop_thread = threading.Thread(target=self._stop,daemon=True,name='safety-stop')
                self.stop_thread.start()
        return self.status()

    def _stop(self):
        errors = []
        power_ok = True
        def attempt(label, action):
            try:action();return True
            except Exception as error:errors.append(f'{label}：{error}');return False
        def power_off(key):
            channel=self.channels[key]
            if not channel.config and key not in self.expected and not channel.output_uncertain:return
            channel.set_output(False)
        def stop_motor():
            if (self.motor.status().get('connected') or self.motor.run.get('running') or 'motor-supply' in self.expected
                    or self.channels['motor-supply'].config):
                self.motor.stop()
        def stop_brake():
            if self.brake.channel.config or self.brake.active() or 'brake-supply' in self.expected:self.brake.stop()
        # Power OFF is sent alongside CAN OFF, even when the CAN adapter is dead.
        with ThreadPoolExecutor(max_workers=4) as pool:
            motor = pool.submit(attempt,'电机',stop_motor)
            brake = pool.submit(attempt,'制动',stop_brake)
            while True:
                with self.lock:
                    keys=list(self.power_off);self.power_off.clear()
                if not keys:break
                futures=[pool.submit(attempt,key,lambda key=key:power_off(key)) for key in keys]
                for task in futures:power_ok=task.result() and power_ok
            motor_ok,brake_ok = motor.result(),brake.result()
        if not motor_ok:
            # A disconnected motor must not keep driving after its load releases.
            fallback = attempt('电机电源断电兜底',lambda:self.channels['motor-supply'].set_output(False))
            if fallback:
                fallback = attempt('电机电源放电确认',lambda:self.wait_discharged(self.channels['motor-supply']))
            motor_ok = fallback
            self.motor_isolated = bool(fallback)
            if fallback:self.isolated_run=self.motor.run
        for engine in (self.motor,self.brake,self.coupled,self.automatic):
            if engine is None:continue
            thread=engine.thread
            if thread and thread is not threading.current_thread():
                thread.join(4)
                if thread.is_alive():errors.append('输出任务尚未退出');motor_ok=False
        # A late OFF request may have arrived while CAN confirmation was running.
        with self.lock:keys=list(self.power_off);self.power_off.clear()
        for key in keys:power_ok=attempt(key,lambda key=key:power_off(key)) and power_ok
        with self.lock:
            if self.power_off:
                # Do not lose a power-OFF arriving during final confirmation.
                self.stop_thread=threading.Thread(target=self._stop,daemon=True,name='safety-stop')
                self.stop_thread.start()
                return
            self.errors = errors
            self.blocked = not (motor_ok and brake_ok and power_ok) or any(c.output_uncertain for c in self.channels.values()
                if not (c.snapshot or {}).get('status',0)&128)
            clean = all((not c.config and key not in self.expected and not c.output_uncertain) or
                (c.snapshot and not c.snapshot.get('status',0)&128 and not c.output_uncertain)
                for key,c in self.channels.items())
            if clean:
                self.owner = None
                self.expected.clear()
            if not self.blocked:self.reason += '；停止处理完成'
            self.stopping=False
        if self.motor_isolated and brake_ok and not self.coupled.active():
            with self.coupled.lock:
                self.coupled.run.update(stopConfirmed=True,motorIsolated=True,stopMethod='power-isolation')
        self._event('safety_stop_result',{'safety':self.status(),
            'supplies':{key:channel.cached_status() for key,channel in self.channels.items()},'motor':self.motor.status()})
        if clean:
            def clean_journal():
                with self.journal_lock:
                    if self.owner is None:
                        try:self._persist(False)
                        except OSError:pass
            if not self.journal_thread or not self.journal_thread.is_alive():
                self.journal_thread=threading.Thread(target=clean_journal,daemon=True)
                self.journal_thread.start()

    @staticmethod
    def wait_discharged(channel, cancel=None, timeout=5):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if cancel and cancel.is_set():raise ValueError('恢复已取消')
            status=channel.read();snapshot=status['snapshot']
            voltage=snapshot['measured'][0]/10**status['config']['voltageDecimals']
            # One device current display count is the smallest usable threshold.
            if not snapshot['status']&128 and voltage<=.5 and snapshot['measured'][1]<=1:return
            if cancel:
                if cancel.wait(.15):raise ValueError('恢复已取消')
            else:time.sleep(.15)
        raise ValueError('电源输出关闭后电压仍未降至 0.5 V 以下，禁止重新上电')

    def _power_trip(self, key, status, issue):
        detail=copy.deepcopy(dict(at=time.time(),channel=key,issue=issue,
                                 config=status.get('config'),trigger=power_evidence(status.get('snapshot') or {}),
                                 verification=status.get('powerCheck')))
        with self.lock:
            if self.stopping:return
            self.last_power_fault=detail
        label='电机电源' if key=='motor-supply' else '制动电源'
        # OFF starts before any diagnostic disk I/O.
        self.stop(f"{label}：{issue['message']}",self.channels.keys())
        self._event('power_boundary_stop',detail)
        def persist():
            try:
                with self.fault_lock:
                    path=self.path.with_name('power-fault.json');path.parent.mkdir(parents=True,exist_ok=True)
                    temporary=path.with_suffix('.tmp')
                    temporary.write_text(json.dumps(detail,ensure_ascii=False,indent=2),encoding='utf-8')
                    os.replace(temporary,path)
            except OSError as error:
                with self.lock:detail['logError']=str(error)
        self.fault_thread=threading.Thread(target=persist,daemon=True,name='power-fault-log')
        self.fault_thread.start()

    def _poll(self, key, channel):
        had_link = False
        while not self.closed.is_set():
            if key != 'brake-supply' or not self.brake.active():
                if channel.config:
                    try:
                        status=channel.read()
                        issue=power_problem(key,status['config'],status['snapshot'])
                        if issue:
                            off=not status['snapshot'].get('status',0)&128
                            duplicate=(issue['kind']=='hardware' and off and self.handled_device_faults.get(key)==issue['message'])
                            if issue['kind']=='hardware':self.handled_device_faults[key]=issue['message']
                            if not duplicate:self._power_trip(key,status,issue)
                        else:self.handled_device_faults.pop(key,None)
                        if not had_link and not issue and status.get('connected'):
                            # Recovery retries OFF verification through _watch;
                            # it never clears the lock or energizes by itself.
                            with self.lock:
                                if self.blocked and not self.stopping:self.reconciled=False
                        had_link = bool(status.get('connected'))
                        if not issue and status['snapshot']['status']&128:
                            with self.lock:unowned=not self.owner
                            if unowned:self.stop('检测到未认领的电源输出，关闭后重新操作',self.channels.keys())
                    except Exception as error:
                        # OFF invalidates an older read. Retry monitoring on the
                        # next poll; the independent stale-link guard stays active.
                        if not getattr(error,'operation_cancelled',False):had_link=False
                        if channel.output_uncertain and not getattr(error,'operation_cancelled',False):
                            self._power_trip(key,channel.cached_status(),dict(kind='communication',message=f'通信失效：{error}'))
            self.closed.wait(.8)

    def _watch(self):
        while not self.closed.wait(.1):
            with self.lock:
                expired=bool(self.owner and time.monotonic()-self.lease>3)
                stopping=self.stopping
                reconcile=self.blocked and not self.reconciled and all(c.config for c in self.channels.values())
                if reconcile:self.reconciled=True
                link_lost=[key for key,c in self.channels.items() if c.output_uncertain and not c.cached_status()['connected']]
                motor=self.motor.status()
                unmanaged=(motor.get('connected') and (motor.get('snapshot') or {}).get('motor',{}).get('output_on')
                           and not self.motor.run.get('running') and not self.coupled.active()
                           and not (self.automatic and self.automatic.active()))
            if reconcile and not stopping:
                self.stop('核对上次输出并关闭',self.channels.keys())
            if unmanaged and not stopping:
                self.stop('检测到工作台任务以外的电机输出，关闭输出',self.channels.keys())
            elif link_lost and not stopping:
                key=link_lost[0]
                self._power_trip(key,self.channels[key].cached_status(),
                                 dict(kind='communication',message='反馈过期或失联，关闭两路输出'))
            elif expired and not stopping:
                self.stop('控制浏览器心跳超过 3 秒，关闭输出',self.channels.keys())
            elif self.owner and not stopping and not self.coupled.active() and any(
                    engine.run.get('phase')=='stopped' and engine.run.get('stopConfirmed') is False
                    and engine.run is not self.isolated_run
                    for engine in (self.motor,self.brake)):
                self.stop('输出关闭未确认，执行电源断电兜底',self.channels.keys())

    def start(self):
        self.started = True
        try:
            if self.path.exists():
                saved=json.loads(self.path.read_text())
                if saved['outputsMayBeOn']:
                    self.expected.update(saved.get('expectedKeys',self.channels))
                    self.blocked = True
                    self.reason = '上次退出时输出可能未关闭，正在核对并关闭'
        except (OSError,ValueError,KeyError):
            self.expected.update(self.channels)
            self.blocked = True;self.reason='停机状态文件异常，需确认输出关闭'
        for key,channel in self.channels.items():
            thread=threading.Thread(target=self._poll,args=(key,channel),daemon=True)
            self.pollers.append(thread);thread.start()
        threading.Thread(target=self._watch,daemon=True).start()

    def close(self):
        self.stop('关闭工作台',self.channels.keys())
        deadline=time.monotonic()+12
        while self.stopping and time.monotonic()<deadline:time.sleep(.05)
        self.closed.set()
        if self.fault_thread:self.fault_thread.join(1)
