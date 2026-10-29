"""Homepage recording of existing device data. Never starts/stops any device."""
import json
import copy
import queue
from pathlib import Path
import re
import threading
import time
import uuid
from loaded_timeline import LoadedTimeline
from specialized_tests import SpecializedTests, finite


class RecordManager:
    def __init__(self, root, source, frames, diagnostics=None, event_hub=None, subjects=None):
        self.root, self.source, self.frames = Path(root), source, frames
        self.diagnostics = diagnostics
        self.subjects = subjects
        self.event_hub=event_hub;self.unsubscribe=None
        self.lock = threading.RLock()
        self.control = threading.Lock()
        self.cancel = threading.Event()
        self.thread = self.timeline = self.live = None
        self.events = queue.Queue(maxsize=4096)
        self.event_error = ''

    def active(self):
        with self.lock:
            return bool(self.live and self.live.get('running'))

    def status(self):
        with self.lock:
            return json.loads(json.dumps(self.live)) if self.live else None

    def history(self):
        result = []
        if self.root.exists():
            for path in self.root.glob('*/report.json'):
                try:result.append(json.loads(path.read_text(encoding='utf-8')))
                except (ValueError, OSError):pass
        return sorted(result, key=lambda row:row['startedAt'], reverse=True)[:200]

    def recover_interrupted(self):
        for report in self.history():
            if report.get('running'):
                report.update(running=False, state='interrupted', reason='服务重启，记录中断；已有文件保留', finishedAt=time.time())
                SpecializedTests._save(self.root/report['id']/'report.json', report)

    def file(self, identifier, name):
        if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier) or name not in ('timeline.csv', 'timeline.jsonl', 'report.json'):
            raise ValueError('无效记录文件')
        path = self.root/identifier/name
        if not path.is_file():raise ValueError('记录文件不存在')
        return path

    def start(self, data, *, test_subject=None):
        with self.control:
            if self.active() or (self.thread and self.thread.is_alive()):
                raise ValueError('已有首页记录，请先停止记录')
            if not isinstance(data, dict) or data.get('range', 100) not in (50, 100):
                raise ValueError('记录配置无效')
            source = self.source()
            if not any(SpecializedTests._fresh(channel, 3) for channel in source.values()):
                raise ValueError('请先连接并读取至少一个真实设备')
            subject = copy.deepcopy(test_subject if test_subject is not None else self.subjects() if self.subjects else {})
            ratio = (subject.get('testSetup') or {}).get('ratio')
            report = {'id':uuid.uuid4().hex,'recordSchema':2,'hostApi':1, 'name':str(data.get('name') or '工作台试验')[:100],
                      'testSubject':subject, 'ratio':ratio,
                      'range':data.get('range', 100), 'startedAt':time.time(), 'running':True,
                      'state':'recording', 'reason':'', 'elapsedS':0, 'sourceAtStart':source,
                      'notes':['仅记录现有工况，不启动或停止电机，不修改电源、控制参数或保护。',
                               '各接口保留实际更新速度和原有接收时间；其他接口的 CSV 列留空。',
                               '完整记录为 timeline.csv / timeline.jsonl；不受网页刷新或预览点数限制。']}
            directory = self.root/report['id'];directory.mkdir(parents=True, exist_ok=False)
            timeline = None
            try:
                timeline = LoadedTimeline(directory, self.source, self.frames, ratio,
                                          {'recordingId':report['id'],'name':report['name'], 'range':report['range'], 'sourceAtStart':source,
                                           'testSubject':report['testSubject']},event_hub=self.event_hub)
                timeline.start_recording()
                if self.diagnostics:
                    report['backgroundDirectory'] = self.diagnostics.start(
                        {'id':report['id'], 'name':report['name'], 'startedAt':report['startedAt'], 'testSubject':report['testSubject']})
                report.update(timeline.status())
                SpecializedTests._save(directory/'report.json', report)
            except Exception:
                if self.diagnostics:
                    try:self.diagnostics.close()
                    except Exception:pass
                if timeline:
                    try:timeline.close()
                    except Exception:pass
                raise
            self.cancel = threading.Event()
            if self.event_hub:
                self.unsubscribe=self.event_hub.subscribe(lambda event:self.observe(event['type'],event['data']) if event['type'] not in ('state','acquisition','snapshot','sensor_frames','sensor_stream') else None)
            self.events = queue.Queue(maxsize=4096)
            self.event_error = ''
            with self.lock:self.timeline, self.live = timeline, report
            self.thread = threading.Thread(target=self._run, args=(directory, report, timeline), daemon=True)
            self.thread.start()
            return self.status()

    def observe(self, source, payload):
        # Never acquire a file/timeline lock on a device-control thread.
        if not self.live or not self.live.get('running') or self.cancel.is_set():return
        try:self.events.put_nowait((source, copy.deepcopy(payload), time.time()))
        except queue.Full:self.event_error = '控制事件队列已满，完整记录中断'
        except Exception as error:self.event_error = str(error)

    def _drain_events(self, timeline):
        while True:
            try:source,payload,stamp = self.events.get_nowait()
            except queue.Empty:break
            timeline.event(source,payload,stamp)

    def stop(self, identifier):
        with self.control:
            report = self.status()
            if not report or identifier != report['id']:
                raise ValueError('当前记录已变化，请刷新后重试')
            self.cancel.set()
            if self.thread:
                self.thread.join(4)
                if self.thread.is_alive():raise ValueError('正在保存完整记录，请稍后重试')
            return self.status()

    def _run(self, directory, report, timeline):
        began = time.monotonic()
        def publish(save=False):
            report.update(timeline.status(), elapsedS=time.monotonic()-began)
            with self.lock:self.live=json.loads(json.dumps(report))
            if save:SpecializedTests._save(directory/'report.json', report)
        try:
            while not self.cancel.wait(.5):
                self._drain_events(timeline)
                if self.event_error:raise ValueError(self.event_error)
                if timeline.error:raise ValueError(timeline.error)
                if self.diagnostics and self.diagnostics.error:raise ValueError(f'后台记录异常：{self.diagnostics.error}')
                publish(save=True)
            report.update(state='completed', reason='记录已停止；电机和电源仍由首页控制')
        except Exception as error:
            report.update(state='interrupted', reason=str(error))
        finally:
            if self.unsubscribe:self.unsubscribe();self.unsubscribe=None
            try:self._drain_events(timeline)
            except Exception as error:report.update(state='interrupted',reason=f'控制事件保存失败：{error}')
            if self.diagnostics:
                try:self.diagnostics.close()
                except Exception as error:
                    report.update(state='interrupted', reason=f'后台记录保存异常：{error}')
            try:
                timeline.end_measurement()
                timeline.close()
            except Exception as error:
                report.update(state='interrupted', reason=f'完整记录异常：{error}')
            report.update(running=False, finishedAt=time.time())
            try:publish(save=True)
            except OSError as error:
                report.update(state='interrupted', reason=f'报告保存失败：{error}')
                publish()

class RecordingView:
    """A test's measurement interval inside the manager's shared recording."""
    def __init__(self,manager,name,ratio,*,test_subject=None):
        self.manager,self.name,self.ratio=manager,name,ratio
        self.test_subject=copy.deepcopy(test_subject)
        self.owned=not manager.active();self.identifier=None;self.recording_at=None;self.measurement_end=None
        self.phase='preparation';self.error='';self.unsubscribe=None;self.cursor=0
        from specialized_tests import Stats
        self.stats={k:Stats() for k in ('torqueNm','speedRpm','shaftPowerW','actualIqA','temperatureC')}
        self.counts={};self.motor_stamp=None
    def event(self,kind,payload):self.manager.observe(kind,{'test':self.name,'phase':self.phase,'data':payload})
    def start_recording(self):
        self.owned=not self.manager.active()
        report=self.manager.start({'name':self.name},test_subject=self.test_subject) if self.owned else self.manager.status()
        if self.test_subject is not None and report.get('testSubject')!=self.test_subject:
            raise ValueError('现有完整记录的测试配置不同，请先结束记录再启动专项')
        self.identifier=report['id'];self.recording_at=time.time()
        if self.manager.event_hub:self.unsubscribe=self.manager.event_hub.subscribe(self._sample)
        self.event('test_measurement_start',{'id':self.identifier,'owner':self.owned,'startedAt':self.recording_at})
    def _sample(self,event):
        if event['type']!='acquisition' or self.measurement_end is not None:return
        sources=event['data']['sources'];motor=(sources.get('motor') or {}).get('snapshot') or {}
        if motor.get('receivedAt')!=self.motor_stamp:
            self.motor_stamp=motor.get('receivedAt')
            self.stats['actualIqA'].add((motor.get('feedback') or {}).get('iq_a'));self.stats['temperatureC'].add((motor.get('ntc') or {}).get('temp_c'))
        packet=event['data']['packet']
        if packet:
            config=packet.get('config') or {}
            for seq,stamp,raw_torque,raw_speed in packet.get('frames',[]):
                if seq<=self.cursor or stamp<self.recording_at:continue
                self.cursor=seq;torque=raw_torque/10**config.get('torqueDecimals',2);speed=raw_speed/10**config.get('speedDecimals',1)
                self.stats['torqueNm'].add(torque);self.stats['speedRpm'].add(speed);self.stats['shaftPowerW'].add(abs(torque*speed)/9.55)
        else:
            sensor=(sources.get('sensor') or {}).get('snapshot') or {};stamp=sensor.get('receivedAt')
            if stamp and stamp!=self.cursor and stamp>=self.recording_at:
                self.cursor=stamp;self.stats['torqueNm'].add(sensor.get('torque'));self.stats['speedRpm'].add(sensor.get('speed'))
    def end_measurement(self):
        if self.measurement_end is None:
            self.measurement_end=time.time();self.event('test_measurement_end',{'endedAt':self.measurement_end})
    def status(self):
        return {'recordingId':self.identifier,'recordingStartedAt':self.recording_at,'recordingEndedAt':self.measurement_end,'statistics':{k:v.result() for k,v in self.stats.items()},'error':self.error,'counts':self.counts}
    def close(self):
        if self.unsubscribe:self.unsubscribe();self.unsubscribe=None
        if self.owned and self.identifier:self.manager.stop(self.identifier)
