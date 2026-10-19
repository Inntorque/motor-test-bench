"""Brake PSU current stepping. Fixed 24 V, 2.3 A cap; no motor commands."""
import math
import threading
import time
import uuid


class BrakeCurrent:
    def __init__(self, channel, observe):
        self.channel, self.observe = channel, observe
        self.control = threading.RLock()
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.thread = None
        self.lease = 0
        self.run = {'running':False, 'phase':'idle', 'targetA':0, 'steps':0,
                    'elapsed':0, 'reason':'', 'stopConfirmed':None}

    def status(self, heartbeat=False):
        with self.lock:
            if heartbeat:self.lease = time.monotonic()
            return {'run':dict(self.run), 'device':self.channel.cached_status()}

    def active(self):
        with self.lock:return self.run['running']

    @staticmethod
    def validate(data):
        try:step, interval, duration = (float(data[key]) for key in ('stepA','intervalS','durationS'))
        except (KeyError, TypeError, ValueError):raise ValueError('请填写电流步长、步进间隔和持续时间') from None
        if not all(math.isfinite(v) for v in (step, interval, duration)):
            raise ValueError('步进参数必须为有限数值')
        if not .001 <= step <= 2.3 or abs(step*1000-round(step*1000)) > 1e-6:
            raise ValueError('制动电流步长为 0.001–2.3 A，最多三位小数')
        if not .2 <= interval <= 10:raise ValueError('步进间隔为 0.2–10 s')
        if not 1 <= duration <= 255:raise ValueError('持续时间为 1–255 s')
        if duration <= interval:raise ValueError('持续时间必须大于步进间隔，才能执行加电流')
        return step, interval, duration

    def _event(self, kind, payload):
        try:self.observe(kind, payload)
        except Exception:pass

    def _command(self, action, data):
        request = {'commandId':uuid.uuid4().hex, 'action':action, 'request':data}
        began = time.monotonic()
        self._event('brake_command_request', request)
        try:
            result = self.channel.set_values(data) if action == 'set' else self.channel.set_output(data['on'], data)
        except Exception as error:
            self._event('brake_command_error', {**request,'error':str(error),'durationS':time.monotonic()-began})
            raise
        self._event('brake_command_response', {**request,'response':result,'durationS':time.monotonic()-began})
        return result

    def start(self, data):
        step, interval, duration = self.validate(data)
        with self.control, self.channel.lock:
            if self.active() or (self.thread and self.thread.is_alive()):raise ValueError('制动电流步进正在运行')
            status = self.channel.read()
            if status['snapshot']['status'] & 128:raise ValueError('请先关闭制动输出，再启动步进')
            config = status['config']
            if config.get('protocol', 'ssj') != 'ssj':raise ValueError('制动步进仅支持当前 SSJ 电源')
            if config.get('currentDecimals') != 3:raise ValueError('制动电源电流精度应为 0.001 A，请重新连接')
            limit = min(2.3, config.get('maxCurrent') or 2.3, config.get('ratedCurrent') or 2.3)
            if step > limit:raise ValueError(f'步长超过电源限幅 {limit:g} A')
            with self.lock:
                self.cancel = threading.Event()
                self.lease = time.monotonic()
                self.run = {'running':True, 'phase':'starting', 'targetA':0, 'steps':0, 'elapsed':0,
                            'stepA':step,'intervalS':interval,'durationS':duration,'limitA':limit,
                            'limitReached':False, 'reason':'','stopConfirmed':None}
            self._event('brake_step_start', dict(self.run))
            self.thread = threading.Thread(target=self._run, args=(step,interval,duration,limit),daemon=True)
            self.thread.start()
            return self.status()

    def _off(self):
        with self.channel.lock:
            result = self._command('output', {'on':False})
            if result['status'] & 128:raise ValueError('制动输出关闭未确认')
            with self.lock:self.run['stopConfirmed'] = True

    def _run(self, step, interval, duration, limit):
        reason = '持续时间到期'
        began = None
        try:
            with self.channel.lock:
                if self.cancel.is_set():return
                self._command('set', {'voltage':24,'current':0})
                if self.cancel.is_set():return
                self._command('output', {'on':True,'voltage':24,'current':0})
            began = time.monotonic()
            next_step = began+interval
            with self.lock:self.run['phase'] = 'running'
            while not self.cancel.is_set():
                now = time.monotonic()
                with self.lock:self.run['elapsed'] = round(now-began,3)
                if now-began >= duration:break
                if now-self.lease > 3:raise ValueError('工作台页面心跳中断，停止制动输出')
                with self.channel.lock:
                    if self.cancel.is_set():break
                    status = self.channel.read()
                    if not status['snapshot']['status'] & 128:raise ValueError('制动电源输出已关闭，步进终止')
                    now = time.monotonic()
                    if now-began >= duration:break
                    if now >= next_step and self.run['targetA'] < limit:
                        if now-next_step > interval:raise ValueError('电源通信耗时超过步进间隔，请增大间隔')
                        if self.cancel.is_set():break
                        target = min(limit, round((self.run['steps']+1)*step,3))
                        self._command('set', {'voltage':24,'current':target})
                        with self.lock:
                            self.run.update(targetA=target,steps=self.run['steps']+1,limitReached=target>=limit)
                        self._event('brake_step', dict(self.run))
                        next_step += interval
                self.cancel.wait(.05)
            if self.cancel.is_set():reason = '用户停止'
        except Exception as error:
            reason = str(error)
        finally:
            self.cancel.set()
            try:self._off()
            except Exception as error:
                reason += f'；停止失败：{error}，请现场关闭制动电源'
                with self.lock:self.run['stopConfirmed'] = False
            with self.lock:
                self.run.update(running=False,phase='stopped',reason=reason,
                                elapsed=round(time.monotonic()-began,3) if began else 0)
            self._event('brake_step_end', dict(self.run))

    def stop(self):
        self.cancel.set()
        with self.control:
            with self.lock:self.run['stopConfirmed'] = False
            try:self._off()
            except Exception:
                with self.lock:self.run['stopConfirmed'] = False
                raise
            if self.thread:self.thread.join(3)
            if self.thread and self.thread.is_alive():raise ValueError('已发送关闭指令，正在等待制动步进退出')
            if self.run['stopConfirmed'] is not True:raise ValueError(self.run['reason'] or '制动输出关闭未确认')
            with self.lock:self.run.update(running=False,phase='stopped',reason='用户停止')
            return self.status()
