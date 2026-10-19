"""Passive HEX stream receiver. Never transmits serial bytes."""
import csv
import io
from itertools import takewhile
import threading
import time
from collections import deque
import serial


def crc_ibm0(data):
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ 0xa001 if value & 1 else value >> 1
    return value.to_bytes(2, 'little')


class HexParser:
    def __init__(self, protocol, crc):
        self.size = 6 if protocol == 'hex6' else 8
        self.crc = crc
        self.buffer = bytearray()
        self.skipped = 0
        self.crc_order = None
        self.crc_profile = None
        self.pending_profile = None
        self.streak = 0

    def feed(self, data):
        self.buffer.extend(data)
        values = []
        while len(self.buffer) >= self.size:
            frame = bytes(self.buffer[:self.size])
            signature = None
            checks = [("modbus-ffff", self.crc), ("ibm-0000", crc_ibm0)]
            for profile, checksum in checks:
                if self.crc_profile and profile != self.crc_profile:
                    continue
                # An all-zero stream can also be a UART break, not a validated frame.
                if profile == "ibm-0000" and not self.crc_profile and not any(frame[:-2]):
                    continue
                expected = checksum(frame[:-2])
                order = 'low-first' if expected == frame[-2:] else 'high-first' if expected[::-1] == frame[-2:] else None
                if order and (not self.crc_order or order == self.crc_order):
                    signature = (profile, order)
                    break
            if signature is None:
                del self.buffer[0]
                self.skipped += 1
                self.streak = 0
                self.pending_profile = None
                continue
            del self.buffer[:self.size]
            if self.pending_profile != signature:
                self.streak = 0
            self.pending_profile = signature
            self.streak += 1
            if self.streak >= 3:
                self.crc_profile, self.crc_order = signature
            if self.size == 6:
                torque = int.from_bytes(frame[:2], 'big')
                if frame[2] & 0x80:
                    torque = -torque
                speed = ((frame[2] & 0x7f) << 8) | frame[3]
            else:
                torque = int.from_bytes(frame[:3], 'big', signed=True)
                speed = int.from_bytes(frame[3:6], 'big')
            values.append((torque, speed, frame.hex(' ')))
        return values


class HexSensorChannel:
    def __init__(self, crc):
        self.crc = crc
        self.lock = threading.RLock()
        self.transport = None
        self.config = None
        self.snapshot = None
        self.error = ''
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.thread = None
        self.last_at = 0
        self.frames = deque(maxlen=60000)

    @property
    def connected(self):
        return bool(self.ready.is_set() and self.transport and self.transport.is_open and self.snapshot and
                    not self.stop.is_set() and time.monotonic() - self.last_at < 1)

    def status(self):
        with self.lock:
            return {'connected': self.connected, 'config': dict(self.config) if self.config else None,
                    'snapshot': dict(self.snapshot) if self.connected else None, 'error': self.error}

    def connect(self, config):
        self.disconnect()
        self.config = dict(config)
        self.error = ''
        self.frames.clear()
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.transport = serial.Serial(config['device'], baudrate=config['baud'], bytesize=8,
                                       parity='N', stopbits=config['stopbits'], timeout=.02)
        self.thread = threading.Thread(target=self._receive, daemon=True)
        self.thread.start()
        if not self.ready.wait(30) or not self.connected:
            error = self.error or '未收到连续有效 HEX 帧；请在传感器面板设置 HEX 模式、115200 波特率及正确停止位'
            self.disconnect()
            self.error = error
            raise ValueError(error)
        return self.status()

    def _receive(self):
        parser = HexParser(self.config['protocol'], self.crc)
        total = 0
        rate_at = time.monotonic()
        rate_count = 0
        rate = 0
        started = rate_at
        try:
            while not self.stop.is_set():
                chunk = self.transport.read(max(1, min(self.transport.in_waiting, 65536)))
                if not chunk:
                    if self.ready.is_set() and time.monotonic() - self.last_at > 1:
                        raise ValueError('HEX 主动上传超过 1 秒无有效帧，连接已断开')
                    if not self.ready.is_set() and time.monotonic() - started > 30:
                        break
                    continue
                decoded = parser.feed(chunk)
                now = time.monotonic()
                wall = time.time()
                if not decoded:
                    if now - (self.last_at or started) > (2 if self.ready.is_set() else 30):
                        raise ValueError('HEX CRC 连续无效；请核对通信模式与串口格式')
                    continue
                total += len(decoded)
                if now - rate_at >= 1:
                    rate = (total - rate_count) / (now - rate_at)
                    rate_at, rate_count = now, total
                with self.lock:
                    for offset, (torque, speed, frame) in enumerate(decoded):
                        self.frames.append((total - len(decoded) + offset + 1, wall, torque, speed))
                    torque, speed, frame = decoded[-1]
                    self.last_at = now
                    self.snapshot = {'rawTorque': torque, 'rawSpeed': speed,
                        'torque': torque / 10 ** self.config['torqueDecimals'],
                        'speed': speed / 10 ** self.config['speedDecimals'],
                        'torqueDecimals': self.config['torqueDecimals'], 'speedDecimals': self.config['speedDecimals'],
                        'receivedAt': wall, 'config': dict(self.config), 'framesReceived': total,
                        'frameRateHz': round(rate, 1), 'targetRateHz': 1000,
                        'crcOrder': parser.crc_order, 'crcProfile': parser.crc_profile, 'resyncBytes': parser.skipped,
                        'bufferedFrames': len(self.frames), 'logs': [{'tx': '', 'rx': frame}]}
                    if parser.streak >= 3:
                        self.ready.set()
        except Exception as error:
            with self.lock:
                self.error = str(error)
        finally:
            self.stop.set()
            if self.transport:
                self.transport.close()
            self.ready.set()

    def read(self):
        # Browser refresh consumes cached data; serial acquisition continues independently.
        return self.status()

    def disconnect(self):
        self.stop.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(.5)
        if self.transport:
            self.transport.close()
        self.transport = None
        self.snapshot = None
        return self.status()

    def batch(self, since):
        with self.lock:
            reset = bool(self.frames and since > self.frames[-1][0])
            cursor = 0 if reset else since
            # Only visit new frames; do not copy/scan the entire 60k ring per request.
            selected = list(takewhile(lambda frame: frame[0] > cursor, reversed(self.frames)))
            selected.reverse()
            return {**self.status(), 'frames': selected, 'reset': reset,
                    'dropped': max(0, self.frames[0][0] - cursor - 1) if self.frames and cursor else 0}

    def capture_csv(self):
        with self.lock:
            frames = list(self.frames)
            config = dict(self.config or {})
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(['sequence', 'pc_received_unix_s', 'raw_torque', 'raw_speed', 'torque_Nm', 'speed_rpm'])
        for seq, received, torque, speed in frames:
            writer.writerow([seq, received, torque, speed, torque/10**config.get('torqueDecimals', 2),
                             speed/10**config.get('speedDecimals', 1)])
        return out.getvalue()
