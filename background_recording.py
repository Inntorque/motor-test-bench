"""Passive diagnostics: cached device feedback and queued commands, never device I/O."""
import copy
import hashlib
import json
import os
from pathlib import Path
import queue
import threading
import time
import uuid


class BackgroundRecording:
    def __init__(self, root, source, frames, rotate_seconds=900):
        self.root, self.source, self.frames = Path(root), source, frames
        self.rotate_seconds = rotate_seconds
        self.queue = queue.Queue(maxsize=8192)
        self.cancel = threading.Event()
        self.thread = None
        self.directory = None
        self.dropped = 0
        self.error = ''
        self.counts = {}
        self.sensor_frames = 0
        self.session = None
        self.ready = threading.Event()

    def observe(self, kind, payload):
        # Control threads never wait for file writes or a full queue.
        if not self.thread or not self.thread.is_alive() or self.cancel.is_set():
            return
        try:
            self.queue.put_nowait((time.time(), time.monotonic(), kind, copy.deepcopy(payload)))
        except queue.Full:
            self.dropped += 1
        except Exception as error:
            self.error = f'event copy failed: {error}'

    def start(self, session=None):
        if self.thread and self.thread.is_alive():
            raise ValueError('后台记录仍在运行或正在结束')
        self.queue = queue.Queue(maxsize=8192)
        self.cancel = threading.Event()
        self.ready = threading.Event()
        self.dropped, self.sensor_frames = 0, 0
        self.error, self.counts = '', {}
        self.session = copy.deepcopy(session)
        self.root.mkdir(parents=True, exist_ok=True)
        self.started_at, self.began = time.time(), time.monotonic()
        identifier = time.strftime('%Y%m%d_%H%M%S', time.localtime(self.started_at))+'_'+uuid.uuid4().hex[:8]
        self.directory = self.root/identifier
        self.directory.mkdir()
        project = Path(__file__).resolve().parent
        self.code = {name:hashlib.sha256((project/name).read_bytes()).hexdigest()
                     for name in ('hardware_bridge.py', 'motor_current.py', 'brake_current.py', 'background_recording.py',
                                  'web/src/App.jsx', 'web/src/MotorCurrentPanel.jsx', 'web/src/BrakeCurrentPanel.jsx') if (project/name).is_file()}
        self.cancel.clear()
        self.thread = threading.Thread(target=self._run, name='background-recording', daemon=True)
        self.thread.start()
        if not self.ready.wait(3) or self.error or not self.thread.is_alive():
            self.close()
            raise ValueError(self.error or '后台记录启动未确认')
        return str(self.directory)

    def _manifest(self, running):
        value = {'schema':1, 'directory':str(self.directory), 'startedAt':self.started_at,
                 'workbenchRecording':self.session,
                 'updatedAt':time.time(), 'running':running, 'counts':self.counts,
                 'sensorFrames':self.sensor_frames, 'droppedEvents':self.dropped, 'error':self.error,
                 'files':'events-*.jsonl', 'codeSha256':self.code,
                 'sensorFrameColumns':['sequence','receivedUnixS','rawTorque','rawSpeed'],
                 'notes':['由首页开始/停止记录按钮同步管理；不发送设备指令，不续期页面控制心跳。',
                          'snapshot 保留各设备实际反馈时间，不插值、不生成模拟值。',
                          'sensor_frames 保留逐帧序号和时间；断线、重置及缺帧单独记录。',
                          '请求包含输入值；motor_command_* 包含步进脚本实际发出的指令。',
                          '每秒刷新文件，15分钟分卷，不自动删除历史记录。']}
        for path in (self.directory/'manifest.json', self.root/'latest.json'):
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(path)

    def _run(self):
        stream = None
        part = 0
        previous = {}
        cursor = None
        sensor_config = None
        last_flush = last_sync = time.monotonic()
        lost = 0

        def emit(kind, payload, wall=None, mono=None):
            nonlocal stream, part, opened
            now = time.monotonic()
            if stream is None or now-opened >= self.rotate_seconds:
                if stream:
                    stream.flush(); os.fsync(stream.fileno()); stream.close()
                part += 1
                stream = (self.directory/f'events-{part:05d}.jsonl').open('a', encoding='utf-8')
                opened = now
            row = {'schema':1, 'recordedAt':time.time() if wall is None else wall,
                   'elapsedS':(now if mono is None else mono)-self.began, 'kind':kind, 'payload':payload}
            stream.write(json.dumps(row, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n')
            self.counts[kind] = self.counts.get(kind, 0)+1

        opened = 0
        try:
            emit('session_start', {'pid':os.getpid(), 'startedAt':self.started_at})
            self._manifest(True)
            self.ready.set()
            while True:
                try:
                    for _ in range(1024):
                        try:wall, mono, kind, payload = self.queue.get_nowait()
                        except queue.Empty:break
                        emit(kind, payload, wall, mono)
                    if lost != self.dropped:
                        emit('event_gap', {'droppedSinceLast':self.dropped-lost, 'total':self.dropped})
                        lost = self.dropped
                    for channel, state in self.source().items():
                        encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, allow_nan=False)
                        if previous.get(channel) != encoded:
                            emit('snapshot', {'channel':channel, 'state':state})
                            previous[channel] = encoded
                    packet = self.frames(cursor or 0)
                    if packet is None:
                        if cursor is not None:
                            emit('sensor_stream_unavailable', {'lastSequence':cursor})
                        cursor, sensor_config = None, None
                    else:
                        config = packet.get('config') or {}
                        batch = packet.get('frames') or []
                        if cursor is None:
                            # Do not import pre-session ring-buffer history.
                            batch = [row for row in batch if row[1] >= self.started_at]
                        if config != sensor_config or packet.get('reset') or packet.get('dropped'):
                            emit('sensor_stream', {'config':config, 'reset':packet.get('reset', False),
                                                  'droppedFrames':packet.get('dropped', 0), 'previousSequence':cursor})
                            sensor_config = copy.deepcopy(config)
                        if batch:
                            emit('sensor_frames', {'config':config, 'frames':batch})
                            self.sensor_frames += len(batch)
                        if packet.get('frames'):
                            cursor = packet['frames'][-1][0]
                        elif cursor is None:
                            cursor = 0
                    now = time.monotonic()
                    if now-last_flush >= 1:
                        stream.flush()
                        self._manifest(True)
                        last_flush = now
                    if now-last_sync >= 5:
                        os.fsync(stream.fileno())
                        last_sync = now
                except Exception as error:
                    self.error = str(error)
                    print(f'Background recording: {error}', flush=True)
                    try:emit('recording_error', {'error':str(error)})
                    except Exception:pass
                    if self.cancel.wait(1):break
                if self.cancel.is_set() and self.queue.empty():break
                self.cancel.wait(.02)
            emit('session_end', {'sensorFrames':self.sensor_frames, 'droppedEvents':self.dropped})
        except Exception as error:
            self.error = str(error)
            print(f'Background recording stopped: {error}', flush=True)
        finally:
            self.ready.set()
            if stream:
                try:stream.flush(); os.fsync(stream.fileno())
                except OSError:pass
                stream.close()
            try:self._manifest(False)
            except OSError as error:print(f'Background recording manifest: {error}', flush=True)

    def close(self):
        self.cancel.set()
        if self.thread:
            self.thread.join(4)
            if self.thread.is_alive():raise ValueError('后台记录正在保存，请稍后重试')
