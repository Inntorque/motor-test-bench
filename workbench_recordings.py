"""Homepage recording of existing device data. Never starts/stops any device."""
import json
from pathlib import Path
import re
import threading
import time
import uuid
from loaded_timeline import LoadedTimeline
from specialized_tests import SpecializedTests, finite


class WorkbenchRecordings:
    def __init__(self, root, source, frames, diagnostics=None):
        self.root, self.source, self.frames = Path(root), source, frames
        self.diagnostics = diagnostics
        self.lock = threading.RLock()
        self.control = threading.Lock()
        self.cancel = threading.Event()
        self.thread = self.timeline = self.live = None

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

    def start(self, data):
        with self.control:
            if self.active() or (self.thread and self.thread.is_alive()):
                raise ValueError('已有首页记录，请先停止记录')
            if not isinstance(data, dict) or data.get('range', 50) not in (50, 100):
                raise ValueError('记录配置无效')
            source = self.source()
            if not any(SpecializedTests._fresh(channel, 3) for channel in source.values()):
                raise ValueError('请先连接并读取至少一个真实设备')
            profile = (((source.get('motor') or {}).get('snapshot') or {}).get('motorProfile') or {})
            ratio = (profile.get('nameplate') or {}).get('reduction_ratio')
            report = {'id':uuid.uuid4().hex, 'name':str(data.get('name') or '工作台试验')[:100],
                      'range':data.get('range', 50), 'startedAt':time.time(), 'running':True,
                      'state':'recording', 'reason':'', 'elapsedS':0, 'sourceAtStart':source,
                      'notes':['仅记录现有工况，不启动或停止电机，不修改电源、控制参数或保护。',
                               '各接口保留实际更新速度和原有接收时间；其他接口的 CSV 列留空。',
                               '完整记录为 timeline.csv / timeline.jsonl；不受网页刷新或预览点数限制。']}
            directory = self.root/report['id'];directory.mkdir(parents=True, exist_ok=False)
            timeline = None
            try:
                timeline = LoadedTimeline(directory, self.source, self.frames, ratio,
                                          {'name':report['name'], 'range':report['range'], 'sourceAtStart':source})
                timeline.start_recording()
                if self.diagnostics:
                    report['backgroundDirectory'] = self.diagnostics.start(
                        {'id':report['id'], 'name':report['name'], 'startedAt':report['startedAt']})
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
            with self.lock:self.timeline, self.live = timeline, report
            self.thread = threading.Thread(target=self._run, args=(directory, report, timeline), daemon=True)
            self.thread.start()
            return self.status()

    def observe(self, source, payload):
        # Recording failures must never turn a successful device operation into a failure.
        with self.lock:
            timeline = self.timeline if self.live and self.live.get('running') and not self.cancel.is_set() else None
        if timeline:
            try:timeline.event(source, payload)
            except Exception as error:
                timeline.error = str(error)
                self.cancel.set()

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
                if timeline.error:raise ValueError(timeline.error)
                if self.diagnostics and self.diagnostics.error:raise ValueError(f'后台记录异常：{self.diagnostics.error}')
                publish(save=True)
            report.update(state='completed', reason='记录已停止；电机和电源仍由首页控制')
        except Exception as error:
            report.update(state='interrupted', reason=str(error))
        finally:
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
