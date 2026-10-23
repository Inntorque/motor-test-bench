"""Loopback-only Modbus RTU bridge for independent RU and SSJ PSU channels.

RU follows the supplied RU-series protocol; SSJ keeps the working SSJ_Control map.
Each PSU owns its own transport, lock, address, and read state. A TCP endpoint
is treated as a transparent RTU tunnel, not as Modbus TCP.
"""

from __future__ import annotations

import json
import math
import secrets
import socket
import struct
import threading
import time
import signal
from contextlib import contextmanager
from collections import deque
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import serial
from serial.tools import list_ports
from motor_current import CurrentMotor
from sensor_hex import HexSensorChannel
from specialized_tests import SpecializedTests
from automatic_tests import AutomaticTests
from workbench_recordings import WorkbenchRecordings
from background_recording import BackgroundRecording
from brake_current import BrakeCurrent
from device_connections import ConnectionManager
from coupled_current import CoupledCurrent
from safety_control import SafetyControl
from power_limits import MOTOR_SUPPLY_CURRENT_LIMIT, MOTOR_SUPPLY_PRESET_CURRENT_A, power_problem, power_evidence


HOST = "127.0.0.1"
PORT = 8766
CHANNEL_NAMES = {"motor-supply": "电机电源", "brake-supply": "制动电源"}
ALLOWED_ORIGINS = {
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
}
TOKEN = secrets.token_urlsafe(32)
CURRENT_MOTOR = CurrentMotor()


class BridgeError(ValueError):
    """An error that is safe to return to the local UI."""


class PowerOperationCancelled(BridgeError):
    """An OFF/stop preempted this transaction; not a failed device reply."""
    operation_cancelled = True


class DeviceLinkError(BridgeError):
    """The configured endpoint did not return a valid RTU response."""


def crc16(data: bytes) -> bytes:
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ 0xA001 if value & 1 else value >> 1
    return struct.pack("<H", value)


def finite_number(data, key, low, high, *, optional=False):
    value = data.get(key)
    if optional and value in (None, ""):
        return None
    if isinstance(value, bool):
        raise BridgeError(f"{key} 参数无效")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise BridgeError(f"{key} 参数无效") from None
    if not math.isfinite(value) or not low <= value <= high:
        raise BridgeError(f"{key} 参数超出范围")
    return value


def scale_config(data):
    voltage_decimals = finite_number(data, "voltageDecimals", 0, 4)
    current_decimals = finite_number(data, "currentDecimals", 0, 4)
    if int(voltage_decimals) != voltage_decimals or int(current_decimals) != current_decimals:
        raise BridgeError("电压和电流小数位必须为整数")
    return 10 ** int(voltage_decimals), 10 ** int(current_decimals)


class TcpRtuTransport:
    """Raw TCP socket transport for a gateway configured as an RTU tunnel."""

    def __init__(self, host: str, port: int):
        self.socket = socket.create_connection((host, port), timeout=3)
        self.socket.settimeout(0.1)
        self.is_open = True

    def reset_input_buffer(self):
        self.socket.setblocking(False)
        try:
            while self.socket.recv(4096):
                pass
        except (BlockingIOError, OSError):
            pass
        finally:
            if self.is_open:
                self.socket.setblocking(True)
                self.socket.settimeout(0.1)

    def write(self, data: bytes):
        self.socket.sendall(data)

    def read(self, count: int):
        try:
            return self.socket.recv(count)
        except socket.timeout:
            return b""

    def close(self):
        if self.is_open:
            self.is_open = False
            self.socket.close()


class PowerSupplyChannel:
    def __init__(self, key):
        self.key = key
        self.lock = threading.RLock()
        self.transport = None
        self.config = None
        self.snapshot = None
        self.connected = False
        self.last_io = 0.0
        self.logs = deque(maxlen=12)
        self.io_epoch = 0
        self.epoch_lock = threading.Lock()
        self.io_context = threading.local()
        self.output_uncertain = False
        self.start_guard = None
        self.powered_at = 0
        self.on_power_on = None
        self.usb_identity = None
        self.verifying_power = False
        self.power_check = None

    def cancel_pending(self):
        # Lock-free invalidation: finish at most the current RTU exchange, then
        # abandon ordinary reads/writes so OFF can take the bus next.
        with self.epoch_lock:self.io_epoch += 1

    def _bound_config(self, config):
        if config.get('transport')!='serial' or not self.usb_identity:return config
        vid,pid,serial_number=self.usb_identity
        if not serial_number:raise BridgeError('原 USB 设备没有唯一序列号，禁止自动重开，请人工核对原连接')
        matches=[port for port in list_ports.comports() if (port.vid,port.pid,port.serial_number)==(vid,pid,serial_number)]
        if len(matches)!=1:raise BridgeError('原绑定的 USB 电源不在或标识重复，不能向其他串口发送关闭')
        return {**config,'device':matches[0].device}

    @contextmanager
    def transaction(self, *, emergency=False, cancel=None):
        epoch = self.io_epoch
        with self.lock:
            if not emergency and epoch != self.io_epoch:raise PowerOperationCancelled('操作已被停止取消')
            previous = getattr(self.io_context, 'epoch', None)
            previous_cancel = getattr(self.io_context,'cancel',None)
            self.io_context.epoch = None if emergency else epoch
            self.io_context.cancel = cancel
            try:yield
            finally:
                self.io_context.epoch = previous
                self.io_context.cancel = previous_cancel

    def _open_transport(self, config):
        if config["transport"] == "serial":
            try:
                return serial.Serial(
                    config["device"],
                    baudrate=config["baud"],
                    bytesize=8,
                    parity=config["parity"],
                    stopbits=config["stopbits"],
                    timeout=0.1,
                    write_timeout=1,
                )
            except serial.SerialException as error:
                raise BridgeError(f"串口打开失败：{error}") from None
        try:
            return TcpRtuTransport(config["host"], config["tcpPort"])
        except (OSError, ValueError) as error:
            raise BridgeError(f"RTU透明TCP连接失败：{error}") from None

    def _close_unlocked(self):
        if self.snapshot and self.snapshot.get('status',0) & 128:self.output_uncertain = True
        transport, self.transport = self.transport, None
        self.connected = False
        self.snapshot = None
        if transport:
            try:
                transport.close()
            except Exception:
                pass

    def disconnect(self):
        with self.lock:
            if self.output_uncertain or (self.snapshot or {}).get('status',0) & 128:
                raise BridgeError('输出仍开启或关闭未确认，请先关闭电源输出')
            self._close_unlocked()
            self.config = None
            self.logs.clear()
            return self.status()

    def status(self):
        return self.cached_status()

    def cached_status(self):
        # Snapshot objects are replaced after a complete read. Acquisition must not
        # wait on a slow serial transaction while draining the sensor frame ring.
        transport, config, snapshot = self.transport, self.config, self.snapshot
        stale = bool(snapshot and time.time()-snapshot.get('receivedAt',0)>3.5)
        return {"connected": bool(self.connected and transport and transport.is_open and not stale),
                "config": dict(config) if config else None,
                "snapshot": dict(snapshot) if snapshot else None,
                'stale':stale,'outputUncertain':self.output_uncertain,'powerCheck':self.power_check}

    def connect(self, config):
        with self.transaction():
            previous_config=self.config
            if self.output_uncertain:config=self._bound_config(config)
            changed=previous_config and (endpoint_key(config)!=endpoint_key(previous_config) or any(
                config.get(key)!=previous_config.get(key) for key in ('protocol','address','voltageDecimals','currentDecimals')))
            if self.output_uncertain and self.usb_identity and config.get('transport')=='serial':
                # A matching unique USB identity may acquire a new port number.
                changed=any(config.get(key)!=previous_config.get(key) for key in ('protocol','address','voltageDecimals','currentDecimals')) if previous_config else False
            if self.output_uncertain and changed:
                raise BridgeError('原电源关闭未确认，不可更换绑定')
            self._close_unlocked()
            self.config = config
            self.logs.clear()
            self.transport = self._open_transport(config)
            try:
                snapshot = self._read_snapshot_unlocked()
            except Exception:
                self._close_unlocked()
                self.config = previous_config
                raise
            self.connected = True
            self.snapshot = snapshot
            self.output_uncertain = bool(snapshot['status'] & 128)
            port=next((port for port in list_ports.comports() if port.device==config.get('device')),None)
            self.usb_identity=(port.vid,port.pid,port.serial_number) if port and port.vid is not None else None
            return self.status()

    def _ensure_open(self):
        if not self.transport or not self.transport.is_open:
            raise BridgeError("电源未连接")

    def _exchange(self, function, register, count=1, values=None):
        cancel=getattr(self.io_context,'cancel',None)
        if cancel and cancel.is_set():raise PowerOperationCancelled('操作已被停止取消')
        epoch = getattr(self.io_context,'epoch',None)
        if epoch is not None and epoch != self.io_epoch:raise PowerOperationCancelled('操作已被停止取消')
        self._ensure_open()
        pause = 0.15 - (time.monotonic() - self.last_io)
        if pause > 0:
            time.sleep(pause)
        if cancel and cancel.is_set():raise PowerOperationCancelled('操作已被停止取消')
        if epoch is not None and epoch != self.io_epoch:raise PowerOperationCancelled('操作已被停止取消')
        address = self.config["address"]
        if function == 16:
            if not values or not 1 <= len(values) <= 123:
                raise BridgeError("寄存器写入参数无效")
            body = struct.pack(">BBHHB", address, function, register, len(values), len(values) * 2)
            body += struct.pack(">" + "H" * len(values), *values)
        elif function in (5, 6):
            if not values or len(values) != 1:
                raise BridgeError('单点写入参数无效')
            body = struct.pack('>BBHH', address, function, register, values[0])
        elif function in (1, 2, 3, 4):
            body = struct.pack(">BBHH", address, function, register, count)
        else:
            raise BridgeError('不支持的 Modbus 功能码')
        frame = body + crc16(body)
        self.transport.reset_input_buffer()
        self.transport.write(frame)
        reply = bytearray()
        expected = None
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            reply.extend(self.transport.read(1))
            if len(reply) >= 2 and reply[1] & 0x80:
                expected = 5
            elif function in (5, 6, 16) and len(reply) >= 6:
                expected = 8
            elif function not in (5, 6, 16) and len(reply) >= 3:
                expected = 5 + reply[2]
            if expected and len(reply) >= expected:
                break
        self.last_io = time.monotonic()
        self.logs.append({"tx": frame.hex(" "), "rx": bytes(reply).hex(" ")})
        if len(reply) < 5:
            raise DeviceLinkError("电源无有效回复：检查端口、接线、地址、波特率和协议")
        if crc16(reply[:-2]) != reply[-2:]:
            raise DeviceLinkError("回复 CRC 校验失败")
        if reply[0] != address:
            raise DeviceLinkError("回复设备地址不匹配")
        if reply[1] & 0x80:
            raise BridgeError(f"Modbus 异常码 {reply[2]}")
        if reply[1] != function:
            raise DeviceLinkError("回复功能码不匹配")
        if function in (5, 6, 16):
            if bytes(reply[:6]) != body[:6]:
                raise DeviceLinkError("写入应答不匹配")
            return True
        if function in (1, 2):
            byte_count = (count + 7) // 8
            if reply[2] != byte_count or len(reply) != 5 + byte_count:
                raise DeviceLinkError('位读取回复长度不匹配')
            return [bool(reply[3+i//8] & (1 << (i%8))) for i in range(count)]
        if reply[2] != count * 2 or len(reply) != 5 + count * 2:
            raise DeviceLinkError("回复长度不匹配")
        return list(struct.unpack(">" + "H" * count, reply[3:-2]))

    def _read_snapshot_unlocked(self):
        if self.config.get('protocol') == 'ru':
            return self._read_ru_snapshot_unlocked()
        measured = self._exchange(4, 1000, 3)
        status = self._exchange(4, 1007)[0]
        settings = self._exchange(3, 2000, 6)
        # Motor SSJ readback is 0.001 A, confirmed against the physical panel.
        # Do not infer a change to the independently configured write scaling.
        actual_scale = 10**self.config.get('measuredCurrentDecimals', self.config['currentDecimals'])
        calculated = (measured[0]/10**self.config['voltageDecimals'] * measured[1]/actual_scale
                      if self.key == 'motor-supply' else None)
        return {
            "measured": measured,  # original registers, including device power
            "calculatedPowerW": calculated,
            "powerCalculated": calculated is not None,
            "status": status,
            "settings": settings,
            "connected": True,
            "config": dict(self.config),
            "logs": list(self.logs),
            "receivedAt": time.time(),
        }

    def _read_ru_snapshot_unlocked(self):
        if not self.config.get('ratingsVerified'):
            decimals = self._exchange(4, 106, 2)
            if any(value not in range(5) for value in decimals):
                raise DeviceLinkError('RU 电源返回的小数位无效')
            ratings = self._exchange(4, 103, 2)
            if any(value <= 0 for value in ratings):
                raise DeviceLinkError('RU 电源返回的额定电压/电流无效')
            self.config.update(voltageDecimals=decimals[0], currentDecimals=decimals[1], measuredCurrentDecimals=decimals[1],
                               ratedVoltage=ratings[0]/10**decimals[0], ratedCurrent=ratings[1]/10**decimals[1],
                               ratingsVerified=True)
            self._validate_limits(self.config.get('maxVoltage'), self.config.get('maxCurrent'))
        measured = self._exchange(4, 100, 2)
        output = self._exchange(1, 133, 1)[0]
        cc, cv = self._exchange(2, 116, 2)
        ovp, ocp, otp = self._exchange(2, 120, 3)
        settings = self._exchange(3, 149, 2)
        protection = self._exchange(3, 157, 2)
        enabled = self._exchange(1, 136, 2)
        scale_v, scale_a = scale_config(self.config)
        return {'measured':[measured[0],measured[1],measured[0]/scale_v*measured[1]/scale_a],
                'powerCalculated':True, 'status':128 if output else 0,
                'settings':[self.config['address'], *settings],
                'ruStatus':{'cc':cc,'cv':cv,'ovp':ovp,'ocp':ocp,'otp':otp},
                'protection':{'voltage':protection[0]/scale_v,'current':protection[1]/scale_a,
                              'ovpEnabled':enabled[0],'ocpEnabled':enabled[1]},
                'connected':True,'config':dict(self.config),'logs':list(self.logs),'receivedAt':time.time()}

    def _validate_limits(self, voltage, current):
        for value, rated, name in ((voltage,self.config.get('ratedVoltage'),'电压'),
                                   (current,self.config.get('ratedCurrent'),'电流')):
            if value is not None and (not math.isfinite(value) or value < 0 or (rated is not None and value > rated)):
                raise BridgeError(f'{name}最大限幅无效或超过电源额定值')

    def set_limits(self, data):
        with self.lock:
            self._ensure_open()
            if self.key != 'motor-supply':
                raise BridgeError('仅电机电源支持此工作台限幅设置')
            voltage = finite_number(data,'maxVoltage',0,65535)
            current = finite_number(data,'maxCurrent',0,65535)
            self._validate_limits(voltage,current)
            try:
                snapshot = self._read_snapshot_unlocked()
            except DeviceLinkError:
                self._close_unlocked()
                raise
            except BridgeError:
                raise
            except Exception:
                self._close_unlocked()
                raise
            scale_v,scale_a=scale_config(self.config)
            if snapshot['status'] & 0x80 and (snapshot['settings'][1]/scale_v>voltage or snapshot['settings'][2]/scale_a>current or snapshot['measured'][0]/scale_v>voltage or snapshot['measured'][1]/10**self.config.get('measuredCurrentDecimals',self.config['currentDecimals'])>current):
                raise BridgeError('当前输出或设定高于新限幅，请先降低设定或关闭输出')
            self.config.update(maxVoltage=voltage,maxCurrent=current)
            snapshot['config']=dict(self.config)
            self.snapshot=snapshot
            return self.status()

    def read(self):
        with self.transaction():
            self._ensure_open()
            try:
                result = self._read_snapshot_unlocked()
                issue=power_problem(self.key,self.config,result)
                if issue and issue['kind']!='hardware':
                    # Read independently on the same verified transport, without
                    # re-opening, reconfiguring or re-enabling either output.
                    self.verifying_power=True
                    first=power_evidence(result)
                    first_issue=issue
                    self.power_check=dict(state='verifying',first=first,issue=issue,firstIssue=first_issue)
                    result=self._read_snapshot_unlocked()
                    issue=power_problem(self.key,self.config,result)
                    self.power_check=dict(state='confirmed' if issue else 'recovered',
                                          first=first,second=power_evidence(result),issue=issue,firstIssue=first_issue)
                elif issue:
                    self.power_check=dict(state='device_protection',first=power_evidence(result),issue=issue)
            except Exception:
                self.verifying_power=False
                if getattr(self.io_context,'epoch',None) != self.io_epoch:raise
                self._close_unlocked()
                raise
            self.connected = True
            self.snapshot = result
            self.output_uncertain = bool(result['status'] & 128)
            self.verifying_power=False
            return self.status()

    def _settings_write_unlocked(self, voltage, current, *, verify=True):
        scale_v, scale_a = scale_config(self.config)
        if not math.isfinite(voltage) or not math.isfinite(current) or voltage < 0 or current < 0:
            raise BridgeError("请填写有效的非负电压和电流限值")
        rated_v = self.config.get("ratedVoltage")
        rated_a = self.config.get("ratedCurrent")
        if rated_v is not None and voltage > rated_v:
            raise BridgeError("电压超过已填写的电源额定上限")
        if rated_a is not None and current > rated_a:
            raise BridgeError("电流超过已填写的电源额定上限")
        if self.config.get('maxVoltage') is not None and voltage > self.config['maxVoltage']:
            raise BridgeError('输出电压超过工作台最大限幅')
        if self.config.get('maxCurrent') is not None and current > self.config['maxCurrent']:
            raise BridgeError('输出电流超过工作台最大限幅')
        raw_v, raw_a = round(voltage * scale_v), round(current * scale_a)
        if raw_v > 65535 or raw_a > 65535:
            raise BridgeError("V/A 数值超出电源寄存器范围")
        if raw_v/scale_v > min(value for value in (rated_v,self.config.get('maxVoltage'),65535/scale_v) if value is not None) or raw_a/scale_a > min(value for value in (rated_a,self.config.get('maxCurrent'),65535/scale_a) if value is not None):
            raise BridgeError('数值按电源精度取整后超过限幅，请降低设定')
        register = 149 if self.config.get('protocol') == 'ru' else 2001
        self._exchange(16, register, values=[raw_v, raw_a])
        actual = self._exchange(3, register, 2) if verify else [raw_v, raw_a]
        if actual != [raw_v, raw_a]:
            raise BridgeError("V/A 读回与设定不一致")
        return [raw_v, raw_a]

    def set_values(self, data, *, cancel=None):
        with self.transaction(cancel=cancel):
            if self.start_guard:self.start_guard()
            self._ensure_open()
            voltage = finite_number(data, "voltage", 0, 65535)
            current = finite_number(data, "current", 0, 65535)
            try:
                # The SSJ snapshot already reads 2001/2002. Use that real
                # readback to verify the write instead of reading them twice.
                ssj = self.config.get('protocol', 'ssj') == 'ssj'
                expected = self._settings_write_unlocked(voltage, current, verify=not ssj)
                result = self._read_snapshot_unlocked()
                if ssj and result['settings'][1:3] != expected:
                    raise BridgeError("V/A 读回与设定不一致")
            except DeviceLinkError:
                self._close_unlocked()
                raise
            except BridgeError:
                raise
            except Exception:
                self._close_unlocked()
                raise
            self.connected = True
            self.snapshot = result
            return result

    def set_output(self, enabled, data=None, *, cancel=None):
        if type(enabled) is not bool:
            raise BridgeError("输出开关参数无效")
        if not enabled:self.cancel_pending()
        with self.transaction(emergency=not enabled,cancel=cancel if enabled else None):
            # A failed read may have closed the handle; retain the verified
            # binding and reconnect only to send OFF, never an implicit ON.
            if not enabled and (not self.transport or not self.transport.is_open) and self.config:
                self.config=self._bound_config(self.config)
                self.transport = self._open_transport(self.config)
            self._ensure_open()
            try:
                if enabled:
                    if self.start_guard:self.start_guard()
                    if not isinstance(data, dict):
                        raise BridgeError("开启输出前必须填写电压与电流限值")
                    voltage = finite_number(data, "voltage", 0, 65535)
                    current = finite_number(data, "current", 0, 65535)
                    if self.config.get('protocol') == 'ru' or self.key == 'motor-supply':
                        fresh=self._read_snapshot_unlocked()
                        # Publish the completed preflight read while the command
                        # holds the serial lock, so the independent stale-link
                        # guard can see real feedback during ON verification.
                        self.snapshot=fresh
                    if self.config.get('protocol') == 'ru':
                        if any(fresh['ruStatus'][key] for key in ('ovp','ocp','otp')):
                            raise BridgeError('RU 电源保护已触发，请检查设备后再开启')
                    if self.key == 'motor-supply':
                        # Preserve the operator's panel preset. ON must never
                        # silently rewrite V/A (old scaling changed 20 A to 2 A).
                        sv,sa=scale_config(self.config)
                        if (abs(fresh['settings'][1]-round(voltage*sv))>1
                                or abs(fresh['settings'][2]-round(current*sa))>1):
                            raise BridgeError(f"电机电源当前预设 {fresh['settings'][1]/sv:g} V / {fresh['settings'][2]/sa:g} A；请在面板设为 {voltage:g} V / {current:g} A。软件未改写预设，未开启输出")
                    else:
                        self._settings_write_unlocked(voltage, current)
                    self.output_uncertain = True
                if self.config.get('protocol') == 'ru':
                    if enabled:self.powered_at=time.time()
                    self._exchange(5, 133, values=[0xFF00 if enabled else 0])
                else:
                    if enabled:self.powered_at=time.time()
                    self._exchange(16, 2016, values=[65535 if enabled else 0])
                result = self._read_snapshot_unlocked()
            except DeviceLinkError:
                self._close_unlocked()
                raise
            except BridgeError:
                raise
            except Exception:
                self._close_unlocked()
                raise
            self.connected = True
            self.snapshot = result
            actual = bool(result["status"] & 0x80)
            if actual != enabled:
                raise BridgeError("电源通信正常，但实际输出状态与请求不一致；请以读回状态为准")
            self.output_uncertain = actual
            result['voltageDischarged'] = not actual and result['measured'][0]/10**self.config['voltageDecimals'] <= .5
            if actual and self.on_power_on:self.on_power_on()
            return result


class TorqueSensorChannel:
    """Read-only Modbus RTU adapter for the EVG-D200 dynamic torque sensor."""

    def __init__(self):
        self.lock = threading.RLock()
        self.transport = None
        self.config = None
        self.snapshot = None
        self.connected = False
        self.logs = deque(maxlen=12)
        self.last_frame_at = 0.0

    def _close_unlocked(self):
        transport, self.transport = self.transport, None
        self.connected = False
        self.snapshot = None
        if transport:
            try:
                transport.close()
            except Exception:
                pass

    def disconnect(self):
        with self.lock:
            self._close_unlocked()
            self.config = None
            self.logs.clear()
            return self.status()

    def status(self):
        with self.lock:
            return {
                "connected": bool(self.connected and self.transport and self.transport.is_open),
                "config": dict(self.config) if self.config else None,
                "snapshot": dict(self.snapshot) if self.snapshot else None,
            }

    def connect(self, config):
        with self.lock:
            self._close_unlocked()
            self.config = config
            self.logs.clear()
            try:
                self.transport = serial.Serial(
                    config["device"],
                    baudrate=config["baud"],
                    bytesize=8,
                    parity="N",
                    stopbits=config["stopbits"],
                    timeout=0.05,
                    write_timeout=1,
                )
                mode = self._read_int32_unlocked(0x1C)
                if mode != 1:
                    raise DeviceLinkError("传感器当前不是 Modbus RTU 模式；请在设备面板将通信模式设为 1 后重试")
                device_stopbits = self._read_int32_unlocked(0x18)
                expected_stopbits = 0 if config["stopbits"] == 2 else 1
                if device_stopbits != expected_stopbits:
                    raise DeviceLinkError("传感器停止位读回与所选串口设置不符；请根据手册冲突项调整 1/2 位后重试")
                torque_decimals = self._read_int32_unlocked(0x08)
                speed_decimals = self._read_int32_unlocked(0x20)
                if not 0 <= torque_decimals <= 4:
                    raise DeviceLinkError("传感器扭矩小数位应为 0–4，缩放参数无效")
                if not 0 <= speed_decimals <= 3:
                    raise DeviceLinkError("传感器转速小数位应为 0–3，缩放参数无效")
                snapshot = self._read_measurements_unlocked(torque_decimals, speed_decimals)
            except serial.SerialException as error:
                self._close_unlocked()
                self.config = None
                raise BridgeError(f"传感器串口打开失败：{error}") from None
            except Exception:
                self._close_unlocked()
                self.config = None
                raise
            self.connected = True
            self.snapshot = snapshot
            return self.status()

    def _ensure_open(self):
        if not self.transport or not self.transport.is_open:
            raise BridgeError("动态扭矩传感器未连接")

    def _exchange(self, register, count=2):
        self._ensure_open()
        address = self.config["address"]
        body = struct.pack(">BBHH", address, 3, register, count)
        frame = body + crc16(body)
        # Preserve an RTU silent interval between complete response/request frames.
        silent_s = max(0.0035, 3.5 * (9 + self.config["stopbits"]) / self.config["baud"])
        remaining = silent_s - (time.monotonic() - self.last_frame_at)
        if remaining > 0:
            time.sleep(remaining)
        self.transport.reset_input_buffer()
        self.transport.write(frame)
        reply = bytearray()
        expected = None
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            reply.extend(self.transport.read(1))
            if len(reply) >= 2 and reply[1] & 0x80:
                expected = 5
            elif len(reply) >= 3:
                expected = 5 + reply[2]
            if expected and len(reply) >= expected:
                break
        self.last_frame_at = time.monotonic()
        self.logs.append({"tx": frame.hex(" "), "rx": bytes(reply).hex(" ")})
        if len(reply) < 5:
            raise DeviceLinkError(f"传感器无有效回复：检查 RS485 A/B、地址、{self.config['baud']} 波特率和停止位")
        if crc16(reply[:-2]) != reply[-2:]:
            raise DeviceLinkError("传感器回复 CRC 校验失败")
        if reply[0] != address:
            raise DeviceLinkError("传感器回复设备地址不匹配")
        if reply[1] & 0x80:
            raise DeviceLinkError(f"传感器 Modbus 异常码 {reply[2]}")
        if reply[1] != 3:
            raise DeviceLinkError("传感器回复功能码不匹配")
        if reply[2] != count * 2 or len(reply) != 5 + count * 2:
            raise DeviceLinkError("传感器回复寄存器数量或长度不匹配")
        return bytes(reply[3:-2])

    def _read_int32_unlocked(self, register):
        return struct.unpack(">i", self._exchange(register, 2))[0]

    def _read_measurements_unlocked(self, torque_decimals, speed_decimals):
        # Real EVG-D200 verified: 0x00..0x03 yields both signed 32-bit values.
        raw_torque, raw_speed = struct.unpack(">ii", self._exchange(0x00, 4))
        torque = raw_torque / (10 ** torque_decimals)
        speed = raw_speed / (10 ** speed_decimals)
        return {
            "rawTorque": raw_torque,
            "rawSpeed": raw_speed,
            "torque": torque,
            "speed": speed,
            "torqueDecimals": torque_decimals,
            "speedDecimals": speed_decimals,
            "receivedAt": time.time(),
            "config": dict(self.config),
            "logs": list(self.logs),
        }

    def read(self):
        with self.lock:
            self._ensure_open()
            try:
                result = self._read_measurements_unlocked(
                    self.snapshot["torqueDecimals"], self.snapshot["speedDecimals"]
                )
            except Exception:
                self._close_unlocked()
                raise
            self.connected = True
            self.snapshot = result
            return self.status()


CHANNELS = {key: PowerSupplyChannel(key) for key in CHANNEL_NAMES}
CHANNELS_LOCK = threading.Lock()
class SensorChannel:
    def __init__(self):
        self.channel = TorqueSensorChannel()

    def __getattr__(self, name):
        return getattr(self.channel, name)

    def connect(self, config):
        self.channel.disconnect()
        self.channel = HexSensorChannel(crc16) if config.get("protocol") in {"hex6", "hex8"} else TorqueSensorChannel()
        return self.channel.connect(config)


TORQUE_SENSOR = SensorChannel()
SPECIALIZED = SpecializedTests(
    Path(__file__).resolve().parent / 'recordings' / 'specialized',
    lambda: {'sensor': TORQUE_SENSOR.status(), 'motor': CURRENT_MOTOR.status(), 'brake': CHANNELS['brake-supply'].status()},
    lambda since: TORQUE_SENSOR.batch(since) if isinstance(TORQUE_SENSOR.channel, HexSensorChannel) else None,
)
AUTOMATIC = AutomaticTests(Path(__file__).resolve().parent / 'recordings' / 'automatic',
                           CURRENT_MOTOR, SPECIALIZED,
                           lambda: {'sensor': TORQUE_SENSOR.status(), 'motor': CURRENT_MOTOR.status(),
                                    'brake': CHANNELS['brake-supply'].cached_status(),
                                    'motorSupply': CHANNELS['motor-supply'].cached_status()},
                           lambda: [channel.read() for channel in CHANNELS.values()])
WORKBENCH_RECORDINGS = WorkbenchRecordings(Path(__file__).resolve().parent / 'recordings' / 'workbench',
                                          AUTOMATIC.source, SPECIALIZED.frames)
CURRENT_MOTOR.write_observer = lambda route,reply: WORKBENCH_RECORDINGS.observe('motor_command_ack', {'route':route, 'response':reply})
BACKGROUND_RECORDING = BackgroundRecording(Path(__file__).resolve().parent/'recordings'/'background',
                                           AUTOMATIC.source, SPECIALIZED.frames)
WORKBENCH_RECORDINGS.diagnostics = BACKGROUND_RECORDING
CURRENT_MOTOR.diagnostic_observer = BACKGROUND_RECORDING.observe
def observe_brake(kind, payload):
    BACKGROUND_RECORDING.observe(kind, payload)
    WORKBENCH_RECORDINGS.observe(kind, payload)
BRAKE_CURRENT = BrakeCurrent(CHANNELS['brake-supply'], observe_brake)
COUPLED_CURRENT = CoupledCurrent(CURRENT_MOTOR, BRAKE_CURRENT, TORQUE_SENSOR, CHANNELS['motor-supply'],
    lambda: AUTOMATIC.active() or CONNECTIONS.active(), observe_brake,recovery=True,recorder=WORKBENCH_RECORDINGS)



def normalized_config(data, key=None):
    if not isinstance(data, dict):
        raise BridgeError("连接参数无效")
    transport = data.get("transport", "serial")
    protocol = data.get('protocol', 'ru' if key == 'motor-supply' else 'ssj')
    if protocol not in ('ru','ssj') or (key == 'brake-supply' and protocol != 'ssj'):
        raise BridgeError('电源协议无效')
    if protocol == 'ru':
        data={'address':1,'baud':9600,'stopbits':1,'voltageDecimals':2,'currentDecimals':2,**data}
    if transport not in ("serial", "tcp"):
        raise BridgeError("不支持的传输方式")
    address = finite_number(data, "address", 1, 255 if protocol=='ru' else 247)
    voltage_decimals = finite_number(data, "voltageDecimals", 0, 4)
    current_decimals = finite_number(data, "currentDecimals", 0, 4)
    if any(int(value) != value for value in (address, voltage_decimals, current_decimals)):
        raise BridgeError("设备地址和小数位必须为整数")
    config = {
        "transport": transport,
        'protocol':protocol,
        "address": int(address),
        "voltageDecimals": int(voltage_decimals),
        "currentDecimals": int(current_decimals),
        "ratedVoltage": finite_number(data, "ratedVoltage", 0, 65535, optional=True),
        "ratedCurrent": finite_number(data, "ratedCurrent", 0, 65535, optional=True),
        'maxVoltage':finite_number(data,'maxVoltage',0,65535,optional=True),
        'maxCurrent':finite_number(data,'maxCurrent',0,65535,optional=True),
    }
    if key in CHANNEL_NAMES:
        # 2026-10-21 panel confirmation: SSJ setpoint raw2000 = 2.000 A.
        # Migrate the old two-decimal host profile without writing the device.
        if protocol=='ssj' and key=='motor-supply' and config['currentDecimals']==2:
            config['currentDecimals']=3
        config['measuredCurrentDecimals']=3 if protocol=='ssj' and key=='motor-supply' else config['currentDecimals']
        config['maxVoltage']=24
        config['maxCurrent']=MOTOR_SUPPLY_CURRENT_LIMIT if key=='motor-supply' else 2.3
        if protocol=='ssj':
            required=3
            if config['voltageDecimals']!=2 or config['currentDecimals']!=required:
                raise BridgeError(f'当前 SSJ 接线固定电压小数位 2、电流小数位 {required}，请核对设备后连接')
    if transport == "serial":
        device = data.get("device")
        if not isinstance(device, str) or not device.strip() or len(device) > 256:
            raise BridgeError("请选择有效的串口")
        baud = finite_number(data, "baud", 1200, 128000 if protocol=='ru' else 115200)
        allowed_baud={1200,2400,4800,9600,19200,38400,57600,115200}
        if protocol=='ru':allowed_baud.update({14400,43000,76800,128000})
        if int(baud) != baud or int(baud) not in allowed_baud:
            raise BridgeError("波特率无效")
        parity = data.get("parity", "N")
        stopbits = finite_number(data, "stopbits", 1, 2)
        if parity not in {"N", "E", "O"} or stopbits not in {1, 2}:
            raise BridgeError("串口校验或停止位无效")
        if protocol=='ru' and (parity!='N' or stopbits!=1):
            raise BridgeError('RU 手册要求 8N1：无校验、1 位停止位')
        config.update({"device": device.strip(), "baud": int(baud), "parity": parity, "stopbits": int(stopbits)})
    else:
        host = data.get("host")
        tcp_port = finite_number(data, "tcpPort", 1, 65535)
        if not isinstance(host, str) or not host.strip() or len(host) > 253:
            raise BridgeError("请填写网关 IP 地址或主机名")
        if int(tcp_port) != tcp_port:
            raise BridgeError("网关 TCP 端口必须为整数")
        config.update({"host": host.strip(), "tcpPort": int(tcp_port)})
    return config


def normalized_sensor_config(data):
    if not isinstance(data, dict):
        raise BridgeError("传感器连接参数无效")
    device = data.get("device")
    if not isinstance(device, str) or not device.strip() or len(device) > 256:
        raise BridgeError("请选择动态扭矩传感器的 USB-RS485 串口")
    protocol = data.get("protocol", "modbus")
    if protocol in {"hex6", "hex8"}:
        baud = finite_number(data, "baud", 9600, 115200)
        stopbits = finite_number(data, "stopbits", 1, 2)
        torque_decimals = finite_number(data, "torqueDecimals", 0, 4)
        speed_decimals = finite_number(data, "speedDecimals", 0, 3)
        if baud not in {9600, 14400, 19200, 38400, 57600, 115200} or stopbits not in {1, 2} or any(int(value) != value for value in (torque_decimals, speed_decimals)):
            raise BridgeError("HEX 串口参数或小数位无效")
        return {"transport": "serial", "protocol": protocol, "device": device.strip(), "baud": int(baud),
                "parity": "N", "bytesize": 8, "stopbits": int(stopbits),
                "torqueDecimals": int(torque_decimals), "speedDecimals": int(speed_decimals)}
    if protocol != "modbus":
        raise BridgeError("未知传感器协议")
    address = finite_number(data, "address", 1, 120)
    baud = finite_number(data, "baud", 1200, 19200)
    stopbits = finite_number(data, "stopbits", 1, 2)
    if int(address) != address or int(baud) != baud or int(stopbits) != stopbits:
        raise BridgeError("传感器地址、波特率和停止位必须为整数")
    if int(baud) not in {1200, 2400, 4800, 9600, 14400, 19200}:
        raise BridgeError("传感器波特率无效；Modbus RTU 首版限制为 19200 bps 及以下")
    return {
        "transport": "serial",
        "device": device.strip(),
        "baud": int(baud),
        "address": int(address),
        "parity": "N",
        "stopbits": int(stopbits),
        "bytesize": 8,
        "protocol": "Modbus RTU read-only",
    }


def endpoint_key(config):
    if config["transport"] == "serial":
        return ("serial", config["device"].casefold())
    return ("tcp", config["host"].casefold(), config["tcpPort"])


def validate_supply_policy(key, data, *, limits=False):
    """Workbench policy only; protocol drivers remain reusable. Never does I/O."""
    if not isinstance(data, dict):
        raise BridgeError('电源设定无效')
    if limits:
        voltage=finite_number(data,'maxVoltage',0,65535)
        current=finite_number(data,'maxCurrent',0,65535)
        if key=='motor-supply' and (voltage!=24 or current!=MOTOR_SUPPLY_CURRENT_LIMIT):
            raise BridgeError('电机直流电源固定 24 V、20 A 限流；电机电流请在电机控制区设置')
        return
    voltage=finite_number(data,'voltage',0,65535)
    current=finite_number(data,'current',0,65535)
    if voltage!=24:
        raise BridgeError('工作台电源电压固定为 24 V')
    if key=='motor-supply' and current!=MOTOR_SUPPLY_PRESET_CURRENT_A:
        raise BridgeError(f'电机电源预设固定为 24 V / {MOTOR_SUPPLY_PRESET_CURRENT_A:g} A；开启时只核验预设，不自动改写 V/A')
    if key=='brake-supply' and current>2.3:
        raise BridgeError('制动电源电流最大允许 2.3 A')


CONNECTIONS = ConnectionManager(globals(), Path(__file__).resolve().parent / '.runtime/connections.json')
SAFETY = SafetyControl(CHANNELS,CURRENT_MOTOR,BRAKE_CURRENT,COUPLED_CURRENT,
                       Path(__file__).resolve().parent/'.runtime/safety.json')
for _channel in CHANNELS.values():_channel.start_guard=SAFETY.check
CURRENT_MOTOR.start_guard=BRAKE_CURRENT.start_guard=SAFETY.check
CURRENT_MOTOR.feedback_after=lambda:CHANNELS['motor-supply'].powered_at
CURRENT_MOTOR.output_hold=lambda:any(channel.verifying_power for channel in CHANNELS.values())
CHANNELS['motor-supply'].on_power_on=CURRENT_MOTOR.powered_on
SAFETY.automatic=AUTOMATIC
SAFETY.observe=observe_brake


class Handler(BaseHTTPRequestHandler):
    server_version = "MotorBenchBridge/1"

    def log_message(self, *_):
        pass

    def _send(self, data, code=200):
        if self.command == 'POST' and getattr(self, 'recording_request', None):
            WORKBENCH_RECORDINGS.observe('operation_response', {**self.recording_request, 'response':data, 'httpStatus':code})
            BACKGROUND_RECORDING.observe('operation_response', {**self.recording_request, 'response':data,
                'httpStatus':code, 'durationS':time.monotonic()-self.recording_started})
        elif self.command == 'POST' and code >= 400:
            BACKGROUND_RECORDING.observe('request_rejected', {'path':urlparse(self.path).path,
                                         'httpStatus':code, 'response':data})
        body = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header('X-Control-Epoch',str(SAFETY.epoch))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _valid_host(self):
        return self.headers.get("Host", "").lower() in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

    def _valid_post(self):
        origin = self.headers.get("Origin")
        return (
            self._valid_host()
            and self.headers.get("X-MotorBench-Token") == TOKEN
            and origin in (None, *ALLOWED_ORIGINS)
        )

    def do_GET(self):
        if not self._valid_host():
            return self._send({"error": "仅支持本机访问"}, 403)
        path = urlparse(self.path).path
        if path == '/api/control/status':return self._send(SAFETY.status())
        if path == '/api/connections/status':
            return self._send(CONNECTIONS.status())
        if path == '/api/coupled-current/status':
            heartbeat = not SAFETY.started and parse_qs(urlparse(self.path).query).get('heartbeat') == ['1']
            return self._send({**COUPLED_CURRENT.status(heartbeat=heartbeat),'safety':SAFETY.status()})
        if path == '/api/brake-current/status':
            return self._send({**BRAKE_CURRENT.status(heartbeat=not SAFETY.started),'safety':SAFETY.status()})
        if path == '/api/recordings/status':
            result = {'current': WORKBENCH_RECORDINGS.status()}
            if parse_qs(urlparse(self.path).query).get('history') != ['0']:
                result['records'] = WORKBENCH_RECORDINGS.history()
            return self._send(result)
        if path == "/api/specialized/status":
            return self._send({'current': SPECIALIZED.status(), 'records': SPECIALIZED.history(), 'availability': SPECIALIZED.availability()})
        if path == '/api/automatic/status':
            result = {'current': AUTOMATIC.status(), 'motor': CURRENT_MOTOR.status()}
            if parse_qs(urlparse(self.path).query).get('history') != ['0']:
                result['records'] = AUTOMATIC.history()
            return self._send(result)
        if path.startswith(('/api/specialized/files/', '/api/automatic/files/', '/api/recordings/files/')):
            try:
                parts = path.split('/')
                if len(parts) != 6:
                    raise ValueError('无效记录文件')
                manager = {'automatic':AUTOMATIC, 'specialized':SPECIALIZED, 'recordings':WORKBENCH_RECORDINGS}[parts[2]]
                file = manager.file(parts[4], parts[5])
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv; charset=utf-8' if file.suffix == '.csv' else 'application/x-ndjson; charset=utf-8' if file.suffix == '.jsonl' else 'application/json; charset=utf-8')
                self.send_header('Content-Disposition', f'attachment; filename="{parts[4]}-{file.name}"')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(file.stat().st_size))
                self.end_headers()
                with file.open('rb') as stream:
                    while chunk := stream.read(65536):
                        self.wfile.write(chunk)
                return
            except ValueError as error:
                return self._send({'error': str(error)}, 404)
        if path == "/api/sensors/torque-sensor/frames":
            if not isinstance(TORQUE_SENSOR.channel, HexSensorChannel):
                return self._send({'error': '当前未使用 HEX 采集'}, 400)
            try:
                since = max(0, int(parse_qs(urlparse(self.path).query).get('since', ['0'])[0]))
            except ValueError:
                return self._send({'error': '无效采集序号'}, 400)
            return self._send(TORQUE_SENSOR.batch(since))
        if path == "/api/sensors/torque-sensor/capture.csv":
            if not isinstance(TORQUE_SENSOR.channel, HexSensorChannel):
                return self._send({"error": "当前未使用 HEX 采集"}, 400)
            body = TORQUE_SENSOR.capture_csv().encode("utf-8-sig")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="torque-hex-raw.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/health":
            return self._send({
                "app": "motor-test-bench-hardware",
                "version": 1,
                "token": TOKEN,
                'safety':SAFETY.status(),
                "channels": {
                    **{key: channel.status() for key, channel in CHANNELS.items()},
                    "torque-sensor": TORQUE_SENSOR.status(),
                    "current-motor": CURRENT_MOTOR.status(),
                },
            })
        if path == "/api/motor-current/status":
            return self._send({**CURRENT_MOTOR.read(heartbeat=not SAFETY.started),'safety':SAFETY.status()})
        if path == "/api/ports":
            try:
                ports = [{"port": item.device, "description": item.description} for item in list_ports.comports()]
                return self._send({"ports": ports})
            except Exception as error:
                return self._send({"ports": [], "error": str(error)}, 200)
        return self._send({"error": "不存在"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path.strip('/').split('/')
        # Validate/read bounded request bodies before taking any command gate.
        if not self._valid_post():return self._send({'error':'本机页面令牌无效或来源不受允许'},403)
        try:
            self.connection.settimeout(2)
            length=int(self.headers.get('Content-Length','0'))
            if not 0<=length<=4096 or self.headers.get('Transfer-Encoding'):raise ValueError('请求体长度无效')
            self.request_data=json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(self.request_data,dict):raise ValueError('请求参数必须为对象')
        except (ValueError,OSError) as error:return self._send({'error':str(error)},400)
        if SAFETY.started:
            if path == ['api','control','release']:
                if self.headers.get('X-Workbench-Client') and self.headers.get('X-Workbench-Client')==SAFETY.owner:
                    SAFETY.stop('控制页面已离开',CHANNELS.keys())
                return self._send(SAFETY.status())
            if path == ['api','control','heartbeat']:
                return self._send(SAFETY.heartbeat(self.headers.get('X-Workbench-Client')))
            stop = len(path)==3 and path[-1]=='stop' and path[:2] in (['api','control'],['api','coupled-current'],['api','motor-current'],['api','brake-current'],['api','automatic'])
            off = len(path)==4 and path[:2]==['api','supplies'] and path[2] in CHANNELS and path[-1]=='output' and self.request_data.get('on') is False
            if stop or off:
                keys=([path[2]] if off else list(CHANNELS) if path[:2]==['api','control'] else [])
                return self._send({'safety':SAFETY.stop('操作者停止',keys)},202)
        # Readback and file recording never take the control gate or delay a stop.
        if path[-1] == 'read' or path[:2] == ['api', 'recordings']:
            return self._do_POST()
        with COUPLED_CURRENT.control:
            if SAFETY.started:
                try:
                    energizing=(path[-1]=='start' and path[:2] in (['api','coupled-current'],['api','motor-current'],['api','brake-current'],['api','automatic'])) or path==['api','coupled-current','load'] or (path[:2]==['api','supplies'] and path[-1]=='output' and self.request_data.get('on') is True)
                    restoring=path[-1]=='connect' or path==['api','connections','start']
                    keys=([path[2]] if path[:2]==['api','supplies'] else list(CHANNELS) if path[:2]==['api','coupled-current']
                          else ['brake-supply'] if path[:2]==['api','brake-current'] else ['motor-supply'])
                    SAFETY.claim(self.headers.get('X-Workbench-Client'),self.headers.get('X-Control-Epoch'),energizing=energizing,restoring=restoring,power_keys=keys)
                except (ValueError,OSError) as error:return self._send({'error':str(error),'safety':SAFETY.status()},409)
            if not COUPLED_CURRENT.blocks_controls() and (path[:3] == ['api','supplies','brake-supply'] or path[:2] == ['api','brake-current']
                    or path == ['api','automatic','start']):
                with BRAKE_CURRENT.control:
                    return self._do_POST()
            return self._do_POST()

    def _do_POST(self):
        self.recording_request = None
        if not self._valid_post():
            return self._send({"error": "本机页面令牌无效或来源不受允许，请刷新页面后重试"}, 403)
        try:
            data = self.request_data
            path = urlparse(self.path).path.strip("/").split("/")
            if COUPLED_CURRENT.blocks_controls() and path[:2] != ['api', 'coupled-current']:
                if path == ['api', 'motor-current', 'stop']:
                    COUPLED_CURRENT.stop(wait=True)
                    return self._send(CURRENT_MOTOR.status())
                if path == ['api', 'brake-current', 'stop']:
                    COUPLED_CURRENT.stop(wait=True)
                    return self._send(BRAKE_CURRENT.status())
                off = path[:2] == ['api', 'supplies'] and path[-1] == 'output' and data.get('on') is False
                restoring = not COUPLED_CURRENT.active() and (
                    path == ['api', 'connections', 'start'] or
                    (path[-1] == 'connect' and path[:2] in (['api','supplies'],['api','sensors'],['api','motor-current'])))
                if off:
                    # An emergency PSU OFF must not wait for CAN stop confirmation.
                    COUPLED_CURRENT.stop()
                elif not restoring and path[-1] != 'read' and path[:2] != ['api', 'recordings']:
                    raise BridgeError('联动试验正在运行，请使用统一停止后再单独操作')
            if path == ['api', 'connections', 'start']:
                return self._send(CONNECTIONS.start(data))
            if CONNECTIONS.active() and path[-1] not in ('read', 'stop'):
                if not (path[-1] == 'output' and data.get('on') is False):
                    raise BridgeError('正在自动连接，请等待扫描完成')
            self.recording_request = None
            if len(path) == 3 and path[:2] == ['api','recordings']:
                if path[2] == 'start':return self._send({'current':WORKBENCH_RECORDINGS.start(data)})
                if path[2] == 'stop':return self._send({'current':WORKBENCH_RECORDINGS.stop(data.get('id'))})
                raise BridgeError('未知记录操作')
            self.recording_started = time.monotonic()
            self.recording_request = {'path':urlparse(self.path).path, 'request':data,
                                      'requestId':secrets.token_hex(8),'controlEpoch':SAFETY.epoch,
                                      'controlClient':self.headers.get('X-Workbench-Client')}
            BACKGROUND_RECORDING.observe('operation_request', self.recording_request)
            WORKBENCH_RECORDINGS.observe('operation_request', self.recording_request)
            if len(path) == 3 and path[:2] == ['api', 'coupled-current']:
                try:
                    if path[2] == 'start':return self._send(COUPLED_CURRENT.start(data))
                    if path[2] == 'load':return self._send(COUPLED_CURRENT.begin_loading(data))
                    if path[2] == 'stop':return self._send(COUPLED_CURRENT.stop())
                    raise BridgeError('未知联动操作')
                except Exception as error:
                    return self._send({**COUPLED_CURRENT.status(), 'error':str(error)},400)
            if len(path) == 3 and path[:2] == ['api', 'automatic']:
                if path[2] == 'start':
                    if BRAKE_CURRENT.active():raise BridgeError('请先停止制动电流步进')
                    return self._send({'current': AUTOMATIC.start(data)})
                if path[2] == 'stop':
                    return self._send({'current': AUTOMATIC.stop(data.get('id'))})
                raise BridgeError('未知自动脚本操作')
            if len(path) == 3 and path[:2] == ['api', 'specialized']:
                with CURRENT_MOTOR.action_lock:
                    if AUTOMATIC.active():
                        raise BridgeError('自动脚本正在管理采集，请先停止自动脚本')
                    if path[2] == 'start':
                        if data.get('kind') == 'loaded_peak':
                            raise BridgeError('带载原始记录由自动脚本创建')
                        return self._send({'current': SPECIALIZED.start(data)})
                    if path[2] == 'stop':
                        return self._send({'current': SPECIALIZED.stop(data.get('id'))})
                raise BridgeError('未知专项测试操作')
            if len(path) == 3 and path[:2] == ['api','brake-current']:
                try:
                    if path[2] == 'start':
                        if AUTOMATIC.active():raise BridgeError('自动脚本正在运行，请先停止脚本')
                        return self._send(BRAKE_CURRENT.start(data))
                    if path[2] == 'stop':return self._send(BRAKE_CURRENT.stop())
                    raise BridgeError('未知制动步进操作')
                except Exception as error:
                    return self._send({**BRAKE_CURRENT.status(),'error':str(error)},400)
            if len(path) == 3 and path[:2] == ["api", "motor-current"]:
                action = path[2]
                if action not in {"connect", "start", "stop", "disconnect"}:
                    raise BridgeError("未知电流环操作")
                try:
                    result = CURRENT_MOTOR.start(data) if action == "start" else getattr(CURRENT_MOTOR, action)()
                    return self._send(result)
                except Exception as error:
                    return self._send({**CURRENT_MOTOR.status(), "error": str(error)}, 400)
            if len(path) == 4 and path[:2] == ["api", "supplies"]:
                _, _, key, action = path
                channel = CHANNELS.get(key)
                if channel is None:
                    raise BridgeError("未知电源通道")
                if key == 'brake-supply' and BRAKE_CURRENT.active() and action != 'read':
                    if action == 'output' and data.get('on') is False:
                        return self._send(BRAKE_CURRENT.stop()['device'])
                    raise BridgeError('制动电流步进正在运行，请先停止步进再手动设置或连接')
                if AUTOMATIC.active() and action != 'read':
                    if action == 'output' and data.get('on') is False:
                        CURRENT_MOTOR.stop()
                    else:
                        raise BridgeError('自动脚本运行期间不可更改电源或连接；请先停止脚本')
            elif len(path) == 4 and path[:2] == ["api", "sensors"] and path[2] == "torque-sensor":
                _, _, _, action = path
                channel = TORQUE_SENSOR
                if AUTOMATIC.active() and action != 'read':
                    raise BridgeError('自动脚本运行期间不可更改传感器连接')
                if action == "connect":
                    config = normalized_sensor_config(data)
                    with CHANNELS_LOCK:
                        connected_channels = [*CHANNELS.items(), ("torque-sensor", TORQUE_SENSOR)]
                        for other_key, other in connected_channels:
                            if other_key == "torque-sensor" or not other.connected or not other.config:
                                continue
                            if endpoint_key(other.config) == endpoint_key(config):
                                raise BridgeError(f"该串口已被{CHANNEL_NAMES.get(other_key, '另一设备')}占用，请为传感器选择独立 RS485 串口")
                        result = channel.connect(config)
                        CONNECTIONS.remember('torque-sensor', result['config'])
                    return self._send(result)
                if action == "disconnect":
                    return self._send(channel.disconnect())
                if action == "read":
                    return self._send(channel.read())
                raise BridgeError("未知传感器操作")
            else:
                raise BridgeError("未知请求")
            if action == "connect":
                config = normalized_config(data,key)
                with CHANNELS_LOCK:
                    for other_key, other in [*CHANNELS.items(), ("torque-sensor", TORQUE_SENSOR)]:
                        if other_key == key or not other.connected or not other.config:
                            continue
                        if endpoint_key(other.config) == endpoint_key(config):
                            raise BridgeError("该串口或 RTU 网关端点已被另一台电源占用，请为两台电源配置独立通道")
                    result = channel.connect(config)
                    CONNECTIONS.remember(key, result['config'])
                return self._send(result)
            if action == "disconnect":
                return self._send(channel.disconnect())
            if action == "read":
                # The ramp owns acquisition on this serial link. Browser polls
                # consume its verified snapshots, never queue extra RTU reads.
                if SAFETY.started or (key == 'brake-supply' and BRAKE_CURRENT.active()):
                    return self._send(channel.cached_status())
                return self._send(channel.read())
            if action == "set":
                if key == 'motor-supply':
                    raise BridgeError('电机电源只允许开关输出，V/A 在面板固定设定；软件不改写')
                validate_supply_policy(key,data)
                return self._send(channel.set_values(data))
            if action == 'limits':
                validate_supply_policy(key,data,limits=True)
                return self._send(channel.set_limits(data))
            if action == "output":
                if data.get('on') is True:
                    validate_supply_policy(key,data)
                    if key == 'brake-supply':
                        with channel.lock:
                            fresh = channel.read()['snapshot']
                            scale_v, scale_a = scale_config(channel.config)
                            if fresh['settings'][1:3] != [round(24 * scale_v), round(float(data['current']) * scale_a)]:
                                raise BridgeError('请先设置制动电流并确认读回，再开启制动输出')
                            return self._send(channel.set_output(True, data))
                return self._send(channel.set_output(data.get("on"), data))
            raise BridgeError("未知操作")
        except (BridgeError, ValueError, TypeError, KeyError, OSError, serial.SerialException) as error:
            message = str(error) or "设备通信失败"
            payload = {"error": message}
            parts = urlparse(self.path).path.strip("/").split("/")
            if len(parts) >= 3 and parts[:2] == ["api", "supplies"] and parts[2] in CHANNELS:
                payload.update(CHANNELS[parts[2]].status())
            return self._send(payload, 400)
        except Exception as error:
            parts = urlparse(self.path).path.strip("/").split("/")
            payload = {"error": f"设备通信失败：{error}"}
            if len(parts) >= 3 and parts[:2] == ["api", "supplies"] and parts[2] in CHANNELS:
                payload.update(CHANNELS[parts[2]].status())
            return self._send(payload, 500)


def serve():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    SPECIALIZED.recover_interrupted()
    AUTOMATIC.recover_interrupted()
    WORKBENCH_RECORDINGS.recover_interrupted()
    SAFETY.start()
    # Restore only known device identities, then reconcile any unowned outputs.
    CONNECTIONS.start({})
    def terminate(*_):
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,terminate)
    if hasattr(signal,'SIGBREAK'):signal.signal(signal.SIGBREAK,terminate)
    print(f"Motor test bench hardware bridge listening at http://{HOST}:{PORT}")
    print("Power supplies start disconnected; output state is only reported after a valid Modbus RTU response.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SAFETY.close()
        if COUPLED_CURRENT.active():
            try:COUPLED_CURRENT.stop(wait=True)
            except Exception as error:print(f'Coupled stop not confirmed: {error}')
        if BRAKE_CURRENT.active():
            try:BRAKE_CURRENT.stop()
            except Exception as error:print(f'Brake stop not confirmed: {error}')
        if WORKBENCH_RECORDINGS.active():
            try:WORKBENCH_RECORDINGS.stop(WORKBENCH_RECORDINGS.status()['id'])
            except Exception as error:print(f'Workbench recording stop: {error}')
        if AUTOMATIC.active():
            try:
                AUTOMATIC.stop(AUTOMATIC.status()['id'])
            except Exception as error:
                print(f'Automatic script stop: {error}')
        if SPECIALIZED.status():
            SPECIALIZED.stop(SPECIALIZED.status()['id'])
        if CURRENT_MOTOR.run["running"]:
            try:
                CURRENT_MOTOR.stop()
            except Exception as error:
                print(f"Motor stop not confirmed: {error}")
        for channel in [*CHANNELS.values(), TORQUE_SENSOR]:
            try:channel.disconnect()
            except Exception as error:print(f'Output state unknown; preserving safety journal: {error}')
        BACKGROUND_RECORDING.close()
        server.server_close()


if __name__ == "__main__":
    serve()
