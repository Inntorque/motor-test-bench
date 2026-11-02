#!/usr/bin/env python3
"""
foc_web.py -- local web dashboard for the N32G455REL7 FOC board.

What it does
------------
Runs a small HTTP server on 127.0.0.1 that shows, live, only what matters:

  * status bar   : CAN state (+ short reason when it is not connected), running
                   firmware version, active slot, FOC loop counter
  * live curves  : three canvas traces (ia / ib / ic in A) over a rolling
                   ~10 s window, with pause / clear controls, fed by EVERY
                   telemetry frame the firmware streams (enabled on page load
                   at 10 ms); the page pulls them in batches, not one per poll
  * control row  : telemetry, sine open-loop and encoder FOC controls; the
                   closed-loop panel exposes calibration and six control modes
  * CAN-OTA      : a manual button builds and updates this xige_foc project
  * param table  : the 8 firmware parameters (collapsed by default, per-row
                   Write, plus one global "Save to flash")

There is deliberately NO log panel.  Normal CAN traffic, heartbeats, watchdog
notes, the 1 Hz "hello world" announcement and INFO/FOC polling chatter are
counted internally but never displayed.  Only real errors surface, as one short
line in the status bar.

It is the ONLY process that may open the CAN adapter during normal dashboard
use: the adapter is opened ONCE at startup (auto-detected ZQWL USBCANFD by
default) and kept open until the process exits.  The page's CAN-OTA action first
stops the motor, then detaches this adapter before running the project updater.
Do not run another CAN host tool while the dashboard or its OTA job is using it.

Protocol used
-------------
  standard 8-byte CAN frames, 500 kbit/s.  Sources of truth:
  ../ota/ota_params.h, ../ota/ota_layout.h and the final telemetry spec.

  OTA channel   host->dev 0x600+node   dev->host 0x580+node
      host->dev 0x01 GET_INFO
      host->dev 0x0C FOC_STAT
      (0x06/0x07 are END/COMMIT on this channel and are NOT used here)
      dev->host 0x85 HEARTBEAT [1]=role [2]=state [3]=active [4]=self
                               [5]=confirm_rc [6]=try_left [7]=flags
      dev->host 0x83 INFO      [1]=active [2]=inactive [3]=state [4]=try_left
                               [5..6]=running version LE16 [7]=flags
      dev->host 0x87 FOC       [1..4]=foc_loop count LE32 [5]=foc_init rc (i8)

  PARAM channel host->dev 0x610+node   dev->host 0x590+node
      host->dev 0x01 GET_ONE  [1]=id
      host->dev 0x02 SET_ONE  [1]=id [2..5]=value LE32
      host->dev 0x03 GET_ALL
      host->dev 0x04 SAVE
      host->dev 0x05 LOAD
      host->dev 0x06 TELEM_CTRL [1]=enable(0/1) [2..3]=period_ms LE16
      host->dev 0x07 TELEM_GET  (no args -> one 0xA1 frame comes back)
      dev->host 0x81 ACK
      dev->host 0x82 NACK     [5]=error code (OTA_PERR_*)
      dev->host 0x83 PARAM    [1]=id [2..5]=value LE32
                              [6]=flags (bit0 saved in flash, bit1 is default)
      dev->host 0x84 ALL_END  [1]=parameter count
      dev->host 0xA0 TEXT     [1]=offset [2]=total_len [3..7]=5 text bytes
                              (reassembled and counted, never displayed)
      dev->host 0xA1 TELEM    [1..2]=ia_mA int16 LE
                              [3..4]=ib_mA int16 LE
                              [5..6]=ic_mA int16 LE
                              [7]=vbus_dV uint8 (0.1 V units)
      dev->host 0xA2 NTC      [1..2]=temp_c_x10 int16 LE (0.1 degC, 0x8000=invalid)
                              [3..4]=ntc_raw uint16 LE (ADC raw 0..4095)
                              [5]=flags (bit0 valid, bit1 over 45C, bit2 tripped
                                         this power-up, bit3 sensor fault)
                              [6]=trip limit in whole degC
                              [7]=reserved 0
                              (the firmware sends one right after every 0xA1,
                               so the temperature rides the telemetry stream)
      dev->host 0xA4 ENCODER  [1..2]=raw count LE16 (0..16383, 0xFFFF=invalid)
                              [3..4]=single-turn angle cdeg LE16 (0.01 degree)
                              [5]=status (bit0 valid, bit1 parity, bit2 device, bit3 SPI timeout)
                              [6..7]=read error count LE16
      host->dev 0x12 CTRL_SET [1]=field [2..5]=signed value LE32, scale 1000
      host->dev 0x13 CALIB    [1]=1 start / 2 abort encoder electrical calibration
      host->dev 0x14 CTRL_STAT [] -> dev->host 0xA5
      dev->host 0xA5 CTRL     [1]=mode [2]=state [3]=calibration stage
                              [4]=progress % [5..6]=Iq target mA signed LE16
                              [7]=calibration error code
                              [4..5]=eβ_mV i16 LE [6]=sample count [7]=flags

  Telemetry runs on the PARAM channel, not on OTA (OTA 0x06/0x07 are END and
  COMMIT).  TELEM_CTRL: enable 0=off / 1=on, period_ms little-endian uint16,
  recommended default 20 ms (50 Hz), accepted range 5..1000 ms.

  IMPORTANT (final firmware spec): this board has NO bus-voltage sense channel
  (PC2 is the DET2 limit input, not an ADC), so vbus_dV is always 0 and must
  NOT be drawn as "0.0 V" nor treated as a fault.  The page hides the vbus
  trace while it stays 0 and says so in one short note; if a future board ever
  reports a non-zero value the trace comes back on its own.

HTTP endpoints
--------------
      GET /                 the page
      GET /api/status       snapshot; includes the "telem" object (or null)
      GET /api/telem/batch  ?since=SEQ -> every sample with seq > SEQ
      GET /api/telem        ?enable=0|1[&period_ms=NNN] -> TELEM_CTRL
      GET /api/motor        ?enable=0|1[&volt=V&vbus=V&step_ms=NNN]
      GET /api/control/set  ?field=name&value=number
      GET /api/control/calibrate ?op=start|abort
      GET /api/control/status
      GET /api/ota/onekey   stops output, releases CAN, runs xige_foc CAN-OTA
      GET /api/ota/status   updater state and recent output
      GET /api/set          ?id=N&value=V
      GET /api/getall       re-read the whole parameter table
      GET /api/get          ?id=N
      GET /api/save         write the parameter page
      GET /api/load         reload the parameter page

Usage
-----
    <python> scripts/foc_web.py                       # auto-detect adapter, port 8888
    <python> scripts/foc_web.py --port 8890 --node 1 --verbose
    <python> scripts/foc_web.py --channel COM4 --baud 500000

then open http://127.0.0.1:8888/ in a browser.
(Normally started by 启动网页.cmd in the project root, which also makes sure
only ONE foc_web instance owns the port and the USB-CAN adapter.)

Notes / limits
--------------
  * HTTP layer is the Python standard library only (http.server).  pyserial and
    python-can come in through scripts/zqwl.py, which sits in the same directory.
  * If the adapter cannot be opened the server STILL starts: the page shows the
    connection failure reason instead of a blank screen.
  * Every CAN request is bounded (default 1.5 s, one retry); a missing reply
    becomes an error for that request, never a hung browser tab.
  * The browser polls /api/status every 200 ms (status bar only) and
    /api/telem/batch every 100 ms (curves).  The batch poll is what keeps the
    plotted curve faithful to the 50/100 Hz stream: /api/status only ever
    carried the NEWEST sample, so 96% of the frames never reached the page.
  * Memory is bounded on both sides: TELEM_KEEP telemetry samples on the
    server, RING_MAX in the browser ring buffer (both 4000).
  * Telemetry samples carry no device timestamp; the batch endpoint reports the
    measured rate and the page spaces each batch evenly from it, ending at
    "now".  A gap the client missed (dropped:true) is drawn as a break in the
    trace instead of a straight line across it.
  * The telemetry decoder is tolerant: short or unknown frames are dropped and
    the reader keeps running.
  * Pure ASCII on purpose (the project pipes tool output through a non-UTF8
    codepage).  Keep it that way.
"""

import argparse
import json
import math
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# ---------------------------------------------------------------------------
# transport: zqwl.py lives next to this file
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from web_client import MUTATING_ROUTES
from run_recorder import RunRecorder
import max_output

try:
    import zqwl
    from zqwl import ZqwlBus, find_zqwl_port, ZqwlError
except Exception as exc:  # pragma: no cover - depends on the bench install
    zqwl = None
    ZqwlBus = None
    ZqwlError = Exception  # so "except ZqwlError" below still compiles/works

    def find_zqwl_port():
        return None

    ZQWL_IMPORT_ERROR = ("cannot import scripts/zqwl.py (%s); pyserial and "
                         "python-can must be installed and zqwl.py must sit "
                         "next to foc_web.py" % exc)
else:
    ZQWL_IMPORT_ERROR = None


# ---------------------------------------------------------------------------
# protocol constants (ota_params.h / ota_layout.h + final telemetry spec)
# ---------------------------------------------------------------------------
OTA_ID_HOST_BASE = 0x600        # host -> device, OTA channel
OTA_ID_DEV_BASE = 0x580         # device -> host, OTA channel
PARAM_ID_HOST_BASE = 0x610      # host -> device, param channel (telemetry too)
PARAM_ID_DEV_BASE = 0x590       # device -> host, param channel (telemetry too)

OTA_CMD_GET_INFO = 0x01
OTA_CMD_FOC_STAT = 0x0C
OTA_RSP_INFO = 0x83
OTA_RSP_HB = 0x85
OTA_RSP_FOC = 0x87

PCMD_GET_ONE = 0x01
PCMD_SET_ONE = 0x02
PCMD_GET_ALL = 0x03
PCMD_SAVE = 0x04
PCMD_LOAD = 0x05
PCMD_TELEM_CTRL = 0x06
PCMD_TELEM_GET = 0x07
PCMD_MOTOR_CTRL = 0x08
PCMD_CUR_CTRL = 0x0B    # 正弦开环电流上限
PCMD_MOTOR_STAT = 0x09    # 电机运行状态读取 (MOTOR_STAT)
PCMD_BRAKE_CTRL = 0x0C   # ★抱闸: [1]release(1)/engage(0) -> 0x81 (诊断"不转"用)
PCMD_HEARTBEAT = 0x17    # ★上位机心跳 (不应答): 运行中收到过它, 固件 1 s 收不到参数帧就停机
PCMD_PHASE_TEST = 0x0A   # ★静态矢量: [1]sa [2]sb [3]sc [4..5]duty [6]seconds -> 0x81
PCMD_MEAS_STAT = 0x11    # ★只读测量状态 -> 0xA3 (高分辨率占空比/峰值电流)
PCMD_CTRL_SET = 0x12    # signed 32-bit setpoint -> ACK/NACK
PCMD_CALIB = 0x13       # encoder electrical calibration start/abort
PCMD_CTRL_STAT = 0x14   # control/calibration status -> 0xA5
PRSP_MEAS = 0xA3
FOC_PWM_ARR = 3200       # 与 foc_config.h FOC_PWM_ARR 一致 (CCR -> 调制比换算用)

# ===========================================================================
# SG-6010HB OTA/sensor bring-up. Unverified current conventions stay unknown.
MOTOR_PROFILES = {
    "SG-6010HB": {
        "label": "SG-6010HB · FOC调试",
        "nameplate": {
            "vbus_v": 24.0, "pole_pairs": 10,
            "rs_mohm": 0.224, "line_rs_mohm": None, "ls_uh": 235,
            "kt_nm_a_rms": None, "ke_vrms_per_krpm_line": None,
            "rated_current_arms": None, "peak_current_arms": None, "peak_current_a": None,
            "rpm_rated": None, "nm_rated": None, "psi_mwb_nominal": None,
            "catalog_ls_uh": 235, "catalog_kt_nm_a": 0.0606,
            "reduction_ratio": 9.67, "ntc_r25_ohm": 10000, "ntc_beta": 3435,
        },
        "safe_cur_ma_default": 3000,
        "safe_cur_ma_max": 30000,
        "safe_volt": 8.0,
    },
}
MOTOR_PROFILE_DEFAULT = "SG-6010HB"
# 选择结果持久化到这个文件 (在工程根目录, 与 ota_version.txt 同级)
MOTOR_PROFILE_FILE = "motor_profile.txt"

# 运行时当前选择 (由 /api/motor_profile 或启动时的文件决定)
ACTIVE_MOTOR = {"sel": MOTOR_PROFILE_DEFAULT}


def motor_profile(sel=None):
    """取电机档案 (sel=None -> 当前选择; 非法值回落到本工程默认)。"""
    key = sel or ACTIVE_MOTOR["sel"]
    return MOTOR_PROFILES.get(key) or MOTOR_PROFILES[MOTOR_PROFILE_DEFAULT]


def position_output_to_rotor_deg(output_deg):
    """Convert SG gearbox output-axis degrees to the rotor degrees expected by firmware."""
    return float(output_deg) * float(motor_profile()["nameplate"]["reduction_ratio"])


def load_motor_sel():
    """启动时读持久化的选择; 读不到就用本工程默认。"""
    try:
        with open(MOTOR_PROFILE_FILE, "r") as f:
            s = f.read().strip()
        if s in MOTOR_PROFILES:
            ACTIVE_MOTOR["sel"] = s
    except Exception:
        pass
    return ACTIVE_MOTOR["sel"]


def save_motor_sel(sel):
    """切换电机档案 (内存 + 落盘)。返回 (ok, error)。"""
    if sel not in MOTOR_PROFILES:
        return False, "未知电机 %r (可选: %s)" % (sel, ", ".join(MOTOR_PROFILES))
    ACTIVE_MOTOR["sel"] = sel
    try:
        with open(MOTOR_PROFILE_FILE, "w") as f:
            f.write(sel + "\n")
    except Exception as exc:
        return True, "已切换但写文件失败: %s" % exc
    return True, ""

MOTOR_DUTY_MAX = 300        # 千分比, 与固件 FOC_SIXSTEP_DUTY_MAX 一致
MOTOR_VOLT_MAX = 8.0        # 跟随西格工程电压参数上限

# ---- ★ 转速直设 (rpm) + 不发热安全上限 -----------------------------------
# 页面只让填"转速", 转速由电频率实现: 10 极对 -> rpm = 1000/step_ms
#   step_ms = 1000/rpm      (100rpm -> 10ms/步, 与固件换向节拍公式一致)
RPM_MIN = 3                 # 3rpm -> step_ms=333 (下限由固件 5..5000ms 决定)
RPM_MAX_SAFE = 300          # 与速度环参数的硬件配置上限一致
RPM_MODE_DEFAULT = 0        # ★ 模式0 = 正弦电压开环
CONTROL_MODE_MAX_WEB = 7
PRSP_ACK = 0x81
PRSP_NACK = 0x82
PRSP_PARAM = 0x83
PRSP_ALL_END = 0x84
PRSP_TEXT = 0xA0
PRSP_TELEM = 0xA1
PRSP_NTC = 0xA2          # ★NTC 温度帧 (跟着 0xA1 遥测流一帧一帧发)
PRSP_ENCODER = 0xA4      # AS5048A 单圈角度帧, 固件每 20ms 独立上报
PRSP_CTRL = 0xA5
PCMD_CTRL_FEEDBACK = 0x16
PRSP_CTRL_FEEDBACK = 0xA6
PRSP_CTRL_POSITION = 0xA7
PRSP_MOTOR = 0x86

CONTROL_FIELDS = {
    "max_bus_a": (13, 1000, .1, 20.0),
    "max_id_min_a": (14, 1000, -30.0, 0.0),
    "max_phase_a": (15, 1000, 0.0, 30.0),
    "max_bus_v": (16, 1000, 6.0, 26.4),
    "max_speed_rpm": (17, 1, 0.0, 20000.0),
    # 2026-10-14: 0.5..3 A 电流环锁定测试; ID 7 同时是相电流跳闸阈值 (0.1..5 A)。
    "current_a": (1, 1000, -30.0, 30.0),
    "voltage_v": (2, 1000, -8.0, 8.0),
    "speed_rpm": (3, 1000, -300.0, 300.0),
    # UI uses gearbox output degrees; convert to rotor degrees in the route.
    "position_deg": (4, 1000, -200000.0, 200000.0),
    "torque_nm": (5, 1000, -5.0, 5.0),
    "mit_position_rad": (6, 1000, -6283.185, 6283.185),
    "mit_speed_rad_s": (7, 1000, -31.416, 31.416),
    "mit_kp": (8, 1000, 0.0, 10.0),
    "mit_kd": (9, 1000, 0.0, 5.0),
    "mit_torque_nm": (10, 1000, -5.0, 5.0),
    "voltage_d_v": (11, 1000, -8.0, 8.0),
    "open_speed_rpm": (12, 1000, 3.0, 200.0),
}
ENCODER_COUNTS_PER_REV = 16384
ENCODER_FRESH_S = 0.25
ENCODER_SPEED_MAX_DT_S = 1.5  # 超过这么久没更新就不算转速 (固件 0xA4 正常是 20ms 一帧)
ENCODER_SPEED_WINDOW_S = 0.10 # ★转速估算的最小基线: 用 ~100ms 的两点求差。
                              #   为什么不用相邻两帧: 20ms 基线下 ±5count 量化噪声 =
                              #   ±0.9rpm 假转速; 而且 Windows 的 time.time() 只有
                              #   ~15.6ms 分辨率, dt 常算成 0 -> 转速每隔一帧显示 None。

# ★"已过温跳闸"提示在温度恢复后保留多久 (秒)。温度回到复归区间后连续这么久,
#   页面就不再显示那条提示 —— 避免降温后状态栏一直挂着旧信息。
#   设备里的锁存位不受影响, /api/status 仍给出 tripped_latched 与 recovered_s。
NTC_TRIP_SHOW_S = 5.0

# ★客户端先断开 (浏览器刷新/关标签/后台节流/请求被 abort) 时, 写响应会抛这几个异常。
#   它在 Windows 上表现为 WinError 10053 (ConnectionAbortedError: "您的主机中的软件
#   中止了一个已建立的连接。"), 10054 (ConnectionResetError), 或 10058/BrokenPipe。
#   这是【正常现象】, 既不是设备故障也不是服务故障 —— 以前这里只挡了后两种,
#   于是 10053 会冒到 do_GET 的兜底分支, 被当成 "HTTP handler failed" 显示成红色
#   ERR 横幅 (中文错误文本还被 ascii_text 变成一堆 "????")。现在统一按"客户端走了"处理。
CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)

PERR = {
    0: "none",
    1: "bad command (OTA_PERR_BAD_CMD)",
    2: "bad parameter id (OTA_PERR_BAD_ID)",
    3: "out of range (OTA_PERR_RANGE)",
    4: "busy: OTA transfer in progress (OTA_PERR_BUSY)",
    5: "parameter page write failed (OTA_PERR_FLASH)",
    6: "firmware has no telemetry/motor source (OTA_PERR_NO_SOURCE)",
}

PARAM_FLAG_SAVED = 0x01
PARAM_FLAG_DEFAULT = 0x02

OTA_STATE_NAMES = {0: "BOOT", 1: "APP", 2: "RECEIVING", 3: "VERIFYING"}
OTA_ROLE_NAMES = {0: "boot", 1: "app"}
SLOT_NAMES = {0: "A", 1: "B"}
MOTOR_STATE_NAMES = {0: "idle", 1: "precharge", 2: "running", 3: "fault"}

REQ_TIMEOUT = 1.5               # seconds to wait for a reply before failing
# ★运行时长默认不限 (seconds=0), 靠两道自动停机兜底:
#   页面关闭/卡死/断网 -> 本服务 WATCHDOG_S 内没收到任何页面请求就停机;
#   本服务崩溃/USB-CAN 拔掉/CAN 断 -> 固件 1 s 收不到心跳 (HEARTBEAT_S 一帧) 就停机。
WATCHDOG_S = 6.0
HEARTBEAT_S = 0.2
REQ_ATTEMPTS = 2                # one send + one retry
POLL_TIMEOUT = 0.7              # shorter bound for the background device poll
TEXT_FRAGMENT_TIMEOUT = 3.0     # drop a half-received TEXT line after this
DEVICE_SILENT_AFTER = 3.5       # no heartbeat/INFO for this long -> say so
POLL_INTERVAL = 0.5             # background device refresh period

TELEM_KEEP = 4000               # telemetry samples kept on the server (history)
TELEM_RATE_MIN_SPAN = 1.0       # need this much history before a rate is shown
TELEM_RATE_WINDOW = 5.0         # rate is measured over this sliding window
TELEM_PERIOD_MIN = 5            # period_ms accepted from the browser (spec)
TELEM_PERIOD_MAX = 1000         # period_ms accepted from the browser (spec)
TELEM_PERIOD_DEFAULT = 20       # 20 ms = 50 Hz (spec recommendation); NOTE: the
                                # page's own selector defaults to 10 ms and
                                # always sends period_ms explicitly, so this
                                # value is only the protocol-level fallback.
TELEM_FRESH_AFTER = 2.0         # telemetry older than this is no longer "live"
TELEM_ERR_KEEP = 20             # only the newest errors are kept for the page

LOG_KEEP = 200                  # internal diagnostic ring (never sent to the page)


# ---------------------------------------------------------------------------
# parameter mirror table
#
# !!! MUST BE KEPT IN SYNC WITH ota_params.h / ota_params.c !!!
# Mirror of const ota_param_desc_t g_ota_params[OTA_PARAM_COUNT] in
# ../ota/ota_params.c.  The device is the authority at runtime: values always
# come back through the PARAM (0x83) frames.  This table only supplies the
# names, units, defaults and min/max used for display and for client-side range
# checks.  Names/units/limits here are pure ASCII on purpose (the C header's own
# comments are not).  If the firmware table changes, change this one too.
# ---------------------------------------------------------------------------
PARAM_TABLE = [
    {'id': 0, 'name': 'pole_pairs', 'def': 10, 'min': 1, 'max': 64, 'unit': 'pairs'},
    {'id': 1, 'name': 'enc_offset', 'def': 0, 'min': 0, 'max': 16383, 'unit': 'cnt'},
    {'id': 2, 'name': 'enc_dir', 'def': 0, 'min': 0, 'max': 2, 'unit': '0/1/2'},
    {'id': 3, 'name': 'cur_kp', 'def': 150, 'min': 0, 'max': 10000, 'unit': 'x100'},
    {'id': 4, 'name': 'cur_ki', 'def': 100, 'min': 0, 'max': 1000000, 'unit': 'x100'},
    {'id': 5, 'name': 'spd_kp', 'def': 500, 'min': 0, 'max': 10000, 'unit': 'x100'},
    {'id': 6, 'name': 'spd_ki', 'def': 20, 'min': 0, 'max': 10000, 'unit': 'x100'},
    {'id': 7, 'name': 'i_limit', 'def': 3000, 'min': 100, 'max': 30000, 'unit': 'mA'},
    {'id': 8, 'name': 'pos_kp', 'def': 300, 'min': 0, 'max': 10000, 'unit': 'x100/s'},
    {'id': 9, 'name': 'pos_ki', 'def': 0, 'min': 0, 'max': 10000, 'unit': 'x100/s2'},
    {'id': 10, 'name': 'pos_kd', 'def': 0, 'min': 0, 'max': 1000, 'unit': 'x1000'},
    {'id': 11, 'name': 'speed_limit', 'def': 300, 'min': 1, 'max': 300, 'unit': 'rpm'},
    {'id': 12, 'name': 'voltage_limit', 'def': 9000, 'min': 100, 'max': 10000, 'unit': 'mV'},
    {'id': 13, 'name': 'deadtime_comp_ns', 'def': 400, 'min': 0, 'max': 1000, 'unit': 'ns;0=off'},
    {'id': 14, 'name': 'speed_ramp', 'def': 60, 'min': 1, 'max': 3000, 'unit': 'rpm/s'},
    {'id': 15, 'name': 'speed_filter', 'def': 20, 'min': 0, 'max': 1000, 'unit': 'ms'},
    {'id': 16, 'name': 'current_bw', 'def': 1000, 'min': 0, 'max': 2000, 'unit': 'rad/s;0=manual'},
    {'id': 17, 'name': 'phase_rs', 'def': 224, 'min': 1, 'max': 10000000, 'unit': 'uOhm'},
    {'id': 18, 'name': 'phase_ls', 'def': 235, 'min': 1, 'max': 10000, 'unit': 'uH'},
    {'id': 19, 'name': 'spd_kd', 'def': 0, 'min': 0, 'max': 10000, 'unit': 'x1000'},
    {'id': 20, 'name': 'voltage_ramp', 'def': 1000, 'min': 100, 'max': 100000, 'unit': 'mV/s'},
    {'id': 21, 'name': 'torque_limit', 'def': 2700, 'min': 1, 'max': 5000, 'unit': 'mNm'},
    {'id': 22, 'name': 'current_filter', 'def': 100, 'min': 0, 'max': 10000, 'unit': 'us'},
]
PARAM_BY_ID = dict((p["id"], p) for p in PARAM_TABLE)

def parameter_table(version):
    table = [dict(p) for p in PARAM_TABLE]
    if version is None or version < 0x0A35:
        table[12].update({'def':8000,'max':8000})  # old firmware compatibility only
        table[17].update({'def':275,'max':10000,'unit':'mOhm equivalent'})
        table[18].update({'def':600,'unit':'uH; provisional'})
    if version is not None and version < 0x0A36:
        table[16]['def'] = 200
    return table



# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def le32(data, off):
    """Little-endian 32-bit value from a CAN payload (short frames -> 0)."""
    chunk = bytes(data[off:off + 4])
    chunk = chunk + b"\x00" * (4 - len(chunk))
    return chunk[0] | (chunk[1] << 8) | (chunk[2] << 16) | (chunk[3] << 24)


def le16(data, off):
    chunk = bytes(data[off:off + 2])
    chunk = chunk + b"\x00" * (2 - len(chunk))
    return chunk[0] | (chunk[1] << 8)


def s16(value):
    """16-bit unsigned -> signed (the telemetry phase currents are int16)."""
    value &= 0xFFFF
    return value - 0x10000 if value > 0x7FFF else value


def s8(value):
    """Unsigned byte -> signed byte (foc_init returns int)."""
    value &= 0xFF
    return value - 256 if value > 127 else value


def to_i32(value):
    """Wrap a Python int into the 32-bit range the firmware stores."""
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value > 0x7FFFFFFF else value


def fmt_version(version):
    if version is None:
        return None
    return "0x%04X" % (version & 0xFFFF)


def perr_text(code):
    return PERR.get(code, "unknown error %d" % code)


def hexs(data):
    try:
        return bytes(data).hex()
    except Exception:
        return "?"


def ascii_text(text, limit=220):
    """Make an EXTERNAL string safe for this project's ASCII pipeline.

    pyserial and the OS hand back localized error strings (Chinese on this
    bench); the project pipes tool output through a non-UTF8 codepage, so those
    would land in the page as mojibake.  Every message that quotes an outside
    exception goes through here.
    """
    try:
        text = str(text)
    except Exception:
        return "unprintable error"
    out = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in text)
    return out[:limit]


class Msg(object):
    """Minimal stand-in for can.Message (what ZqwlBus.send() reads)."""

    __slots__ = ("arbitration_id", "is_extended_id", "is_remote_frame",
                 "data", "dlc", "timestamp")

    def __init__(self, arbitration_id, data, is_extended_id=False,
                 is_remote_frame=False):
        self.arbitration_id = arbitration_id
        self.data = bytes(data)
        self.dlc = len(self.data)
        self.is_extended_id = is_extended_id
        self.is_remote_frame = is_remote_frame
        self.timestamp = time.time()


class TextAssembler(object):
    """Reassemble the 1 Hz announcement from 0xA0 TEXT frames.

    Each TEXT frame carries: [1]=offset, [2]=total_len, [3..7]=up to 5 bytes.
    A line is emitted only when every byte has arrived; a half-received line is
    dropped after TEXT_FRAGMENT_TIMEOUT seconds so a lost frame cannot wedge
    the buffer forever.  Keyed by the device arbitration id.

    Kept for protocol completeness and diagnostics only: the dashboard never
    displays the assembled text.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._bufs = {}

    def feed(self, source_id, data, now):
        """Return the completed text, or None while still incomplete."""
        if len(data) < 3:
            return None
        offset = data[1]
        total = data[2]
        payload = bytes(data[3:8])
        if total == 0 or total > 128 or offset >= total:
            return None
        take = total - offset
        if take > len(payload):
            take = len(payload)
        if take <= 0:
            return None
        with self._lock:
            slot = self._bufs.get(source_id)
            if slot is None or slot["total"] != total or now - slot["t0"] > TEXT_FRAGMENT_TIMEOUT:
                slot = {"total": total, "data": bytearray(total), "have": [False] * total,
                        "t0": now, "got": 0}
                self._bufs[source_id] = slot
            for i in range(take):
                idx = offset + i
                if not slot["have"][idx]:
                    slot["have"][idx] = True
                    slot["got"] += 1
                slot["data"][idx] = payload[i]
            if slot["got"] == total:
                del self._bufs[source_id]
                text = bytes(slot["data"]).decode("ascii", "replace")
                return text
        return None

    def flush_stale(self, now):
        with self._lock:
            stale = [k for k, v in self._bufs.items()
                     if now - v["t0"] > TEXT_FRAGMENT_TIMEOUT]
            for key in stale:
                del self._bufs[key]
        return len(stale)


class TelemBuffer(object):
    """Sliding window of decoded telemetry samples (bounded, thread-safe).

    Two deques, both bounded:
      _samples : the newest TELEM_KEEP samples, each
                 (seq, t, ia_mA, ib_mA, ic_mA, vbus_dV)
      _stamps  : arrival times inside TELEM_RATE_WINDOW, for the rate

    A device that streams for hours therefore cannot grow this process's
    memory, and rate_hz is always measured over a fresh sliding window rather
    than over the whole session.

    Sequence numbers: every accepted frame gets the next integer from a
    counter that starts at 1.  The counter NEVER resets for the life of the
    process -- not when the stream stops, not when the device is unplugged and
    plugged back in (this dashboard opens the adapter once at startup; there is
    no in-process adapter reconnect).  A browser cursor therefore stays valid
    across a device gap, and only a restart of foc_web.py starts a new series
    (a fresh page then learns the current cursor from a since-less request).
    Sequence numbers are NOT reused and never go backwards, so a client can
    tell "nothing new" from "I missed data" (see samples_since()).

    Locking: one plain Lock, exactly as before.  add() runs on the CAN reader
    thread, samples_since()/snapshot() run on HTTP handler threads, and the
    lock is never held while calling out, so the two never deadlock.
    """

    def __init__(self, keep=TELEM_KEEP):
        self._lock = threading.Lock()
        self._samples = deque(maxlen=keep)   # (seq,t,ia_mA,ib_mA,ic_mA,vbus_dV)
        self._stamps = deque()               # arrival times for the rate window
        self._seq = 0                        # last handed out sequence number
        self._total = 0

    def add(self, t, ia, ib, ic, vbus):
        with self._lock:
            self._seq += 1
            seq = self._seq
            self._samples.append((seq, t, ia, ib, ic, vbus))
            self._stamps.append(t)
            self._total += 1
            # Rate window: keep only frames inside TELEM_RATE_WINDOW seconds.
            # Dropping them by TIME (not by "keep two") means a stalled stream
            # empties the window and reports no rate at all, instead of
            # dividing one frame by a long silence and inventing a tiny Hz.
            cutoff = t - TELEM_RATE_WINDOW
            while self._stamps and self._stamps[0] < cutoff:
                self._stamps.popleft()
        return seq

    def snapshot(self):
        """(newest_sample_or_None, rate_hz_or_None, total_frames).

        Unchanged shape for /api/status: newest is (t, ia, ib, ic, vbus).
        """
        with self._lock:
            newest = self._samples[-1][1:] if self._samples else None
            stamps = list(self._stamps)
            total = self._total
        rate = None
        if len(stamps) >= 3:
            span = stamps[-1] - stamps[0]
            if span >= TELEM_RATE_MIN_SPAN:
                rate = round((len(stamps) - 1) / span, 1)
        return newest, rate, total

    def samples_since(self, seq):
        """(newest_seq, dropped, [(ia, ib, ic, vbus), ...]) oldest first.

        Every retained sample with seq > the caller's cursor.  `dropped` is
        True when the cursor is older than the oldest retained sample, i.e.
        the ring wrapped and the client really did miss frames; the page then
        draws a break instead of joining across the gap.  A cursor that is
        AHEAD of the newest sample (server restarted under a live page) is not
        a drop: it simply yields no samples.
        """
        with self._lock:
            if not self._samples:
                return self._seq, False, []
            newest_seq = self._samples[-1][0]
            oldest_seq = self._samples[0][0]
            dropped = bool(seq is not None and (seq + 1) < oldest_seq)
            rows = [(s[2], s[3], s[4], s[5]) for s in self._samples if s[0] > seq]
        return newest_seq, dropped, rows


class Request(object):
    """One host->device command and the replies that satisfy it.

    arb_id  is the id we SEND to (0x610+node param channel, 0x600+node OTA
            channel); reply_id is the id the device answers on (0x590+node /
            0x580+node).  Matching a reply against arb_id would never fire --
    that mistake cost a full debugging round.

    Two events, and they are not the same thing:
      event : set by the reader thread when a matching reply lands
      done  : set by the worker when the attempt is over (reply or timeout)
    Both are needed: waiting only on `event` can miss a reply that arrives
    before the waiter starts, and waiting only on `done` cannot distinguish
    "answered" from "timed out".
    """

    def __init__(self, name, arb_id, reply_id, data, want, timeout=REQ_TIMEOUT):
        self.name = name
        self.arb_id = arb_id
        self.reply_id = reply_id
        self.data = data
        self.want = set(want)
        self.timeout = timeout
        self.reply = None
        self.event = threading.Event()
        self.done = threading.Event()
        self.cancelled = False
        self.started = threading.Event()
        self.epoch = None
        self.sent_at = None

    def cancel(self):
        self.cancelled = True
        self.reply = None
        self.event.set()
        self.done.set()


class Link(object):
    """Owns the CAN bus: background reader + one serialized request worker.

    All sending goes through the worker queue, so only one thread ever pushes
    frames onto the adapter (the adapter's own reader thread is inside zqwl).
    The TCP server threads only ever enqueue and wait on an Event, which keeps
    the 1.5 s bound of "no reply" independent of how many browser tabs are open.
    """

    def __init__(self, adapter, node, verbose=False):
        self.adapter = adapter
        self.node = node
        self.verbose = verbose
        self.ota_host_id = OTA_ID_HOST_BASE + node
        self.ota_dev_id = OTA_ID_DEV_BASE + node
        self.param_host_id = PARAM_ID_HOST_BASE + node
        self.param_dev_id = PARAM_ID_DEV_BASE + node

        self.ready = adapter is not None
        self.reason = "" if self.ready else "CAN adapter is not open"
        self.bus = adapter

        self.state_lock = threading.Lock()
        self.diag_lock = threading.Lock()
        self.max_output_lock = threading.Lock()
        self.max_trace_lock = threading.Lock()
        self.max_output_snapshot = None
        self.max_output_error = None
        self.max_run_generation = 0
        self.log = deque(maxlen=LOG_KEEP)            # internal only
        self.errors = deque(maxlen=TELEM_ERR_KEEP)   # newest real errors
        self.info = {}
        self.foc = {}
        self.hb = {}
        self.texts = 0
        self.last_text = None
        self.last_rx = 0.0
        self.tx_total = 0
        self.rx_total = 0
        self.bad_frames = 0
        self.client_gone = 0
        self.err_frames = 0        # ★CAN 错误帧计数 (candleLight 会大量上报)
        self.last_activity = 0.0   # ★任何帧(含错误帧)的到达时刻: 判"手柄是否假活"用
        self.need_reopen = False   # ★python-can 适配器 IO 异常时置起, 交给 _ensure_bus 重开
        self._ntc_clear_t = None   # 温度降到"允许启动"区间的起始时刻 (消隐过温提示用)

        self.telem = TelemBuffer()
        self.control_targets = {}      # field id -> last raw setpoint sent (for the run recorder)
        self.recorder = RunRecorder(
            self,
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "运行记录"),
            lambda: self.request("REC_FEEDBACK", self.param_host_id, self.param_dev_id,
                                 bytes([PCMD_CTRL_FEEDBACK, 1, 0, 0, 0, 0, 0, 0]),
                                 (PRSP_CTRL_FEEDBACK, PRSP_NACK), attempts=1))
        self.telem_enable = 0
        self.telem_period_ms = TELEM_PERIOD_DEFAULT
        self.telem_supported = None      # None = not tried, True/False = known
        self.telem_last_err = ""
        self.motor_status = None
        self.control_status = None
        self.feedback = {}
        self.ntc = None                   # ★最新一帧 0xA2 NTC 温度 (None = 从没收到)
        self.encoder_history=[]
        self.encoder_seq=0
        self.encoder = None               # ★最新一帧 0xA4 AS5048A 单圈角度
        self._encoder_anchor = None       # 转速估算的基线样本 (见 _on_encoder)
        self._encoder_speed_rpm = None    # ★上一次算出的转速; 每帧都保留着, 别清成 None

        self.pending_lock = threading.Lock()
        self.pending = []          # lists of Requests awaiting a reply
        self._param_hooks = {}     # arb_id -> callable for unsolicited PARAM frames
        self.text_asm = TextAssembler()
        self.job_q = []            # list of (priority, callable), sorted on push
        self.job_cv = threading.Condition()
        self.command_epoch = 0
        self._tx_lock = threading.Lock()   # worker 线程与心跳线程共用一条总线
        self.request_context = threading.local()
        self.outstanding = set()
        self.stop_lock = threading.Lock()
        self.stopping = False
        self._stop = False
        self._reader = None
        self._worker = None

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        """Always start BOTH threads.

        The request worker must run even without an adapter, otherwise an
        /api/set could never be served and every browser call would sit out its
        full timeout before erroring.  Only the reader needs a bus.
        """
        self._worker = threading.Thread(target=self._work_loop, name="foc-web-tx")
        self._worker.daemon = True
        self._worker.start()
        if self.bus is not None:
            self._reader = threading.Thread(target=self._read_loop, name="foc-web-rx")
            self._reader.daemon = True
            self._reader.start()

    def attach(self, adapter):
        """★ 装上(或换上一个)适配器并重启读线程。

        用途: USB-CAN 适配器会从 USB 总线上掉线(实测过多次), 插回来之后
        不需要重启网页 —— App._ensure_bus() 会自动调用这里把它接上。
        """
        if adapter is None:
            return False
        old = self.bus
        if old is not None and old is not adapter:
            try:
                old.shutdown()
            except Exception:
                pass
        self.bus = adapter
        self.ready = True
        self.reason = ""
        self.telem_supported = None      # 新适配器 = 链路重来, 遥测要重新确认
        # ★ 清掉"没找到适配器"这类【过期】错误, 否则页面红色错误框会一直挂着
        #   一句早已不成立的报错(实测: 链路已恢复, 页面还在显示没适配器)。
        with self.diag_lock:
            self.errors.clear()
        if self._reader is None or not self._reader.is_alive():
            self._reader = threading.Thread(target=self._read_loop, name="foc-web-rx")
            self._reader.daemon = True
            self._reader.start()
        return True

    def detach(self):
        """★ 把当前适配器摘掉(关句柄), 交给 _ensure_bus 重新开一个。

        用于"句柄还在但已经死了"的情况: 实测串口会进入写失败状态
        (PermissionError(13) WriteFile failed) 且再也收不到任何帧,
        此时 bus 不是 None, 旧逻辑永远不会重连 -> 页面看着"连着"其实全死。
        """
        bus = self.bus
        self.bus = None
        self.ready = False
        self.reason = "adapter stopped responding; reopening"
        if bus is not None:
            try:
                bus.shutdown()
            except Exception:
                pass

    def shutdown(self):
        self._stop = True
        with self.job_cv:
            self.job_cv.notify_all()
        if self.bus is not None:
            try:
                self.bus.shutdown()
            except Exception:
                pass

    # -- diagnostics (internal only; never rendered as a scrolling log) ----
    def add_log(self, text):
        entry = {"t": time.time(), "text": ascii_text(text, 400)}
        with self.diag_lock:
            self.log.append(entry)
        if self.verbose:
            sys.stdout.write("   [web] %s\n" % entry["text"])
            sys.stdout.flush()

    def add_error(self, text):
        """Remember one REAL error for the status bar (bounded, newest last)."""
        entry = {"t": time.time(), "text": ascii_text(text, 240)}
        with self.diag_lock:
            self.errors.append(entry)
        if self.verbose:
            sys.stdout.write("   [web] ERROR: %s\n" % entry["text"])
            sys.stdout.flush()

    def last_error(self):
        with self.diag_lock:
            return self.errors[-1] if self.errors else None

    def note_client_gone(self):
        """浏览器先断开连接: 只计数, 不进错误横幅 (见 CLIENT_GONE 的说明)。"""
        with self.state_lock:
            self.client_gone += 1

    # -- reader ------------------------------------------------------------
    def _read_loop(self):
        while not self._stop:
            # ★句柄可能被 _ensure_bus 摘掉(重连中): 不能直接 recv, 更不能再 break,
            #   否则一次重连就把接收线程永久干掉 (实测踩到: bus=None -> AttributeError)。
            bus = self.bus
            if bus is None:
                time.sleep(0.05)
                continue
            try:
                msg = bus.recv(timeout=0.05)
            except Exception as exc:
                self.ready = False
                self.reason = ascii_text("CAN receive failed: %s" % exc)
                self.need_reopen = True      # ★真 IO 异常: 让 _ensure_bus 重开
                self.add_error(self.reason)
                time.sleep(0.2)     # 不 break: 交给 _ensure_bus 重开后再继续
                continue
            if msg is None:
                self.text_asm.flush_stale(time.time())
                continue
            with self.state_lock:
                self.last_activity = time.time()
            # ★CAN 错误帧 (candleLight/gs_usb 会大量上报): 不是设备的业务帧, 不能
            #   当成"收到数据"计入 rx, 否则页面会显示"适配器在收帧"却怎么都连不上。
            #   统计到 err_frames, 并在状态栏给出可执行的排查提示 (实测: 对面没上电/
            #   没接终端电阻时, 适配器会以 ~4700 帧/s 刷 ACK 错误)。
            if getattr(msg, "is_error_frame", False):
                with self.state_lock:
                    self.err_frames += 1
                continue
            try:
                self._dispatch(msg)
            except Exception:
                # A malformed frame must never kill the reader: count it and
                # carry on with the next frame.
                with self.state_lock:
                    self.bad_frames += 1

    def _dispatch(self, msg):
        arb = int(getattr(msg, "arbitration_id", -1))
        data = bytes(getattr(msg, "data", b"") or b"")
        if not data:
            return
        now = time.time()
        with self.state_lock:
            self.last_rx = now
            self.rx_total += 1
        if self.verbose:
            self.add_log("rx id=0x%03X dlc=%d %s" % (arb, len(data), hexs(data)))

        # --- parameter channel (device -> host) ---
        if arb == self.param_dev_id:
            code = data[0]
            if code == PRSP_TELEM:
                self._on_telem(data, now)
                return
            if code == PRSP_TEXT:
                text = self.text_asm.feed(arb, data, now)
                if text is not None:
                    self._on_text(text)
                return
            # ★0xA2 NTC 温度: 只在状态栏显示一个数字, 不进曲线, 不新开轮询
            if code == PRSP_NTC:
                self._wake(arb, data)
                self._on_ntc(data, now)
                return
            if code == PRSP_ENCODER:
                self._on_encoder(data, now)
                return
            if code in (PRSP_CTRL_FEEDBACK, PRSP_CTRL_POSITION):
                self._on_feedback(data, now)
                self._wake(arb, data)
                return
            if code == PRSP_CTRL:
                self._wake(arb, data)
                self._on_control_status(data, now)
                return
            if code == PRSP_PARAM:
                # 0x83 frames are unsolicited (GET_ALL sends several with only
                # the final 0x84 framed as a reply), so feed them to every
                # waiting collector as well as to the global state.
                self._wake(arb, data)
                self._on_param(arb, data)
                return
            if code == PRSP_MOTOR:
                self._wake(arb, data)
                self._on_motor_status(data, now)
                return
            # ACK / NACK / ALL_END belong to whoever is waiting for them
            self._wake(arb, data)
            return

        # --- OTA channel (device -> host) ---
        if arb == self.ota_dev_id:
            code = data[0]
            if code == OTA_RSP_HB:
                self._on_heartbeat(arb, data)
                return
            # INFO and FOC_STAT are both polled and answered; wake the waiter
            # before folding the same frame into the shared state.
            self._wake(arb, data)
            if code == OTA_RSP_INFO:
                self._on_info(arb, data)
                return
            if code == OTA_RSP_FOC:
                self._on_foc(arb, data)
                return
            return

        # anything else on the bus belongs to another node and is ignored

    def _on_motor_status(self, data, now=None):
        if len(data) < 8:
            return
        now = now if now is not None else time.time()
        state = data[2]
        flags = data[7]
        fault_report = state == 3 and bool(flags & 0x20)
        with self.state_lock:
            self.motor_status = {
                "available": True,
                "mode": data[1],
                "state": state,
                "state_name": MOTOR_STATE_NAMES.get(state, "unknown"),
                "step": None if fault_report else data[3],
                "step_ms": None if fault_report else le16(data, 4),
                "fault_reason": data[3] if fault_report else None,
                "fault_peak_ma": le16(data, 4) if fault_report else None,
                "duty_permille": data[6] * 10,
                "output_on": bool(flags & 0x01),
                "fault": bool(flags & 0x02),
                "host_timeout": bool(flags & 0x08),   # 上次停机 = 固件心跳超时 (CAN 链路断过)
                "supervised": bool(flags & 0x10),     # 本次运行受固件心跳监督
                "updated_at": now,
            }

    def _on_feedback(self, data, now=None):
        if len(data) != 8:
            return
        def signed16(offset):
            return int.from_bytes(data[offset:offset+2], "little", signed=True)
        with self.state_lock:
            if data[0] == PRSP_CTRL_FEEDBACK:
                self.feedback.update(id_a=signed16(1)/1000.0,
                    iq_a=signed16(3)/1000.0, speed_rpm=signed16(5)/(100.0 if data[7] & 0x80 else 1.0),
                    state=data[7] & 0x7F, updated_at=time.time() if now is None else now)
            else:
                self.feedback.update(position_deg=int.from_bytes(data[1:5], "little", signed=True)/1000.0,
                                     position_at=time.time() if now is None else now)

    def feedback_snapshot(self):
        with self.state_lock:
            result = dict(self.feedback)
        result["stale"] = not result.get("updated_at") or time.time()-result["updated_at"] > 1.0
        result["position_stale"] = not result.get("position_at") or time.time()-result["position_at"] > 1.0
        return result

    def _on_control_status(self, data, now=None):
        if len(data) < 8:
            return
        now = time.time() if now is None else now
        iq_ma = le16(data, 5)
        if iq_ma >= 0x8000:
            iq_ma -= 0x10000
        with self.state_lock:
            self.control_status = {
                "mode": data[1],
                "state": data[2],
                "calibration_stage": data[3],
                "calibration_progress": data[4],
                "iq_target_ma": iq_ma,
                "calibration_error": data[7],
                "updated_at": now,
            }

    def _motor_status_reply(self, reply):
        if reply is None:
            return
        if reply[0] == PRSP_MOTOR:
            self._on_motor_status(reply)
            return
        code = reply[5] if len(reply) > 5 else 0
        with self.state_lock:
            self.motor_status = {
                "available": False,
                "error": "device has no motor status source (%s)" % perr_text(code),
                "updated_at": time.time(),
            }

    def motor_status_snapshot(self):
        with self.state_lock:
            status = dict(self.motor_status or {})
        if status.get("updated_at"):
            status["age_s"] = round(time.time() - status["updated_at"], 2)
            status["available"] = bool(status.get("available") and status["age_s"] < 5.0)
        else:
            status["available"] = False
        return status

    def ntc_snapshot(self):
        """★NTC (0xA2) 给 /api/status 用的拷贝.

        从没收到过 0xA2 就返回 None —— 不要在页面上编造 0.0 degC
        (和 vbus_not_fitted 一样: 没数据就说没数据)。
        temp_c 只在 valid 时给数值, 否则 None; temp_c_x10 原样带出方便排查。
        """
        with self.state_lock:
            ntc = dict(self.ntc) if self.ntc else None
        if ntc is None:
            return None
        temp_x10 = ntc.get("temp_c_x10")
        valid = bool(ntc.get("valid")) and temp_x10 != -32768
        now = time.time()
        # ★"已过温跳闸"的显示规则: 只在这两种情况下显示 ——
        #   ① 温度还在过温/禁止启动区间; 或 ② 刚恢复不到 NTC_TRIP_SHOW_S 秒。
        #   恢复够久就自动消失 (设备侧的锁存位照旧, 用 tripped_latched 带出去)。
        with self.state_lock:
            clear_t = self._ntc_clear_t if self._ntc_clear_t is not None else None
        recovered_s = None if clear_t is None else round(now - clear_t, 1)
        tripped_latched = bool(ntc.get("tripped"))
        tripped_shown = tripped_latched and (recovered_s is None or
                                             recovered_s < NTC_TRIP_SHOW_S)
        return {
            "temp_c": round(temp_x10 / 10.0, 1) if valid else None,
            "temp_c_x10": temp_x10,
            "raw": ntc.get("raw"),
            "valid": valid,
            "over": bool(ntc.get("over")),
            "tripped": tripped_shown,
            "tripped_latched": tripped_latched,
            "recovered_s": recovered_s,
            "fault": bool(ntc.get("fault")),
            "limit_c": ntc.get("limit_c"),
            "age_s": round(time.time() - ntc.get("t", 0.0), 2),
        }

    def _wake(self, arb, data):
        """Hand a frame to every request waiting for it, plus the per-request
        collectors registered for that channel.

        `arb` is the id the frame ARRIVED on (a device id), so it has to be
        compared with req.reply_id -- not with req.arb_id, which is the id we
        transmit to.  self.pending holds one LIST of requests per in-flight
        job, so the inner loop has to walk the requests themselves.
        """
        with self.pending_lock:
            for req_list in self.pending:
                for req in req_list:
                    if req.reply_id != arb or data[0] not in req.want:
                        continue
                    if req.cancelled or req.sent_at is None:
                        continue
                    if (arb == self.param_dev_id and data[0] == PRSP_PARAM and
                            req.data[0] == PCMD_GET_ONE and
                            (len(req.data) < 2 or len(data) < 2 or data[1] != req.data[1])):
                        continue
                    if data[0] == 0x91:
                        # Bank/chunk replies echo the selector; trace metadata
                        # and record-latch replies echo the operation instead.
                        op=req.data[1] if len(req.data)>2 else 0
                        tag=req.data[2] if op in (4,7) else op
                        if len(data)<2 or data[1]!=tag:
                            continue
                        if op==6 and data[2:4]!=req.data[2:4]:
                            continue
                    if data[0] == 0x8F and data[1:3] != req.data[1:3]:
                        continue
                    if req.reply is None:
                        req.reply = data
                        req.event.set()
            hooks = dict(self._param_hooks)

        # hook keyed by the DEVICE id of the channel that produced the frame
        hook = hooks.get(arb)
        if hook is not None:
            try:
                hook(data)
            except Exception:
                pass

    # -- decoders ----------------------------------------------------------
    def _on_text(self, text):
        # Counted only: the dashboard must not display CAN chatter.
        with self.state_lock:
            self.texts += 1
            self.last_text = text

    def _on_telem(self, data, now=None):
        """0xA1 TELEM: ia/ib/ic in mA (int16 LE) + vbus in 0.1 V units.

        Tolerant on purpose: a short or unknown frame is dropped and the reader
        keeps going.  This is the only decoder that feeds the live curves.
        """
        if len(data) < 8:
            with self.state_lock:
                self.bad_frames += 1
            return
        ia = s16(le16(data, 1))
        ib = s16(le16(data, 3))
        ic = s16(le16(data, 5))
        vbus = data[7] & 0xFF
        if now is None:
            now = time.time()
        self.telem.add(now, ia, ib, ic, vbus)
        with self.state_lock:
            self.telem_supported = True

    def _on_ntc(self, data, now=None):
        """0xA2 NTC: 温度帧 (固件在每帧 0xA1 之后紧接着发, 默认 20ms 周期).

        payload: [1..2] temp_c_x10 int16 LE (0x8000 = 读数无效)
                 [3..4] ntc_raw uint16 LE (ADC 原码, 现场标定分压拓扑用)
                 [5]    flags  bit0 有效 / bit1 >=45C / bit2 本次上电跳过闸
                                / bit3 传感器异常 (开路/短路/超范围)
                 [6]    跳闸阈值 (整数 degC)
                 [7]    保留
        和别的 _on_* 一样只写共享状态, 由 state_lock 保护。
        """
        if len(data) < 8:
            with self.state_lock:
                self.bad_frames += 1
            return
        if now is None:
            now = time.time()
        temp_x10 = s16(le16(data, 1))
        flags = data[5] & 0xFF
        valid = bool(flags & 0x01) and temp_x10 != -32768
        ntc = {
            "temp_c_x10": temp_x10,
            "raw": le16(data, 3),
            "flags": flags,
            # 0x8000 是"读数无效"哨兵, 不能当成 -3276.8 degC 用
            "valid": valid,
            "over": bool(flags & 0x02),
            "tripped": bool(flags & 0x04),
            "fault": bool(flags & 0x08),
            "limit_c": data[6] & 0xFF,
            "t": now,
        }
        with self.state_lock:
            self.ntc = ntc

        # ★"已过温跳闸"的自动消隐: 温度回到允许启动区间 (flags bit1 清零) 之后开始
        #   计时, 连续恢复 NTC_TRIP_SHOW_S 秒就不再显示那条提示。
        #   设备里的锁存位 (bit2) 不动 —— 那是"本次上电确实跳闸过"的事实记录,
        #   只是不再长期占着状态栏。
        if valid and not (flags & 0x02):
            if self._ntc_clear_t is None:
                self._ntc_clear_t = now
        else:
            self._ntc_clear_t = None

    def _on_encoder(self, data, now=None):
        """Decode AS5048A 0xA4 data; derived speed is display-only."""
        if len(data) < 8:
            with self.state_lock:
                self.bad_frames += 1
            return
        now = time.time() if now is None else now
        perf_now = time.perf_counter()   # ★转速的时间差用高分辨率时钟, 别用 time.time()
        raw = le16(data, 1)
        angle_cdeg = le16(data, 3)
        status = data[5] & 0xFF
        error_count = le16(data, 6)
        valid = bool(status & 0x01) and raw < ENCODER_COUNTS_PER_REV and angle_cdeg < 36000
        if valid:
            status_text = "数据有效"
        elif status & 0x02:
            status_text = "奇偶校验错误"
        elif status & 0x04:
            status_text = "传感器错误"
        elif status & 0x08:
            status_text = "SPI 超时"
        else:
            status_text = "编码器数据无效"

        with self.state_lock:
            speed_rpm = None
            anchor = self._encoder_anchor
            if valid:
                # ★转速用"~100ms 基线"求差, 不是相邻两帧 (2026-09-27 实测修正):
                #   ① 相邻帧只差 20ms, 编码器 ±5 count 的量化噪声就变成 ±0.9 rpm 的假转速;
                #   ② Windows 上 time.time() 只有 ~15.6ms 分辨率, 20ms 的间隔经常算出
                #      dt=0 -> 转速每隔一帧显示 None (实测就是 None/0.46 交替出现);
                #   ③ 所以时间差用 perf_counter() (亚微秒分辨率), time.time() 只留给
                #      页面显示"这帧有多新"。
                if anchor is None:
                    self._encoder_anchor = {"raw": raw, "tp": perf_now}
                else:
                    dt = perf_now - anchor["tp"]
                    if dt > ENCODER_SPEED_MAX_DT_S:
                        # 断了很久(丢帧/重启): 基线作废, 转速也清零, 别拿旧值糊弄
                        self._encoder_anchor = {"raw": raw, "tp": perf_now}
                        self._encoder_speed_rpm = None
                    elif dt >= ENCODER_SPEED_WINDOW_S:
                        half_turn = ENCODER_COUNTS_PER_REV // 2
                        delta = ((raw - anchor["raw"] + half_turn) %
                                 ENCODER_COUNTS_PER_REV) - half_turn
                        self._encoder_speed_rpm = delta * 60.0 / (ENCODER_COUNTS_PER_REV * dt)
                        self._encoder_anchor = {"raw": raw, "tp": perf_now}
                # ★沿用上一次算出的转速: 转速只在基线翻滚时(每 ~100ms)更新一次,
                #   若每帧都重新赋 None, 页面就会有 4/5 的时间显示"—"(实测踩到)。
                speed_rpm = self._encoder_speed_rpm
            else:
                self._encoder_anchor = None
                self._encoder_speed_rpm = None
            self.encoder_seq+=1
            self.encoder_history.append((self.encoder_seq,now,raw if valid else None,valid,(self.control_status or {}).get("calibration_stage")==3,error_count))
            if len(self.encoder_history)>4000:
                del self.encoder_history[:2000]
            self.encoder = {
                "received": True,
                "valid": valid,
                "raw": raw if raw < ENCODER_COUNTS_PER_REV else None,
                "angle_cdeg": angle_cdeg if angle_cdeg < 36000 else None,
                "angle_deg": (angle_cdeg / 100.0) if valid else None,
                "speed_rpm": speed_rpm,
                "status": status,
                "status_text": status_text,
                "error_count": error_count,
                "updated_at": now,
            }

    def encoder_batch(self,since=None):
        """Every received encoder frame, with its own host receive timestamp."""
        with self.state_lock:
            hist=list(self.encoder_history)
            seq=self.encoder_seq
        params={p["id"]:p for p in self.params_snapshot()}
        vals=[params.get(i,{}) for i in (0,1,2)]
        pp,zero,direction=[p.get("value") if p.get("known") else None for p in vals]
        stage=(self.control_snapshot() or {}).get("calibration_stage")
        calibrated=(isinstance(pp,int) and 1<=pp<=64 and isinstance(zero,int) and 0<=zero<16384 and direction in (1,2) and stage==3)
        rows=hist[-1:] if since is None or since>seq else [p for p in hist if p[0]>since]
        samples=[]
        for n,t,raw,valid,was_calibrated,errors in rows:
            angle=None
            if calibrated and was_calibrated and valid:
                delta=((raw-zero+8192)%16384)-8192
                angle=(delta*360.0/16384*pp*(-1 if direction==2 else 1))%360.0
            samples.append([n,t,angle,raw,valid,errors])
        return {"encoder_seq":seq,"encoder_samples":samples,"encoder_dropped":bool(hist and since is not None and since<hist[0][0]-1),"encoder_calibration_key":[pp,zero,direction,stage],"encoder_time_source":"host CAN receive time; separate from current telemetry"}

    def encoder_snapshot(self):
        """Return the latest encoder frame and whether it is still fresh."""
        with self.state_lock:
            sample = dict(self.encoder) if self.encoder else None
        if sample is None:
            return None
        sample["age_s"] = round(max(0.0, time.time() - sample["updated_at"]), 3)
        sample["stale"] = sample["age_s"] > ENCODER_FRESH_S
        if sample["stale"]:
            sample["speed_rpm"] = None
        return sample

    def _on_param(self, arb, data):
        if len(data) < 2:
            return
        pid = data[1]
        value = le32(data, 2)
        flags = data[6] if len(data) > 6 else 0
        with self.state_lock:
            self.info["param_%d" % pid] = {"value": value, "flags": flags,
                                           "t": time.time()}

    def _on_info(self, arb, data):
        if len(data) < 8:
            return
        info = {
            "active": data[1],
            "inactive": data[2],
            "state": data[3],
            "state_name": OTA_STATE_NAMES.get(data[3], "?%d" % data[3]),
            "try_left": data[4],
            "version": le16(data, 5),
            "flags": data[7],
            "t": time.time(),
        }
        with self.state_lock:
            self.info["info"] = info

    def _on_foc(self, arb, data):
        if len(data) < 6:
            return
        foc = {"loops": le32(data, 1), "init_rc": s8(data[5]), "t": time.time()}
        with self.state_lock:
            self.foc = foc

    def _on_heartbeat(self, arb, data):
        if len(data) < 8:
            return
        hb = {
            "role": data[1],
            "role_name": OTA_ROLE_NAMES.get(data[1], "?%d" % data[1]),
            "state": data[2],
            "state_name": OTA_STATE_NAMES.get(data[2], "?%d" % data[2]),
            "active": data[3],
            "self": data[4],
            "confirm_rc": s8(data[5]),
            "try_left": data[6],
            "flags": data[7],
            "t": time.time(),
        }
        with self.state_lock:
            self.hb = hb

    # -- request machinery -------------------------------------------------
    def _enqueue(self, job, priority=0, epoch=None):
        """Queue a job.  priority 0 = browser request, 1 = background poll.

        Browser requests jump ahead of the periodic device poll; without that,
        a silent device would make every button wait for the poll to time out.
        """
        with self.job_cv:
            self.job_q.append((priority, self.command_epoch if epoch is None else epoch, job))
            self.job_q.sort(key=lambda item: item[0])
            self.job_cv.notify()

    def _work_loop(self):
        while not self._stop:
            with self.job_cv:
                while not self.job_q and not self._stop:
                    self.job_cv.wait(0.5)
                if self._stop:
                    return
                _prio, epoch, job = self.job_q.pop(0)
                if epoch != self.command_epoch:
                    continue
                self.request_context.epoch = epoch
            try:
                job()
            except Exception as exc:
                self.add_error("request job failed: %s" % exc)

    def send_heartbeat(self):
        """0x17 HEARTBEAT straight onto the bus (no reply, bypasses the job queue:
        a slow request in the queue must not starve the firmware supervision)."""
        if self.bus is None:
            return False
        return self._send(self.param_host_id, bytes([PCMD_HEARTBEAT, 0, 0, 0, 0, 0, 0, 0]))

    def _send(self, arb_id, data):
        msg = Msg(arb_id, data)
        try:
            with self._tx_lock:
                self.bus.send(msg)
            with self.state_lock:
                self.tx_total += 1
            return True
        except Exception as exc:
            self.reason = ascii_text("CAN send failed: %s" % exc)
            self.add_error(self.reason)
            return False

    def _collect(self, requests):
        """Send each request once, then wait for its reply and mark it done.

        The caller (request()/get_all()) waits on req.done, so every exit path
        here -- including a failed send -- has to set it, otherwise the web
        thread would sit until its own timeout for no reason.
        """
        with self.pending_lock:
            self.pending.append(requests)
        send_ok = False
        try:
            for req in requests:
                # Serialize the last cancellation check and send with STOP's barrier.
                with self.job_cv:
                    epoch = req.epoch if req.epoch is not None else getattr(
                        self.request_context, "epoch", self.command_epoch)
                    if req.cancelled or epoch != self.command_epoch or self._stop:
                        req.cancel()
                        continue
                    req.started.set()
                    req.sent_at = time.monotonic()
                    ok = self._send(req.arb_id, req.data)
                send_ok = send_ok or ok
                if ok and self.verbose:
                    self.add_log("tx id=0x%03X %s (%s)"
                                 % (req.arb_id, hexs(req.data), req.name))
            # nothing reached the adapter: no point waiting for an answer
            if send_ok:
                for req in requests:
                    # every request has its own deadline, so one slow reply
                    # cannot extend the wait of the others
                    left = req.timeout - (time.monotonic() - req.sent_at) if req.sent_at else 0
                    if left > 0 and not self._stop:
                        req.event.wait(left)
        finally:
            with self.pending_lock:
                try:
                    self.pending.remove(requests)
                except ValueError:
                    pass
            for req in requests:
                req.done.set()

    def request(self, name, arb_id, reply_id, data, want, attempts=REQ_ATTEMPTS, priority=0):
        """No retransmission of writes. Expired queued requests cannot run later."""
        if self.bus is None:
            return None
        read_commands = {PCMD_GET_ONE, PCMD_MOTOR_STAT, PCMD_CTRL_STAT,
                         PCMD_CTRL_FEEDBACK, PCMD_MEAS_STAT, PCMD_TELEM_GET}
        if arb_id != self.param_host_id or data[0] not in read_commands:
            attempts = 1
        for attempt in range(max(1, attempts)):
            req = Request(name, arb_id, reply_id, data, want, timeout=REQ_TIMEOUT)
            with self.job_cv:
                req.epoch = getattr(self.request_context, "epoch", self.command_epoch)
                if req.epoch != self.command_epoch or (self.stopping and priority >= 0):
                    return None
                self.outstanding.add(req)
                job = lambda r=req: self._collect([r])
                self._enqueue(job, priority, req.epoch)
            try:
                if not req.done.wait(REQ_TIMEOUT + 0.3):
                    with self.job_cv:
                        req.cancel()
                    return None   # queue timeout is never another transmission
                if req.cancelled:
                    return None
                if req.reply is not None:
                    return req.reply
            finally:
                with self.job_cv:
                    self.outstanding.discard(req)
                    if req.cancelled:
                        self.job_q[:] = [item for item in self.job_q if item[2] is not job]
        return None

    def _stop_barrier(self):
        with self.job_cv:
            self.command_epoch += 1
            self.request_context.epoch = self.command_epoch
            self.job_q.clear()
            for req in self.outstanding:
                req.cancel()
            with self.pending_lock:
                for group in self.pending:
                    for req in group:
                        req.cancel()
            self.job_cv.notify_all()

    def _confirm_motor(self, enabled, mode=None, priority=0):
        reply = self.request("CONFIRM_MOTOR", self.param_host_id, self.param_dev_id,
                             bytes([PCMD_MOTOR_STAT]), (PRSP_MOTOR,), attempts=1,
                             priority=priority)
        if reply is None or len(reply) < 8:
            return False, "命令状态未确认：未收到新的电机状态；不要将 ACK 当作执行成功"
        self._on_motor_status(reply)
        good = ((reply[2] in (1, 2) and bool(reply[7] & 1) and not (reply[7] & 2))
                if enabled else (reply[2] == 0 and not (reply[7] & 1)))
        if enabled and mode is not None:
            good = good and reply[1] == mode
        return (True, "") if good else (False, "设备状态与命令不符，请检查故障和连接")

    def stop_motor(self):
        self.recorder.event("收到停止命令")
        with self.stop_lock:
            self.stopping = True
            try:
                self._stop_barrier()
                self.request("STOP", self.param_host_id, self.param_dev_id,
                             bytes([PCMD_MOTOR_CTRL, 0, 0, 0, 0, 0, 0, 0]),
                             (PRSP_ACK, PRSP_NACK), attempts=1, priority=-10)
                # Even if ACK is lost, a new status can confirm the actual stop.
                return self._confirm_motor(False, priority=-10)
            finally:
                self.stopping = False

    # -- high level operations --------------------------------------------
    def _store_param(self, pid, value, flags, now=None):
        """Remember one parameter reading for /api/status."""
        with self.state_lock:
            self.info["param_%d" % pid] = {
                "value": value, "flags": flags,
                "t": now if now is not None else time.time()}

    def parameter_table(self):
        with self.state_lock:
            version = (self.info.get("info") or {}).get("version")
        return parameter_table(version)

    def _parse_param_reply(self, data):
        pid = data[1]
        value = le32(data, 2)
        flags = data[6] if len(data) > 6 else 0
        known = next((p for p in self.parameter_table() if p["id"] == pid), None)
        return {
            "id": pid,
            "name": known["name"] if known else "id %d" % pid,
            "value": value,
            "unit": known["unit"] if known else "",
            "saved": bool(flags & PARAM_FLAG_SAVED),
            "is_default": bool(flags & PARAM_FLAG_DEFAULT),
            "flags": flags,
        }

    def fetch_one(self, pid):
        """GET_ONE.  Returns (param_dict, error_string)."""
        info = next((p for p in self.parameter_table() if p["id"] == pid), None)
        name = info["name"] if info else "id %d" % pid
        reply = self.request("GET_ONE(%s)" % name, self.param_host_id,
                             self.param_dev_id, bytes([PCMD_GET_ONE, pid & 0xFF]),
                             (PRSP_PARAM, PRSP_NACK))
        if reply is None:
            return None, self._no_reply("GET_ONE(%s)" % name)
        if reply[0] == PRSP_NACK:
            code = reply[5] if len(reply) > 5 else 0
            return None, "device NACK for %s: %s" % (name, perr_text(code))
        if len(reply) < 6:
            return None, "malformed PARAM reply for %s (%s)" % (name, hexs(reply))
        param = self._parse_param_reply(reply)
        self._store_param(param["id"], param["value"], param["flags"])
        return param, None

    def set_one(self, pid, value):
        """SET_ONE.  Returns (param_dict, error_string)."""
        if pid == 17:
            with self.state_lock:
                version = (self.info.get("info") or {}).get("version")
            if version is None:
                return None, "电阻单位尚未确认：请先读取固件版本"
        info = next((p for p in self.parameter_table() if p["id"] == pid), None)
        name = info["name"] if info else "id %d" % pid
        if info is not None:
            if value < info["min"] or value > info["max"]:
                return None, ("%s = %d is outside the firmware range %d..%d %s"
                              % (name, value, info["min"], info["max"],
                                 info["unit"]))
        if value < -0x80000000 or value > 0x7FFFFFFF:
            return None, "%s = %d does not fit in 32 bits" % (name, value)
        payload = bytes([PCMD_SET_ONE, pid & 0xFF]) + (to_i32(value) & 0xFFFFFFFF).to_bytes(4, "little")
        reply = self.request("SET_ONE(%s=%d)" % (name, value), self.param_host_id,
                             self.param_dev_id, payload, (PRSP_ACK, PRSP_NACK))
        if reply is None:
            return None, self._no_reply("SET_ONE(%s)" % name)
        if reply[0] == PRSP_NACK:
            code = reply[5] if len(reply) > 5 else 0
            return None, "device refused %s = %d: %s" % (name, value, perr_text(code))
        # read it back so the page shows what the device really holds
        param, err = self.fetch_one(pid)
        if param is None:
            return None, "写入结果未确认：参数回读失败；请读取设备参数后再试"
        if param['value'] != value:
            return None, "写入结果不一致：请求 %d，设备回读 %d" % (value, param['value'])
        self.recorder.event("参数 ID%d %s = %d" % (pid, name, value))
        return param, None

    def get_all(self):
        """GET_ALL.  Returns (dict_of_param_dicts, error_string).

        The PARAM (0x83) frames are unsolicited and cannot be matched to one
        request, so they are collected into a per-run map while ALL_END is the
        framed reply that ends the wait.
        """
        collected = {}
        if self.bus is None:
            return None, "GET_ALL: %s" % (self.reason or "CAN adapter is not open")

        def on_param(data):
            try:
                collected[data[1]] = self._parse_param_reply(data)
            except Exception:
                pass

        requests = [Request("GET_ALL", self.param_host_id, self.param_dev_id,
                            bytes([PCMD_GET_ALL]),
                            (PRSP_ALL_END, PRSP_NACK))]

        def job():
            # one attempt: the 0x84 ALL_END frame is itself the completion
            # signal, and repeating GET_ALL would only duplicate frames.  The
            # hook is keyed by the device id the PARAM frames arrive on.
            with self.pending_lock:
                self._param_hooks[self.param_dev_id] = on_param
            try:
                self._collect(requests)
            finally:
                with self.pending_lock:
                    self._param_hooks.pop(self.param_dev_id, None)

        self._enqueue(job)
        # wait for ALL_END (0x84); the individual PARAM frames arrive before it
        # and are all recorded by the hook, so ALL_END is the "table complete"
        # marker rather than the data carrier.
        timed_out = not requests[0].done.wait(REQ_TIMEOUT + 0.3)
        if timed_out:
            with self.job_cv:
                requests[0].cancel()
                self.job_q[:] = [item for item in self.job_q if item[2] is not job]
        # fold this run's readings into the shared state (also on a timeout:
        # whatever did arrive is still the freshest data we have)
        with self.state_lock:
            now = time.time()
            for pid, param in collected.items():
                self.info["param_%d" % pid] = {
                    "value": param["value"], "flags": param["flags"], "t": now}
        if timed_out:
            return None, self._no_reply("GET_ALL")

        reply = requests[0].reply
        if reply is not None and reply[0] == PRSP_NACK:
            code = reply[5] if len(reply) > 5 else 0
            return None, "device NACK for GET_ALL: %s" % perr_text(code)
        return collected, None

    def save(self):
        """SAVE (write the parameter page).  Returns (ok, error_string)."""
        reply = self.request("SAVE", self.param_host_id, self.param_dev_id,
                             bytes([PCMD_SAVE]), (PRSP_ACK, PRSP_NACK))
        if reply is None:
            return False, self._no_reply("SAVE")
        if reply[0] == PRSP_NACK:
            code = reply[5] if len(reply) > 5 else 0
            return False, "device refused SAVE: %s" % perr_text(code)
        # re-read so the page's "saved" flags match what is now in the flash
        self.get_all()
        return True, None

    def load(self):
        reply = self.request("LOAD", self.param_host_id, self.param_dev_id,
                             bytes([PCMD_LOAD]), (PRSP_ACK, PRSP_NACK))
        if reply is None:
            return False, self._no_reply("LOAD")
        if reply[0] == PRSP_NACK:
            code = reply[5] if len(reply) > 5 else 0
            return False, "device refused LOAD: %s" % perr_text(code)
        return True, None

    # -- telemetry control -------------------------------------------------
    def telem_ctrl(self, enable, period_ms):
        """TELEM_CTRL (0x06).  Returns (ok, negotiated_period_ms, error).

        Payload is exactly [0x06, enable, period_lo, period_hi, 0,0,0,0].
        The period is remembered locally even when the device refuses, so the
        selector keeps showing what the operator picked.
        """
        enable = 1 if enable else 0
        period_ms = max(TELEM_PERIOD_MIN, min(TELEM_PERIOD_MAX, int(period_ms)))
        with self.state_lock:
            self.telem_period_ms = period_ms
        if self.bus is None:
            err = self.reason or "CAN adapter is not open"
            with self.state_lock:
                self.telem_supported = False
                self.telem_last_err = err
            return False, period_ms, err
        payload = bytes([PCMD_TELEM_CTRL, enable, period_ms & 0xFF,
                         (period_ms >> 8) & 0xFF, 0, 0, 0, 0])
        reply = self.request("TELEM_CTRL(enable=%d,period=%d)" % (enable, period_ms),
                             self.param_host_id, self.param_dev_id, payload,
                             (PRSP_ACK, PRSP_NACK))
        if reply is None:
            err = self._no_reply("TELEM_CTRL")
            with self.state_lock:
                self.telem_supported = False
                self.telem_last_err = err
            return False, period_ms, err
        if reply[0] == PRSP_NACK:
            code = reply[5] if len(reply) > 5 else 0
            err = "device refused telemetry: %s" % perr_text(code)
            with self.state_lock:
                self.telem_supported = False
                self.telem_last_err = err
            return False, period_ms, err
        with self.state_lock:
            self.telem_supported = True
            self.telem_last_err = ""
        return True, period_ms, None

    def telemetry_on(self, enable, period_ms=None):
        """Apply the operator's telemetry wish and mirror it locally."""
        if period_ms is None:
            with self.state_lock:
                period_ms = self.telem_period_ms
        enable = 1 if enable else 0
        ok, period_ms, err = self.telem_ctrl(enable, period_ms)
        if ok:
            with self.state_lock:
                self.telem_enable = enable
                self.telem_period_ms = period_ms
        elif err:
            self.add_error(err)
        return ok, period_ms, err

    def telem_snapshot(self):
        """The "telem" object of /api/status, or None when nothing arrived.

        vbus_not_fitted reports the hardware fact from the final spec: this
        board has no bus-voltage sense channel, so the page must show "-" and
        must not treat a zero reading as a fault.
        """
        newest, rate, total = self.telem.snapshot()
        with self.state_lock:
            enable = self.telem_enable
            period = self.telem_period_ms
            supported = self.telem_supported
            last_err = self.telem_last_err
        if newest is None:
            return None
        t, ia, ib, ic, vbus = newest
        age = time.time() - t
        return {
            "ia_mA": ia,
            "ib_mA": ib,
            "ic_mA": ic,
            "vbus_dV": vbus,
            "age_s": round(age, 2),
            "rate_hz": rate,
            "count": total,
            "enable": enable,
            "period_ms": period,
            "supported": supported,
            "stale": age > TELEM_FRESH_AFTER,
            "vbus_not_fitted": True,
            "error": last_err,
        }

    def motor_ctrl(self, enable, step_ms=0, duty_permille=0, seconds=0, mode=0):
        """Motor enable/mode control (0x08 MOTOR_CTRL) -> (ok, error).

        payload 与固件 ota/ota_params.c 的 OTA_PCMD_MOTOR_CTRL 一致:
            [0x08, enable, mode, step_lo, step_hi, duty_lo, duty_hi, run_seconds]
        enable=1 启动(固件会自动先松抱闸), enable=0 停机(自动抱闸)。
        Modes 1..6 use encoder feedback; mode 0 is the existing sine open-loop.
        """
        if not enable:
            return self.stop_motor()
        if self.bus is None:
            return False, self.reason or "CAN adapter is not open"
        if enable and not self.device_alive():
            return False, "device is not responding on node %d; motor command was not sent" % self.node
        payload = bytes([PCMD_MOTOR_CTRL, 1 if enable else 0, mode & 0xFF,
                         step_ms & 0xFF, (step_ms >> 8) & 0xFF,
                         duty_permille & 0xFF, (duty_permille >> 8) & 0xFF,
                         seconds & 0xFF])
        reply = self.request("MOTOR_CTRL(enable=%d,duty=%d)" % (1 if enable else 0, duty_permille),
                             self.param_host_id, self.param_dev_id, payload,
                             (PRSP_ACK, PRSP_NACK))
        if reply is None:
            return False, self._no_reply("MOTOR_CTRL")
        if reply[0] == PRSP_NACK:
            code = reply[5] if len(reply) > 5 else 0
            reason = reply[6] if len(reply) > 6 else 0
            reasons = {1: "模式编号非法", 3: "开环步进时间超出范围",
                       4: "占空比超出范围", 5: "运行时间超出范围",
                       6: "温度保护未复归或 NTC 无效", 7: "请先执行本版编码器校准",
                       8: "没有新鲜有效的编码器读数", 9: "请先停止输出回到 IDLE",
                       10: "ADC 与 PWM 同步失败，检查诊断页 sync_code"}
            return False, "device refused motor command: %s" % reasons.get(reason, perr_text(code))
        ok, err = self._confirm_motor(True, mode)
        if ok:
            self.max_run_generation += 1
            self.max_output_snapshot = None
            if mode==7:
                threading.Thread(target=max_output.monitor,args=(self,self.max_run_generation),
                    name="foc-maximum-diagnostic",daemon=True).start()
            self.recorder.start(mode, seconds)
        return ok, err

    def cur_ctrl(self, target_ma, seconds=0):
        """★ 电流上限 (0x0B CUR_CTRL) -> (ok, error); 正弦模式下 = 扭矩/发热上限"""
        if self.bus is None:
            return False, self.reason or "CAN adapter is not open"
        t = max(0, min(20000, int(target_ma)))
        payload = bytes([PCMD_CUR_CTRL, t & 0xFF, (t >> 8) & 0xFF, int(seconds) & 0xFF, 0, 0, 0, 0])
        reply = self.request("CUR_CTRL(%d)" % t, self.param_host_id, self.param_dev_id,
                             payload, (PRSP_ACK, PRSP_NACK))
        if reply is None:
            return False, self._no_reply("CUR_CTRL")
        if reply[0] == PRSP_NACK:
            code = reply[5] if len(reply) > 5 else 0
            return False, "device refused current limit: %s" % perr_text(code)
        return True, ""

    def control_set(self, field, value):
        """Send one signed 32-bit 0x12 control setpoint."""
        if self.bus is None:
            return False, self.reason or "CAN adapter is not open"
        if not self.device_alive():
            return False, "device is not responding; setpoint was not sent"
        if value < -0x80000000 or value > 0x7FFFFFFF:
            return False, "setpoint does not fit in signed 32-bit"
        payload = bytes([PCMD_CTRL_SET, int(field) & 0xFF]) + int(value).to_bytes(4, "little", signed=True) + b"\0\0"
        reply = self.request("CONTROL_SET(%d=%d)" % (field, value),
                             self.param_host_id, self.param_dev_id, payload,
                             (PRSP_ACK, PRSP_NACK))
        if reply is None:
            return False, self._no_reply("CONTROL_SET")
        if reply[0] == PRSP_NACK:
            code = reply[5] if len(reply) > 5 else 0
            if int(field) == 12 and code == 2:
                return False, "当前固件不支持运行中开环调速；后续升级xige_foc应用后才能使用。"
            return False, "device refused setpoint: %s" % perr_text(code)
        self.control_targets[int(field)] = int(value)
        self.recorder.event("目标 field %d = %d" % (int(field), int(value)))
        return True, ""

    def calibration_ctrl(self, op):
        if op == 2:
            return self.stop_motor()
        if self.bus is None:
            return False, self.reason or "CAN adapter is not open"
        if op == 1 and not self.device_alive():
            return False, "device is not responding; calibration was not started"
        payload = bytes([PCMD_CALIB, int(op) & 0xFF, 0, 0, 0, 0, 0, 0])
        reply = self.request("ENCODER_CALIB(%d)" % op, self.param_host_id,
                             self.param_dev_id, payload, (PRSP_ACK, PRSP_NACK))
        if reply is None:
            return False, self._no_reply("ENCODER_CALIB")
        if reply[0] == PRSP_NACK:
            code = reply[5] if len(reply) > 5 else 0
            return False, "device refused calibration: %s" % perr_text(code)
        return self._confirm_motor(True, 254)

    def control_stat(self):
        if self.bus is None:
            return None, self.reason or "CAN adapter is not open"
        # Control clients must not depend on the lower-priority periodic poll.
        # A recorder can otherwise leave MOTOR_STAT stuck at PRECHARGE while
        # CONTROL_STAT/feedback already report RUN. Require a real reply here.
        motor_reply = self.request("CONTROL_MOTOR_STAT", self.param_host_id,
                                   self.param_dev_id, bytes([PCMD_MOTOR_STAT]),
                                   (PRSP_MOTOR, PRSP_NACK), attempts=1)
        if motor_reply is None or len(motor_reply) != 8 or motor_reply[0] != PRSP_MOTOR:
            return None, "电机状态读取失败：未收到新的 MOTOR_STAT 应答"
        self._on_motor_status(motor_reply)
        reply = self.request("CONTROL_STAT", self.param_host_id,
                             self.param_dev_id,
                             bytes([PCMD_CTRL_STAT, 0, 0, 0, 0, 0, 0, 0]),
                             (PRSP_CTRL, PRSP_NACK))
        if reply is None:
            return None, self._no_reply("CONTROL_STAT")
        if reply[0] == PRSP_NACK:
            code = reply[5] if len(reply) > 5 else 0
            return None, "device refused control status: %s" % perr_text(code)
        self._on_control_status(reply)
        # Old devices keep working; extended feedback is requested only on 0906+.
        if (self.info.get("info", {}).get("version", 0) or 0) >= 0x0906:
            self.request("CONTROL_FEEDBACK", self.param_host_id, self.param_dev_id,
                         bytes([PCMD_CTRL_FEEDBACK, 1, 0, 0, 0, 0, 0, 0]),
                         (PRSP_CTRL_FEEDBACK, PRSP_NACK))
        return self.control_snapshot(), None

    def control_snapshot(self):
        with self.state_lock:
            return dict(self.control_status) if self.control_status else None

    def brake_ctrl(self, release):
        """★抱闸 (0x0C BRAKE_CTRL) -> (ok, error)。release=1 松闸 / 0 抱闸。

        只驱动抱闸电磁铁 (PC13), 不给任何相输出 —— 用于"电机不转"的机械侧诊断:
        松闸后轴能用手自由转动 => 抱闸正常; 仍然锁死 => 抱闸/驱动/接线问题。
        """
        if self.bus is None:
            return False, self.reason or "CAN adapter is not open"
        rel = 1 if release else 0
        payload = bytes([PCMD_BRAKE_CTRL, rel, 0, 0, 0, 0, 0, 0])
        reply = self.request("BRAKE_CTRL(%d)" % rel, self.param_host_id, self.param_dev_id,
                             payload, (PRSP_ACK, PRSP_NACK))
        if reply is None:
            return False, self._no_reply("BRAKE_CTRL")
        if reply[0] == PRSP_NACK:
            code = reply[5] if len(reply) > 5 else 0
            return False, "device refused brake command: %s" % perr_text(code)
        return True, ""

    def phase_test(self, sa, sb, sc, duty_permille, seconds):
        """★静态相测试 (0x0A PHASE_TEST) -> (ok, error)。

        作用: 把一个**静止**的电压矢量加到三相上 (例如 A=上管PWM, B/C=下管常开),
        ⚠ 调用前必须先把电流目标清零 (cur_ctrl(0)), 否则限流环会来改 s_duty。
        """
        if self.bus is None:
            return False, self.reason or "CAN adapter is not open"
        d = max(0, min(1000, int(duty_permille)))
        payload = bytes([PCMD_PHASE_TEST, int(sa) & 0xFF, int(sb) & 0xFF, int(sc) & 0xFF,
                         d & 0xFF, (d >> 8) & 0xFF, int(seconds) & 0xFF, 0])
        reply = self.request("PHASE_TEST", self.param_host_id, self.param_dev_id,
                             payload, (PRSP_ACK, PRSP_NACK))
        if reply is None:
            return False, self._no_reply("PHASE_TEST")
        if reply[0] == PRSP_NACK:
            code = reply[5] if len(reply) > 5 else 0
            return False, "device refused phase test: %s" % perr_text(code)
        return self._confirm_motor(True, 255)

    def telem_batch(self, since):
        """One batch of telemetry samples for the page's curve poll.

        Called from HTTP handler threads.  `since` is the client's last seen
        sequence number, or None when the page has no cursor yet: then just the
        newest sample is returned (so a fresh page draws a point at once) plus
        "cursor": true, which tells it to adopt the seq we hand back.

        Never raises and never touches the CAN bus, so it answers in
        microseconds even with no adapter and no device attached.
        """
        try:
            newest, rate, total = self.telem.snapshot()
            cursor = since is None
            if cursor:
                seq, _ignored, allrows = self.telem.samples_since(0)
                rows = [allrows[-1]] if allrows else []
                dropped = False
            else:
                seq, dropped, rows = self.telem.samples_since(since)
            if len(rows) > TELEM_KEEP:
                rows = rows[-TELEM_KEEP:]
            with self.state_lock:
                enable = self.telem_enable
                period = self.telem_period_ms
            stale = bool(newest is not None and
                         (time.time() - newest[0]) > TELEM_FRESH_AFTER)
            out = {
                "ok": True,
                "seq": seq,
                "dropped": bool(dropped),
                "rate_hz": rate,
                "vbus_not_fitted": True,
                "enable": 1 if enable else 0,
                "period_ms": period,
                "stale": stale,
                "count": total,
                "samples": rows,
            }
            if cursor:
                out["cursor"] = True
            return out
        except Exception as exc:
            return {"ok": False,
                    "error": ascii_text("telemetry batch failed: %s" % exc)}

    def _no_reply(self, what):
        return ("%s: no reply from node %d within %.1f s (device id 0x%03X). "
                "Check that the firmware is running, the bus is terminated, the "
                "bitrate is right, and the node id matches."
                % (what, self.node, REQ_TIMEOUT, self.ota_dev_id))

    # -- status snapshot ---------------------------------------------------
    def device_alive(self):
        with self.state_lock:
            last = max(self.hb.get("t", 0.0), self.info.get("info", {}).get("t", 0.0))
        return last > 0.0 and (time.time() - last) < DEVICE_SILENT_AFTER

    def params_snapshot(self):
        with self.state_lock:
            readings = dict((k, dict(v)) for k, v in self.info.items()
                            if k.startswith("param_"))
        out = []
        for desc in self.parameter_table():
            key = "param_%d" % desc["id"]
            reading = readings.get(key)
            out.append({
                "id": desc["id"],
                "name": desc["name"],
                "value": reading["value"] if reading else None,
                "unit": desc["unit"],
                "saved": bool(reading["flags"] & PARAM_FLAG_SAVED) if reading else False,
                "is_default": bool(reading["flags"] & PARAM_FLAG_DEFAULT) if reading else False,
                "min": desc["min"],
                "max": desc["max"],
                "default": desc["def"],
                "known": reading is not None,
            })
        return out

    def status(self):
        now = time.time()
        with self.state_lock:
            info = dict(self.info.get("info") or {})
            foc = dict(self.foc)
            hb = dict(self.hb)
            last_rx = self.last_rx
            bad_frames = self.bad_frames
            err_frames = self.err_frames
            client_gone = self.client_gone
            tx_total = self.tx_total
            rx_total = self.rx_total
            telem_enable = self.telem_enable
            telem_period = self.telem_period_ms
            telem_last_err = self.telem_last_err
            control_status = dict(self.control_status) if self.control_status else None
        telem = self.telem_snapshot()
        connected = bool(self.ready and self.device_alive())
        if not self.ready:
            reason = self.reason or "CAN adapter is not open"
        elif connected:
            reason = "device answering on node %d" % self.node
        else:
            reason = ("no heartbeat from node %d yet - adapter open, but "
                      "nothing is talking on id 0x%03X"
                      % (self.node, self.ota_dev_id))
            # ★错误帧风暴 = 物理层问题, 直接给出可执行提示 (candleLight 实测)
            if err_frames > 50:
                reason += ("; ⚠ CAN 错误帧 %d 个 (总线无应答): 检查对面是否上电、"
                           "CANH/CANL 是否接对、终端电阻与波特率(500k)" % err_frames)
        # ONE short error line, and only for a real failure: normal bus chatter
        # (INFO, heartbeat, watchdog notes, the 1 Hz text) never lands here.
        err = self.last_error()
        error = None
        if not self.ready:
            error = reason
        elif err is not None:
            error = err["text"]
        # INFO (0x83 on the OTA channel) is the authoritative slot: it is the
        # direct answer to GET_INFO.  The heartbeat only carries a slot as
        # extra context, so it is the fallback, not the source.
        active = info.get("active", hb.get("active"))
        version = info.get("version")
        params = self.params_snapshot()
        encoder = self.encoder_snapshot()
        if encoder is not None and encoder.get("valid"):
            by_id = {item["id"]: item for item in params}
            pp = by_id.get(0, {})
            offset = by_id.get(1, {})
            direction = by_id.get(2, {})
            pp_value = pp.get("value") if pp.get("known") else None
            offset_value = offset.get("value") if offset.get("known") else None
            direction_value = direction.get("value") if direction.get("known") else None
            # ★control_snapshot() 在还没收到 CONTROL_STAT 之前返回 None。原来直接
            #   在它上面调 .get(), 于是"磁编帧有效 + 还没收到 CONTROL_STAT"这段启动
            #   窗口里整个 /api/status 抛 AttributeError -> HTTP 500 -> 页面所有状态卡
            #   空白 (2026-09-27 实测定位)。这里先兜成 {}。
            control_now = self.control_snapshot() or {}
            # Old parameter pages are not proof of corrected calibration.
            calibrated = (isinstance(pp_value, int) and 1 <= pp_value <= 64 and
                          isinstance(offset_value, int) and 0 <= offset_value < ENCODER_COUNTS_PER_REV and
                          direction_value in (1, 2) and
                          control_now.get("calibration_stage") == 3)
            encoder["electrical_angle_valid"] = bool(calibrated)
            encoder["electrical_calibrated"] = bool(calibrated)
            encoder["pole_pairs"] = pp_value if isinstance(pp_value, int) else None
            if calibrated:
                half_turn = ENCODER_COUNTS_PER_REV // 2
                delta = ((encoder["raw"] - offset_value + half_turn) %
                         ENCODER_COUNTS_PER_REV) - half_turn
                direction_sign = -1.0 if direction_value == 2 else 1.0
                encoder["electrical_angle_deg"] = (
                    delta * (360.0 / ENCODER_COUNTS_PER_REV) * pp_value * direction_sign
                ) % 360.0
            else:
                encoder["electrical_angle_deg"] = None
                encoder["electrical_angle_note"] = "等待有效的编码器校准参数"
        elif encoder is not None:
            encoder["electrical_angle_valid"] = False
            encoder["electrical_calibrated"] = False
            encoder["electrical_angle_deg"] = None
            encoder["pole_pairs"] = None
        return {
            "connected": connected,
            "reason": reason,
            "error": error,
            "adapter": {
                "open": bool(self.ready),
                "channel": getattr(self.bus, "channel", None),
                "bitrate": getattr(self.bus, "bitrate", None),
                "debug": self.reason if not self.ready else "",
            },
            "node": self.node,
            "host_id": "0x%03X" % self.ota_host_id,
            "device_id": "0x%03X" % self.ota_dev_id,
            "active_slot": SLOT_NAMES.get(active, "?") if active is not None else None,
            "active_slot_raw": active,
            "state": info.get("state_name") or hb.get("state_name"),
            "version": fmt_version(version),
            "version_raw": version,
            "foc_loops": foc.get("loops"),
            "foc_init_rc": foc.get("init_rc"),
            "try_left": info.get("try_left", hb.get("try_left")),
            "last_rx_age": round(now - last_rx, 1) if last_rx else None,
            "bad_frames": bad_frames,
            "err_frames": err_frames,
            "client_gone": client_gone,
            "telem": telem,
            "encoder": encoder,
            "ntc": self.ntc_snapshot(),
            "control": control_status,
            "feedback": self.feedback_snapshot(),
            "motor_profile": {
                "sel": ACTIVE_MOTOR["sel"],
                "default": MOTOR_PROFILE_DEFAULT,
                "options": [{"key": k, "label": v["label"]}
                            for k, v in MOTOR_PROFILES.items()],
                "data": motor_profile(),
            },
            "motor": self.motor_status_snapshot(),
            "telem_enable": telem_enable,
            "telem_period_ms": telem_period,
            "telem_error": telem_last_err,
            "params": params,
            "traffic": {"tx": tx_total, "rx": rx_total},
        }

    # -- background device poll -------------------------------------------
    # ---- ★阶段A: 诊断抓取/导出 (只读采样数据, 绝不改变控制量) ----
    DIAG_REC_SIZE = 32      # 与固件 foc_diag_sample_t 一致 (有静态断言)
    DIAG_REC_FMT = "<IIIhhhHHHHBBHH"   # seq,t_us,seq_acq,ia,ib,ic,ca,cb,cc,cnt1,flags,vsrc,vbus,step
    DIAG_MAX_FRAG = 40000

    def _diag_req(self, payload, expect, timeout=POLL_TIMEOUT):
        """在工作线程里发一条参数通道请求并等应答 (同步, 只在 job 里调用)。"""
        req = Request("DIAG", self.param_host_id, self.param_dev_id,
                      bytes(payload), tuple(expect), timeout=timeout)
        self._collect([req])
        return req.reply

    def diag_ctrl_job(self, op, n=0, decim=0):
        """op: 0=off 1=arm 2=abort 3=取采样质量统计"""
        if not self.ready or self._stop:
            return

        def job():
            d = self._diag_req([0x0D, op & 0xFF, n & 0xFF, (n >> 8) & 0xFF, decim & 0xFF],
                               (0x81, 0x82, 0x90))
            if d is None:
                return
            if d[0] == 0x90 and len(d) >= 8:
                with self.state_lock:
                    self.diag_stat = {
                        "rate_hz": le16(d, 1), "dropped": le16(d, 3),
                        "overrun": le16(d, 5),
                        "armed": bool(d[7] & 0x01), "output_on": bool(d[7] & 0x02),
                        # ★阶段A/4: ADC 是否已切到"与 PWM 同步的触发采样"
                        #   bit2 = 已锁相; bit3 = 尝试过但失败, 已回退自由跑
                        "sync_locked": bool(d[7] & 0x04),
                        "sync_fallback": bool(d[7] & 0x08),
                        # 高4位: 同步结果码 1=已同步 2=等不到触发 3=相位乱跳
                        #         4=校正不收敛 5=前置不满足
                        "sync_code": (d[7] >> 4) & 0x0F,
                        "updated_at": time.time(),
                    }
        self._enqueue(job, priority=1)

    def diag_stat_snapshot(self):
        with self.state_lock:
            return dict(getattr(self, "diag_stat", None) or {})

    def diag_dump_job(self, path, max_frag=None):
        """把固件环形缓冲整块读出来 -> CSV (给 scripts/diag_analyze.py 回放)。"""
        if not self.ready or self._stop:
            return
        limit = max_frag or self.DIAG_MAX_FRAG

        def job():
            import struct as _st
            blob = bytearray()
            frag = 0
            while frag < limit:
                d = self._diag_req([0x0E, frag & 0xFF, (frag >> 8) & 0xFF], (0x8F, 0x82))
                if d is None or len(d) < 8 or d[0] != 0x8F:
                    break                      # NACK = 读完
                blob.extend(d[3:8])            # 每片 5 字节记录切片
                frag += 1
            n = len(blob) // self.DIAG_REC_SIZE
            # 手写 CSV (不依赖 csv 模块, 减少对宿主环境的假设)
            with open(path, "w") as f:
                f.write("seq,t_us,seq_acq,ia,ib,ic,ca,cb,cc,cnt1,flags,vsrc,vbus_dv,step_ms\n")
                for i in range(n):
                    r = _st.unpack_from(self.DIAG_REC_FMT, blob, i * self.DIAG_REC_SIZE)
                    f.write("%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d\n"
                            % r)
            with self.state_lock:
                self.diag_dump = {"ok": True, "records": n, "frags": frag, "path": path}
        self._enqueue(job, priority=1)

    def diag_dump_snapshot(self):
        with self.state_lock:
            return dict(getattr(self, "diag_dump", None) or {})

    def poll_job(self):
        """Queue one GET_INFO + FOC_STAT + MOTOR_STAT pass (worker thread).

        Per-request timeout is shorter than a browser click and the job is
        queued at low priority, so the background poll can never make a button
        feel hung.  The parameter table is read on demand only (opening the
        <details> section), which keeps this poll cheap.
        """
        if not self.ready or self._stop:
            return

        def job():
            requests = [
                Request("GET_INFO", self.ota_host_id, self.ota_dev_id,
                        bytes([OTA_CMD_GET_INFO]), (OTA_RSP_INFO,),
                        timeout=POLL_TIMEOUT),
                Request("FOC_STAT", self.ota_host_id, self.ota_dev_id,
                        bytes([OTA_CMD_FOC_STAT]), (OTA_RSP_FOC,),
                        timeout=POLL_TIMEOUT),
                Request("MOTOR_STAT", self.param_host_id, self.param_dev_id,
                        bytes([PCMD_MOTOR_STAT]), (PRSP_MOTOR, PRSP_NACK),
                        timeout=POLL_TIMEOUT),
                # ★2026-10-14: 位置反馈也要周期轮询 —— 否则静止(不跑电机)时
                #   feedback.position_deg 一直是 stale/0, 手动转动转子时上位机
                #   看不到位置变化, 无法核对位置参数。
                Request("CONTROL_FEEDBACK", self.param_host_id, self.param_dev_id,
                        bytes([PCMD_CTRL_FEEDBACK, 1, 0, 0, 0, 0, 0, 0]),
                        (PRSP_CTRL_FEEDBACK, PRSP_NACK), timeout=POLL_TIMEOUT),
            ]
            # INFO / FOC_STAT are matched replies and would otherwise only be
            # folded into the state when the reader thread sees them -- store
            # them here too, so a poll whose reply was consumed by _wake still
            # lands in /api/status.
            self._collect(requests)
            info_frame = requests[0].reply
            if info_frame is not None:
                self._on_info(self.ota_dev_id, info_frame)
            foc_frame = requests[1].reply
            if foc_frame is not None:
                self._on_foc(self.ota_dev_id, foc_frame)
            self._motor_status_reply(requests[2].reply)

        self._enqueue(job, priority=1)


# ---------------------------------------------------------------------------
# HTML dashboard (inline on purpose: one self-contained file, works offline)
#
# Layout, top to bottom:
#   1. MotorTune header and device summary cards
#   2. connection, phase telemetry, motor state and explicit RPM controls
#   3. live traces with telemetry controls, followed by session events
# ---------------------------------------------------------------------------
INDEX_HTML = ''


# ---------------------------------------------------------------------------
# ★python-can 适配器 (candleLight / CANable, gs_usb 协议; 走 WinUSB, 没有 COM 口)
# ---------------------------------------------------------------------------
class PyCanBus(object):
    """把 python-can 的 Bus 包装成与 ZqwlBus 同名同义的最小接口。

    为什么需要: 本页原来只会讲 ZQWL 的串口厂商协议。换成 candleLight
    (gs_usb, 无 COM 口) 之后必须走 python-can。页面只用到:
        send(msg) / recv(timeout) / shutdown() + channel / bitrate 属性
    发送时把页面自己的 Msg 转成 can.Message (python-can 的 send 会检查类型)。
    """

    def __init__(self, interface, channel, bitrate, verbose=False):
        import usb_gs          # noqa: F401 给 pyusb 装 libusb 后端 (ZQWL 不受影响)
        import can

        self._can = can
        self.interface = interface
        try:
            ch = int(channel)
        except (TypeError, ValueError):
            ch = 0             # gs_usb 的 channel 是适配器序号, 不是 COM 口
        self.channel = ch
        self.bitrate = int(bitrate)
        self.verbose = verbose
        self.bus = can.Bus(interface=interface, channel=ch, bitrate=self.bitrate)
        # 与 ZqwlBus 同名计数器 (状态栏/诊断会读)
        self.rx_frames = 0
        self.tx_frames = 0
        self.dropped = 0
        self.heartbeats = 0
        self.last_heartbeat = None

    @property
    def state(self):
        try:
            return str(self.bus.state).split(".")[-1]
        except Exception:
            return None

    def send(self, msg):
        m = self._can.Message(
            arbitration_id=msg.arbitration_id,
            data=bytes(msg.data),
            is_extended_id=bool(getattr(msg, "is_extended_id", False)),
            is_remote_frame=bool(getattr(msg, "is_remote_frame", False)))
        self.bus.send(m)
        self.tx_frames += 1

    def recv(self, timeout=0.05):
        m = self.bus.recv(timeout=timeout)
        if m is not None:
            self.rx_frames += 1
        return m

    def stats(self):
        return ("%s ch%s @%d: tx=%d rx=%d"
                % (self.interface, self.channel, self.bitrate,
                   self.tx_frames, self.rx_frames))

    def shutdown(self):
        try:
            self.bus.shutdown()
        except Exception:
            pass

    def __del__(self):
        # ★必须兜底关闭: gs_usb 的句柄会一直占着 USB 接口, 没关掉的话下一次打开
        #   就是 [Errno 13] Access denied —— 实测页面重连失败几次后设备就再也打不开,
        #   只能重启页面才能解开。所以对象被回收时一定要 shutdown。
        self.shutdown()


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------
class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "foc_web/2.0"
    protocol_version = "HTTP/1.1"

    # keep the console clean: one line per request only in verbose mode
    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            sys.stdout.write("   [http] %s\n" % (fmt % args))
            sys.stdout.flush()

    # -- plumbing ----------------------------------------------------------
    def _send(self, body, ctype):
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except CLIENT_GONE:
            self._note_gone()

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Command-Epoch", str(getattr(self.server.app.link, "command_epoch", 0)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except CLIENT_GONE:
            self._note_gone()

    def _note_gone(self):
        """浏览器先断开: 只计数, 绝不进错误横幅 (见 CLIENT_GONE 的说明)。

        ⚠ 必须在这里也计一次: 异常是在 _send/_json 内部被吞掉的, 不会冒到
        do_GET 的兜底分支 —— 只在 do_GET 里计数会永远是 0 (实测踩到)。
        """
        try:
            self.server.app.link.note_client_gone()
        except Exception:
            pass

    def _status(self):
        """link.status() 的安全版本。

        ★为什么需要它: 适配器处于"半开"(检测到 7 秒收不到帧 -> 正在重开)的那一小段
        时间里, link.status() 可能抛异常。原来它会冒到 do_GET 的兜底分支变成
        HTTP 500 "internal error" —— 页面上所有状态卡会整块变空白, 看起来像"网页坏了",
        而真实原因只是适配器在重连。
        现在退化成一次正常应答: connected=false + 明确原因(适配器正在重连), 页面
        照常显示"未连接 + 原因", 其它卡片保持上一次的值。
        若之前拿到过完整状态, 就用它的结构, 保证字段齐全。
        """
        link = self.server.app.link
        try:
            status = link.status()
        except Exception as exc:
            status = dict(getattr(link, "_last_good_status", None) or {})
            for key, default in (("adapter", {}), ("motor", {}), ("traffic", {}),
                                 ("motor_profile", {}), ("params", []),
                                 ("encoder", None), ("telem", None),
                                 ("ntc", None)):
                status.setdefault(key, default)
            status["ok"] = True
            status["connected"] = False
            status["reason"] = "适配器状态读取失败(正在重连?): %s" % exc
            status["error"] = status["reason"]
        else:
            link._last_good_status = dict(status)
        return status

    def do_GET(self):
        self._dispatch(False)

    def do_POST(self):
        self._dispatch(True)

    def _dispatch(self, post):
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        app = self.server.app
        expected_hosts = {'127.0.0.1:%d' % self.server.server_port,
                          'localhost:%d' % self.server.server_port}
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin')
        if host not in expected_hosts or (origin is not None and origin != 'http://' + host):
            self._json({'ok': False, 'error': 'Host/Origin rejected'}, 403)
            return
        app.last_client = time.time()     # 页面看门狗: 通过 Host/Origin 检查的请求都算"页面还在"
        if route in MUTATING_ROUTES:
            if getattr(app, "closing", False):
                self._json({"ok": False, "error": "服务正在关闭"}, 409)
                return
            if not post:
                self._json({'ok': False, 'error': 'Control endpoints require authenticated POST'}, 405)
                return
            token = self.headers.get('X-Control-Token', '')
            if not secrets.compare_digest(token, app.control_token):
                self._json({'ok': False, 'error': 'Invalid control token; refresh dashboard'}, 403)
                return
            try:
                epoch = int(self.headers.get('X-Command-Epoch', '-1'))
            except ValueError:
                epoch = -1
            is_stop = ((route == '/api/motor' and query.get('enable') == ['0']) or
                       (route == '/api/control/calibrate' and query.get('op') == ['abort']))
            if not is_stop and epoch != getattr(app.link, 'command_epoch', 0):
                self._json({'ok': False, 'error': 'STOP cancelled this command sequence; read state before restarting'}, 409)
                return
            if hasattr(app.link, 'request_context'):
                app.link.request_context.epoch = epoch
        elif post:
            self._json({'ok': False, 'error': 'Read endpoints require GET'}, 405)
            return
        # Request bodies are deliberately unused; parameters stay in the URL.
        if post and (self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length', '0') != '0'):
            self._json({'ok': False, 'error': 'POST body must be empty'}, 400)
            return
        try:
            self._route(route, query)
        except CLIENT_GONE:
            # 浏览器先走了 (刷新/关标签/后台节流/请求被 abort): 记录计数即可,
            # 不能当成服务端错误显示到状态栏上 —— 见 CLIENT_GONE 的说明。
            self._note_gone()
        except Exception:
            tb = traceback.format_exc().splitlines()[-3:]
            app = self.server.app
            app.link.add_error("HTTP handler failed (%s)" % " | ".join(tb))
            self._json({"ok": False, "error": "internal error"}, status=500)

    def _route(self, route, query):
        app = self.server.app
        link = app.link

        if route == "/api/shutdown":
            with app.close_lock:
                if app.ota_busy():
                    self._json({"ok": False, "error": "OTA 进行中，不能关闭服务"}, 409)
                    return
                app.closing = True
                if link.bus is not None or app.armed_at is not None:
                    ok, error = link.stop_motor()
                    if not ok:
                        app.closing = False
                        self._json({"ok": False, "error": "停机未确认，服务保持运行：" + error}, 409)
                        return
                app.arm(False)
                # Run shutdown off the serving thread, even if the browser disconnects.
                try:
                    self._json({"ok": True})
                finally:
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        if route == "/":
            page = INDEX_HTML.replace('__CONTROL_TOKEN__', app.control_token).replace(
                '__COMMAND_EPOCH__', str(getattr(link, 'command_epoch', 0))).replace(
                '__MUTATING_ROUTES__', json.dumps(sorted(MUTATING_ROUTES))).replace(
                '__TUNE_CATALOG__', json.dumps([
                    {"id": p["id"], "default": p["def"],
                     "min": p["min"], "max": p["max"]}
                    for p in link.parameter_table()
                ], separators=(",", ":")))
            self._send(page.encode('utf-8'), "text/html; charset=utf-8")
            return

        if route == '/api/alive':
            # 页面的后台 Worker 每秒打一次: 标签页在后台被节流时也能喂看门狗
            self._json({'ok': True})
            return

        if route == '/api/session':
            self._json({'ok': True, 'token': app.control_token,
                        'command_epoch': getattr(link, 'command_epoch', 0)})
            return

        if route == "/guide":
            names={name: "工程说明.txt" for name in ("project", "workflow", "usage", "progress", "agents")}
            name=names.get(query.get("name",[""])[0])
            if name is None:
                self._json({"ok":False,"error":"unknown guide"},status=404)
                return
            root=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            with open(os.path.join(os.path.dirname(root),name),"rb") as f:
                self._send(f.read(),"text/plain; charset=utf-8")
            return

        if route == "/api/status":
            status = self._status()
            status["project_root"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            status["ota"] = app.ota_snapshot(include_output=False)
            self._json(status)
            return

        if route == "/api/diag/assist":
            # ★ 2026-09-28 审查要求: 启动助跑/环路诊断, 只拉 bank19/20, 所以可以高频轮询。
            # 用来替代"从累计 820 counts 猜交接时刻"。
            words = []
            for bank in (19, 20):
                reply = link.request("ADC_RAW", link.param_host_id, link.param_dev_id,
                    bytes([0x0D, 4, bank, 0, 0]), (0x91, 0x82))
                if reply is None or len(reply) != 8 or reply[0] != 0x91 or reply[1] != bank:
                    self._json({"ok": False, "error": "assist diag unavailable", "bank": bank})
                    return
                words.extend(le16(reply, i) for i in (2, 4, 6))
            def _s16(v):
                return v - 0x10000 if v >= 0x8000 else v
            self._json({"ok": True,
                "stage": words[0],
                "stage_name": {0: "IDLE", 1: "FORCE", 2: "BLEND", 3: "DONE"}.get(words[0], "?"),
                "travel_counts": _s16(words[1]),
                "flags": words[2],
                "voltage_saturated": bool(words[2] & 0x01),
                "iq_limited": bool(words[2] & 0x02),
                "speed_loop_saturated": bool(words[2] & 0x04),
                "position_loop_saturated": bool(words[2] & 0x08),
                "theta_deg": words[3] / 10.0,
                "speed_ref_rpm": _s16(words[4]),
                "speed_integral_a": _s16(words[5]) / 1000.0,
                "note": "bank19/20 合成诊断字; stage 1=FORCE 2=BLEND 3=DONE"})
            return

        if route == "/api/diag/raw":
            words = []
            # 0..18 = frozen raw snapshot, 19/20 = assist, 21..23 = control diag
            # (本拍 Id/Iq、内环更新次数、漏拍、DWT 耗时) —— 见 foc/foc_diag.c。
            for bank in range(24):
                reply = link.request("ADC_RAW", link.param_host_id, link.param_dev_id,
                    bytes([0x0D, 4, bank, 0, 0]), (0x91, 0x82))
                if reply is None or len(reply) != 8 or reply[0] != 0x91 or reply[1] != bank:
                    self._json({"ok": False, "error": "raw ADC bank unavailable", "bank": bank})
                    return
                words.extend(le16(reply, i) for i in (2, 4, 6))
            self._json({"ok": True, "raw": words[:4], "offset": words[4:8],
                "dma_observed": words[8:12], "seq": words[12] | words[13]<<16,
                "acquisition_irq_count": words[14] | words[15]<<16, "dma_remaining": words[16],
                "adc_dat": words[17], "adc_ctrl2": words[18] | words[19]<<16,
                "sync_mode": words[20], "ntc_raw_min": words[21],
                "ntc_raw_max": words[22], "ntc_rail_count_mod65536": words[23],
                "arm_failure_registers": words[24:33],
                "pwm": {"ccen": words[39], "bkdt": words[40], "ccmod1": words[41], "ccmod2": words[42], "ccr": words[43:46], "remap": words[46] | words[47]<<16, "gpioa_pl": words[48] | words[49]<<16, "gpioa_ph": words[50] | words[51]<<16, "gpiob_pl": words[52] | words[53]<<16, "gpioa_input": words[54], "gpiob_input": words[55], "cnt": words[56]},
                "motor_fault_reason": words[36], "motor_fault_peak_ma": words[37], "motor_ocp_ma": words[38],
                "launch_cnt": words[33], "publication_cnt": words[34], "launch_down": bool(words[35]),
                "assist": {"stage": words[57],
                           "stage_name": {0: "IDLE", 1: "FORCE", 2: "BLEND", 3: "DONE"}.get(words[57], "?"),
                           "travel_counts": (words[58] - 0x10000) if words[58] >= 0x8000 else words[58],
                           "flags": words[59], "theta_deg": words[60] / 10.0,
                           "speed_ref_rpm": (words[61] - 0x10000) if words[61] >= 0x8000 else words[61],
                           "speed_integral_a": ((words[62] - 0x10000) if words[62] >= 0x8000 else words[62]) / 1000.0},
                "control_diag": {
                           # bank21: 内环完成计数(32bit) + 漏拍次数
                           "updates": words[63] | words[64]<<16,
                           "overruns": words[65],
                           # bank22: 上次/最大 DWT 周期 (CPU cycles, 128MHz -> 7.8125ns)
                           "last_cycles": words[66], "max_cycles": words[67],
                           "last_us": words[66] / 128.0, "max_us": words[67] / 128.0,
                           # bank23: 本拍原始 Id/Iq (mA, int16)
                           "id_ma": (words[69] - 0x10000) if words[69] >= 0x8000 else words[69],
                           "iq_ma": (words[70] - 0x10000) if words[70] >= 0x8000 else words[70],
                           # ★诊断: PLL原始速度(words[68]) vs 外环实际用的速度(words[71]), rpm
                           "pll_rpm": (words[68] - 0x10000) if words[68] >= 0x8000 else words[68],
                           "meas_rpm": (words[71] - 0x10000) if words[71] >= 0x8000 else words[71]},
                "note": "raw/offset ordered A,B,C,NTC; dma observation may span scans; NTC extrema since boot"})
            return

        if route == "/api/control/set":
            if app.ota_busy():
                self._json({"ok": False, "error": "CAN-OTA is running"}, status=409)
                return
            field = query.get("field", [None])[0]
            value = self._float_arg(query, "value", None)
            desc = CONTROL_FIELDS.get(field)
            if desc is None or value is None or not math.isfinite(value):
                self._json({"ok": False, "error": "need a known field and finite value"},
                           status=400)
                return
            field_id, scale, low, high = desc
            if value < low or value > high:
                self._json({"ok": False,
                            "error": "%s must be in %.3g..%.3g" % (field, low, high)},
                           status=400)
                return
            firmware_value = value
            if field == "position_deg":
                firmware_value = position_output_to_rotor_deg(value)
            raw = int(round(firmware_value * scale))
            ok, err = link.control_set(field_id, raw)
            self._json({"ok": bool(ok), "field": field,
                        "value": value, "firmware_value": raw / float(scale), "error": err or None})
            return

        if route == "/api/control/calibrate":
            if app.ota_busy():
                self._json({"ok": False, "error": "CAN-OTA is running"}, status=409)
                return
            op = query.get("op", [""])[0].strip().lower()
            op_id = 1 if op == "start" else 2 if op == "abort" else 0
            if not op_id:
                self._json({"ok": False, "error": "op must be start or abort"}, status=400)
                return
            ok, err = link.calibration_ctrl(op_id)
            self._json({"ok": bool(ok), "op": op, "error": err or None})
            return

        if route == "/api/max-output/status":
            if app.ota_busy():
                self._json({"ok":False,"error":"CAN-OTA is running"},status=409)
                return
            try:
                if query.get('cached')==['1']:
                    with link.state_lock:maximum=link.max_output_snapshot
                    self._json({"ok":True,"maximumOutput":maximum,"diagnosticError":link.max_output_error})
                else:
                    self._json({"ok":True,"maximumOutput":max_output.status(link)})
            except ValueError as exc:
                self._json({"ok":False,"error":str(exc)},status=409)
            return

        if route == "/api/control/status":
            if app.ota_busy():
                self._json({"ok": True, "control": link.control_snapshot(),
                            "ota_busy": True})
                return
            control, err = link.control_stat()
            self._json({"ok": control is not None, "control": control,
                        "motor": link.motor_status_snapshot(),
                        "feedback": link.feedback_snapshot(), "error": err})
            return

        if route == "/api/ota/status":
            self._json(app.ota_snapshot())
            return

        if route == "/api/ota/onekey":
            if not app.begin_ota():
                self._json({"ok": False, "error": "CAN-OTA is already running",
                            "ota": app.ota_snapshot()}, status=409)
                return
            stopped, stop_err = link.motor_ctrl(0, 0, 0, 0, 0)
            if not stopped:
                app.finish_ota(1, "", error="无法确认电机已停止: %s" % stop_err)
                self._json({"ok": False, "error": app.ota_snapshot()["error"]},
                           status=409)
                return
            link.telemetry_on(0, link.telem_period_ms)
            # Release the exclusive CAN adapter before the OTA host opens it.
            ota_bus = link.bus
            ota_channel = getattr(ota_bus, "channel", None)
            ota_interface = getattr(ota_bus, "interface", None)
            if ota_interface is None:
                ota_interface = "zqwl"
            ota_env = {
                "OTA_CHANNEL": str(ota_channel if ota_channel is not None else "can0"),
                "OTA_BAUD": str(getattr(ota_bus, "bitrate", None) or app.args.baud or 500000),
                "OTA_NODE": str(link.node),
                "OTA_INTERFACE": str(ota_interface),
            }
            link.detach()
            app.bus = None
            app.launch_ota(ota_env)
            self._json({"ok": True, "ota": app.ota_snapshot()})
            return

        if route == "/api/reconnect":
            # ★ 手动重连: 立刻重开适配器, 不用等自动重连的 3 秒间隔。
            #   适配器从 USB 掉线后插回来, 点页面上的 Reconnect 就能恢复。
            app._reopen_at = 0.0
            if getattr(link, "bus", None) is None:
                app._ensure_bus()
            bus = getattr(link, "bus", None)
            self._json({"ok": bus is not None,
                        "channel": getattr(bus, "channel", None),
                        "error": None if bus is not None
                                 else (app._last_open_err or "adapter not found")})
            return

        if route == "/api/telem/batch":
            # ?since=SEQ -> every sample with seq > SEQ (oldest first).
            # A missing or malformed cursor is NOT an error: the page then gets
            # the newest sample only, plus "cursor": true, so a fresh tab can
            # draw one point and start tracking from the seq we return.
            # This handler answers from local memory only, so it can never hang
            # on a missing adapter or a silent device.
            b = link.telem_batch(self._since_arg(query))
            b.update(link.encoder_batch(self._since_arg({"since":query.get("encoder_since",[])})))
            self._json(b)
            return

        if route == "/api/phase":
            # ★手动静态矢量 (诊断用, 会略微拖动转子): ?sa=&sb=&sc=&duty=&seconds=
            #   sa/sb/sc: 0=FLOAT 1=PWM 2=LOW (与固件 FOC_PH_* 一致)
            #   用途: 相序核对(A/B/C 谁接谁)、自举是否充上电(看电流有没有)、
            #         手工量相电阻 (静态电压 / 电流)。
            # ⚠ 调用前请先把电流目标清零 (/api/cur?ma=0), 否则限流环会来改占空比。
            def _qph(name, dflt):
                v = query.get(name)
                if isinstance(v, (list, tuple)):
                    v = v[0] if v else None
                try:
                    return int(float(v))
                except (TypeError, ValueError):
                    return dflt

            sa = _qph("sa", -1)
            sb = _qph("sb", -1)
            sc = _qph("sc", -1)
            if min(sa, sb, sc) < 0 or max(sa, sb, sc) > 2:
                self._json({"ok": False,
                            "error": "need sa/sb/sc in 0..2 (0=FLOAT 1=PWM 2=LOW)"},
                           status=400)
                return
            duty = max(0, min(1000, _qph("duty", 60)))
            sec = _qph("seconds", 1)
            if sec < 1 or sec > 10:
                self._json({"ok": False,
                            "error": "phase-test seconds must be 1..10; no phase-test command was sent"},
                           status=400)
                return
            ok, err = link.phase_test(sa, sb, sc, duty, sec)
            self._json({"ok": bool(ok), "sa": sa, "sb": sb, "sc": sc,
                        "duty_permille": duty, "seconds": sec,
                        "error": err or None})
            return

        if route == "/api/brake":
            # ★抱闸手动控制 (诊断用, 不启动电机): ?release=1 松闸 / ?release=0 抱闸
            #   为什么需要: "电机不转"要先分清是【抱闸没松开】还是【力矩不够】——
            #   松闸后用手能不能转动轴, 一眼就能分开这两种原因。
            #   只发 BRAKE_CTRL(0x0C), 不给任何相输出。
            def _qb(name, dflt):
                v = query.get(name)
                if isinstance(v, (list, tuple)):
                    v = v[0] if v else None
                try:
                    return int(float(v))
                except (TypeError, ValueError):
                    return dflt

            rel = _qb("release", -1)
            if rel not in (0, 1):
                self._json({"ok": False, "error": "need release=0|1"}, status=400)
                return
            ok, err = link.brake_ctrl(rel)
            self._json({"ok": bool(ok), "release": rel,
                        "hint": "松闸后用手转轴: 能自由转 = 抱闸正常; 仍然锁死 = 抱闸/驱动问题",
                        "error": err or None})
            return

        if route == "/api/motor_profile":
            # ★电机档案: /api/motor_profile?sel=7010 (5014 已封存, 现在只有 7010 一份)
            #   档案决定: 转速直设的限流/电压默认、rpm↔step_ms 换算
            #   全部跟着变 (固件侧的档案在 foc/foc_config.h, 需要另外编译烧录)。
            sv = query.get("sel")
            if isinstance(sv, (list, tuple)):
                sv = sv[0] if sv else None
            if sv is None:
                self._json({"ok": True, "sel": ACTIVE_MOTOR["sel"],
                            "default": MOTOR_PROFILE_DEFAULT,
                            "options": [{"key": k, "label": v["label"]}
                                        for k, v in MOTOR_PROFILES.items()],
                            "data": motor_profile()})
                return
            ok, err = save_motor_sel(str(sv).strip().lower())
            self._json({"ok": bool(ok), "sel": ACTIVE_MOTOR["sel"],
                        "data": motor_profile(), "error": err or None})
            return

        if route == "/api/cur":
            # ★运行中实时改电流目标 (不重启电机): ?ma=NNN[&seconds=N]
            #   为什么需要这个接口: 正弦开环下实际电流≈这个目标, 而 I²R 就是发热本身。
            #   只有能扫这个值, 才能找到"还能转的最小电流" —— 那才是降升温的唯一杠杆。
            #   只发 CUR_CTRL(0x0B), 不动频率/占空比/输出使能。
            def _qc(name, dflt):
                v = query.get(name)
                if isinstance(v, (list, tuple)):
                    v = v[0] if v else None
                try:
                    return int(float(v))
                except (TypeError, ValueError):
                    return dflt

            ma = _qc("ma", -1)
            sec = _qc("seconds", 0)
            cur_max = motor_profile()["safe_cur_ma_max"]
            if ma < 0:
                self._json({"ok": False, "cur_ma": None,
                            "error": "need ma=0..%d (mA)" % cur_max},
                           status=400)
                return
            if ma > cur_max:
                ma = cur_max
            ok, err = link.cur_ctrl(ma, sec)
            self._json({"ok": bool(ok), "cur_ma": ma, "seconds": sec,
                        "error": err or None})
            return

        if route == "/api/motor":
            # ★ 正弦开环输出开关 + 目标电压 (键盘输入, 自动换算成占空比)
            #   ?enable=0|1[&volt=1.0][&vbus=48][&step_ms=500][&seconds=10]
            #   占空比 = 目标电压 / 母线电压 * 1000 (千分比), 固件硬上限 30%
            #   注意: 本板没有母线电压采样, vbus 是"你告诉我的假设值", 用来换算。
            def qget(name):
                v = query.get(name)
                if isinstance(v, (list, tuple)):
                    v = v[0] if v else None
                return v

            def qfloat(name, default):
                try:
                    return float(qget(name))
                except (TypeError, ValueError):
                    return default

            def qint(name, default):
                try:
                    return int(float(qget(name)))
                except (TypeError, ValueError):
                    return default

            if "enable" not in query:
                self._json({"ok": False, "error": "need enable=0|1"}, status=400)
                return
            enable = 1 if qint("enable", 0) else 0
            mode = qint("mode", RPM_MODE_DEFAULT)
            if mode < 0 or mode > CONTROL_MODE_MAX_WEB:
                self._json({"ok": False, "error": "mode must be in 0..7"}, status=400)
                return
            if app.ota_busy():
                self._json({"ok": False, "error": "CAN-OTA is running"}, status=409)
                return
            seconds = qint("seconds", 0)
            if enable:
                # 0 = 不限时 (靠页面看门狗 + 固件心跳自动停机, 见 WATCHDOG_S)
                if seconds < 0 or seconds > 255:
                    self._json({"ok": False, "error": "seconds must be 0..255 (0 = no limit)"}, 400)
                    return
            else:
                app.arm(False)
                ok, err = link.motor_ctrl(0, 0, 0, 0, 0)
                self._json({"ok": bool(ok), "enable": 0, "mode": mode,
                            "error": err or None})
                return
            if mode != RPM_MODE_DEFAULT:
                ok, err = link.motor_ctrl(1, 0, 0, seconds, mode)
                if ok:
                    app.arm(True)
                self._json({"ok": bool(ok), "enable": 1, "mode": mode,
                            "seconds": seconds, "error": err or None})
                return
            vbus = qfloat("vbus", 48.0)
            step_ms = qint("step_ms", 500)
            # ★ 转速直设: ?rpm=NN -> step_ms, 换算要用【电机档案】的极对数:
            #   f_elec = 1000/(step_ms·6);  rpm = 60·f_elec/p = 10000/(step_ms·p)
            #   => step_ms = 10000/(rpm·p)     (p=10 时就是老的 1000/rpm)
            prof = motor_profile()
            _pp = 10
            try:
                _pp = int(prof["nameplate"].get("pole_pairs") or 10)
            except Exception:
                _pp = 10
            if _pp < 1:
                _pp = 10
            rpm_asked = qint("rpm", 0)
            rpm_eff = 0
            if rpm_asked > 0:
                if rpm_asked > RPM_MAX_SAFE:
                    rpm_asked = RPM_MAX_SAFE
                if rpm_asked < RPM_MIN:
                    rpm_asked = RPM_MIN
                step_ms = int(round(10000.0 / (float(rpm_asked) * _pp)))
                step_ms = max(5, min(5000, step_ms))
            if step_ms > 0:
                rpm_eff = int(round(10000.0 / (float(step_ms) * _pp)))
            volt = qfloat("volt", None)
            if volt is None:
                # ★ 转速直设路径: 电压天花板固定 3V。
                #   绝不能沿用 link.motor_volt —— 停机(enable=0)会把 duty 打成 0,
                #   于是 motor_volt 被记成 0.0, 下一次不带 volt 的转速启动就会被
                #   "target voltage must be 0.1..5.0 V" 直接拒掉(实测踩到过)。
                if "volt" not in query:
                    volt = prof["safe_volt"] if rpm_asked > 0 else getattr(link, "motor_volt", 1.0)
            if enable and (not math.isfinite(volt) or volt < 0.1 or volt > MOTOR_VOLT_MAX):
                self._json({"ok": False, "enable": 0,
                            "error": "target voltage must be 0.1..%.1f V; no motor command was sent"
                                     % MOTOR_VOLT_MAX}, status=400)
                return
            if enable and (not math.isfinite(vbus) or vbus < 1.0 or vbus > 60.0):
                self._json({"ok": False, "enable": 0,
                            "error": "bus voltage must be 1..60 V; no motor command was sent"},
                           status=400)
                return
            if enable and (step_ms < 5 or step_ms > 5000):
                self._json({"ok": False, "enable": 0,
                            "error": "step time must be 5..5000 ms; no motor command was sent"},
                           status=400)
                return
            # ★ 安全上限: 电流封顶 = "不发热"的真正保证 (铜损与转速无关, 只跟电流有关)
            cur_ma = qint("cur_ma", 0)
            if cur_ma > prof["safe_cur_ma_max"]:
                cur_ma = prof["safe_cur_ma_max"]
            if rpm_asked > 0 and enable:
                if cur_ma <= 0:
                    cur_ma = prof["safe_cur_ma_default"]   # 只用转速时: 自动上安全电流
                # ★ 电压天花板: 电流环只能把占空比加到 s_duty_cmd 为止,
                #   给太小电流环够不到目标电流 -> 电机没力/不转。
                if "volt" not in query:
                    volt = prof["safe_volt"]
            duty = 0
            if enable:
                duty = int(round(max(0.0, volt) / vbus * 1000.0))
                duty = max(1, min(MOTOR_DUTY_MAX, duty))
            ok, err = link.motor_ctrl(enable, step_ms, duty, seconds, mode)
            if ok and cur_ma > 0:
                cok, cerr = link.cur_ctrl(cur_ma, seconds)
                if not cok:
                    # ★电流环命令失败必须让 ok=False。否则网页显示"运行中",
                    #   而固件里 s_cur_mode 仍是 0 -> 占空比不爬升, 只剩首步占空比
                    #   -> 电机没力不动, 现场表现就是"网页说在跑、电机不动",
                    #   而且【页面上看不到任何错误】(2026-09-24 实测踩到)。
                    link.motor_ctrl(0, 0, 0, 0, 0)
                    ok, err = False, cerr
            if ok and duty > 0:
                # 只在真的有输出时记住电压, 否则停机(enable=0)会把它记成 0.0
                link.motor_volt = (duty / 1000.0) * vbus
            app.arm(bool(ok and enable))
            self._json({"ok": bool(ok), "enable": enable, "seconds": seconds,
                        "volt": round((duty / 1000.0) * vbus, 3), "vbus": vbus,
                        "duty_permille": duty, "step_ms": step_ms,
                        "rpm": rpm_eff, "cur_ma": cur_ma, "mode": mode,
                        "rpm_max_safe": RPM_MAX_SAFE,
                        "error": err or None})
            return

        if route == "/api/diag/stat":
            # ★阶段A: 采样质量 (样本率/丢样/覆盖) —— 阶段A 验收要的数据
            link.diag_ctrl_job(3)
            self._json({"ok": True, "stat": link.diag_stat_snapshot()})
            return

        if route == "/api/diag/arm":
            n = self._int_arg(query, "n", 1024)
            if not n or n < 1 or n > 1024:
                n = 1024
            decim = self._int_arg(query, "decim", 50)
            if not decim or decim < 4 or decim > 200:
                decim = 50
            link.diag_ctrl_job(1, n, decim)
            self._json({"ok": True, "armed": n, "decim": decim,
                        "note": "抓取中; 抓满 n 条会自动停"})
            return

        if route == "/api/diag/off":
            link.diag_ctrl_job(0)
            self._json({"ok": True})
            return

        if route == "/api/diag/dump":
            # ★阶段A: 把环形缓冲整块导出成 CSV (后台线程分片读, 不阻塞 HTTP)
            dump_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "diag_capture.csv")
            os.makedirs(os.path.dirname(dump_path), exist_ok=True)
            link.diag_dump_job(dump_path)
            self._json({"ok": True, "dump": link.diag_dump_snapshot(),
                        "note": "后台抓取中; 完成后再查这个接口看 records, "
                                "文件是 xige_foc/logs/diag_capture.csv, 用 scripts/diag_analyze.py 回放"})
            return

        if route == "/api/telem":
            # ?enable=0|1[&period_ms=NNN] -> TELEM_CTRL (0x06) on the PARAM
            # channel: [0x06, enable, period_lo, period_hi, 0,0,0,0].
            if "enable" not in query:
                self._json({"ok": False,
                            "error": "need enable=0|1 (period_ms optional)"},
                           status=400)
                return
            enable = self._int_arg(query, "enable", None)
            if enable is None:
                self._json({"ok": False, "error": "enable must be 0 or 1"},
                           status=400)
                return
            period = self._int_arg(query, "period_ms", None)
            if period is None:
                period = link.telem_period_ms
            ok, negotiated, err = link.telemetry_on(1 if enable else 0, period)
            if ok:
                self._json({"ok": True, "enable": 1 if enable else 0,
                            "period_ms": negotiated})
            else:
                self._json({"ok": False, "enable": 0, "period_ms": negotiated,
                            "error": err or "telemetry request failed"})
            return

        if route in ("/api/set", "/api/save", "/api/load"):
            if app.ota_busy():
                self._json({"ok": False, "error": "CAN-OTA is running"}, status=409)
                return
            reply = link.request("MOTOR_STAT", link.param_host_id, link.param_dev_id,
                                 bytes([PCMD_MOTOR_STAT]), (PRSP_MOTOR, PRSP_NACK))
            if reply is None or reply[0] != PRSP_MOTOR:
                self._json({"ok": False, "error": "无法确认输出已停止"}, status=409)
                return
            link._on_motor_status(reply)
            motor = link.motor_status_snapshot()
            if motor.get("output_on"):
                self._json({"ok": False, "error": "请先停止输出，再修改或保存控制参数"}, status=409)
                return

        if route == "/api/set":
            if "id" not in query or "value" not in query:
                self._json({"ok": False, "error": "need id and value"}, 400)
                return
            pid = self._int_arg(query, "id", None)
            value = self._int_arg(query, "value", None)
            if pid is None or value is None:
                self._json({"ok": False, "error": "id and value must be integers"}, 400)
                return
            if pid not in PARAM_BY_ID:
                self._json({"ok": False, "error": "unknown parameter id %d" % pid}, 400)
                return
            param, err = link.set_one(pid, value)
            if param is None:
                self._json({"ok": False, "error": err, "id": pid, "value": value})
            else:
                self._json({"ok": True, "id": param["id"], "value": param["value"],
                            "unit": param["unit"], "saved": param["saved"]})
            return

        if route == "/api/getall":
            try:
                params, err = link.get_all()
            except Exception as exc:
                # 同上: 适配器半开时不要 500, 给一条能读懂的原因
                params, err = None, "读取参数失败(适配器正在重连?): %s" % exc
            status = self._status()
            if params is None:
                status["ok"] = False
                status["error"] = err
            else:
                status["ok"] = True
            self._json(status)
            return

        if route == "/api/save":
            ok, err = link.save()
            if ok:
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": err})
            return

        if route == "/api/get":
            pid = self._int_arg(query, "id", None)
            if pid is None or pid not in PARAM_BY_ID:
                self._json({"ok": False, "error": "need a known id"}, 400)
                return
            param, err = link.fetch_one(pid)
            if param is None:
                self._json({"ok": False, "error": err})
            else:
                param["ok"] = True
                self._json(param)
            return

        if route == "/api/load":
            ok, err = link.load()
            self._json({"ok": ok} if ok else {"ok": False, "error": err})
            return

        self._json({"ok": False, "error": "unknown endpoint %s" % route}, 404)

    @staticmethod
    def _int_arg(query, key, default):
        if key not in query or not query[key]:
            return default
        try:
            return int(str(query[key][0]).strip(), 0)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _float_arg(query, key, default):
        if key not in query or not query[key]:
            return default
        try:
            value = float(str(query[key][0]).strip())
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    @staticmethod
    def _since_arg(query):
        """?since=N -> N (>= 0), or None for "no usable cursor".

        Deliberately strict and decimal-only: "since=0" IS a cursor (a client
        that has seen nothing yet), while a missing, negative or non-numeric
        value means "I have no cursor" and gets the newest sample instead.
        """
        if "since" not in query or not query["since"]:
            return None
        try:
            value = int(str(query["since"][0]).strip(), 10)
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None


class App(object):
    """Wires the CAN link to the HTTP server."""

    def __init__(self, args):
        self.args = args
        self.control_token = secrets.token_urlsafe(32)
        self.closing = False
        self.close_lock = threading.Lock()
        self.link = None
        self.bus = None
        self._poll_stop = False
        self._poll_thread = None
        self._reopen_at = 0.0        # ★ 下次尝试重开适配器的时刻
        self._last_open_err = ""     # ★ 最近一次重开失败的原因
        self._bus_since = time.time()  # ★ 当前句柄是什么时候打开的
        self._ota_lock = threading.Lock()
        self._ota = {"state": "idle", "started_at": None, "finished_at": None,
                     "returncode": None, "error": None, "output": []}
        self.last_client = time.time()   # 最近一次收到页面/脚本请求
        self.armed_at = None             # 本服务启动了电机 (None = 没有)
        self._safety_thread = None

    def arm(self, on):
        self.armed_at = time.time() if on else None

    def _safety_loop(self):
        """电机由本服务启动后: 页面还在 -> 每 HEARTBEAT_S 发一帧 0x17;
        页面 WATCHDOG_S 没动静 -> 停机。设备报告已停 (时限到/故障/别处停) -> 解除。"""
        fired = False
        while not self._poll_stop:
            time.sleep(HEARTBEAT_S)
            link, armed = self.link, self.armed_at
            if link is None or armed is None or self.ota_busy():
                fired = False
                continue
            m = link.motor_status_snapshot() or {}
            if m.get("updated_at", 0) > armed + 1.0 and not m.get("output_on"):
                self.armed_at = None
                continue
            if time.time() - self.last_client < WATCHDOG_S:
                link.send_heartbeat()
                fired = False
            elif not fired:
                fired = True
                link.add_error("网页 %d 秒没有请求 (页面关闭/卡死/断网?), 已自动停机" % WATCHDOG_S)
                try:
                    ok, _err = link.stop_motor()
                except Exception:
                    ok = False
                if ok:
                    self.armed_at = None

    def ota_busy(self):
        with self._ota_lock:
            return self._ota["state"] in ("stopping", "running")

    def ota_snapshot(self, include_output=True):
        with self._ota_lock:
            out = dict(self._ota)
            if include_output:
                out["output"] = list(self._ota["output"])
            else:
                out.pop("output", None)
            out["running"] = out["state"] in ("stopping", "running")
            out["project"] = "xige_foc"
            return out

    def begin_ota(self):
        with self._ota_lock:
            if self._ota["state"] in ("stopping", "running"):
                return False
            self._ota = {"state": "stopping", "started_at": time.time(),
                         "finished_at": None, "returncode": None,
                         "error": None, "output": []}
            return True

    def finish_ota(self, returncode, output_line="", error=None):
        with self._ota_lock:
            if output_line:
                self._ota["output"].append(ascii_text(output_line, 500))
                self._ota["output"] = self._ota["output"][-120:]
            self._ota.update({"state": "done" if returncode == 0 else "failed",
                              "finished_at": time.time(),
                              "returncode": int(returncode),
                              "error": error})
        self._reopen_at = 0.0

    def _ota_output_line(self, line):
        line = line.rstrip("\r\n")
        if not line:
            return
        with self._ota_lock:
            self._ota["output"].append(ascii_text(line, 500))
            self._ota["output"] = self._ota["output"][-120:]

    def launch_ota(self, ota_env=None):
        with self._ota_lock:
            self._ota["state"] = "running"
            self._ota_env = dict(ota_env or {})
        thread = threading.Thread(target=self._run_ota_onekey, name="foc-web-ota")
        thread.daemon = True
        thread.start()

    def _run_ota_onekey(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        runner = os.path.join(project_root, "scripts", "ota_onekey.py")
        command = [sys.executable, runner]
        kwargs = {"cwd": project_root, "stdout": subprocess.PIPE,
                  "stderr": subprocess.STDOUT, "text": True,
                  "encoding": "utf-8", "errors": "replace", "bufsize": 1}
        env = os.environ.copy()
        with self._ota_lock:
            env.update(getattr(self, "_ota_env", {}))
        kwargs["env"] = env
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            self._ota_output_line("Running make ota-onekey in xige_foc")
            proc = subprocess.Popen(command, **kwargs)
            with self._ota_lock:
                self._ota["pid"] = proc.pid
            for line in proc.stdout:
                self._ota_output_line(line)
            rc = proc.wait()
            self.finish_ota(rc, error=None if rc == 0 else "ota-onekey exited with code %d" % rc)
        except Exception as exc:
            self.finish_ota(1, error="cannot run ota-onekey: %s" % ascii_text(exc, 300))

    def open_bus(self):
        """Open the adapter ONCE.  Never fatal: the page must still come up.

        三条路, 按顺序:
          1) --interface 明确指定 (gs_usb / slcan / pcan ...) -> 走 python-can;
          2) 否则自动探测 ZQWL 串口适配器 (老路径, 厂商串口协议);
          3) ZQWL 找不到 -> 再试 python-can 的 gs_usb (candleLight/CANable, 无 COM 口)。
        都不行就把两边的错误原因一起报出来, 页面照样能打开 (不会因为没适配器起不来)。
        """
        iface = (getattr(self.args, "interface", None) or "").strip()

        # ---- 1) 明确指定了 python-can 后端 ----
        if iface and iface.lower() not in ("zqwl", "serial"):
            return self._pycan_open(iface, self.args.channel or 0)

        errs = []
        # ---- 2) ZQWL 串口 ----
        if ZQWL_IMPORT_ERROR is None:
            channel = self.args.channel
            if not channel:
                channel = find_zqwl_port()
            if channel:
                try:
                    bus = ZqwlBus(channel, bitrate=self.args.baud,
                                  verbose=self.args.verbose)
                    return bus, None
                except ZqwlError as exc:
                    errs.append(ascii_text(exc))
                except Exception as exc:
                    errs.append(ascii_text("cannot open CAN adapter %s: %s" % (channel, exc)))
            else:
                errs.append("no ZQWL adapter auto-detected (USB VID 0x%04X)"
                            % getattr(zqwl, "ZQWL_VID", 0x3562))
        else:
            errs.append(ascii_text(ZQWL_IMPORT_ERROR))

        # ---- 3) candleLight / gs_usb (无 COM 口, 走 WinUSB) ----
        if not iface:
            bus, err = self._pycan_open("gs_usb", 0)
            if bus is not None:
                return bus, None
            errs.append(err)

        return None, ascii_text("no CAN adapter usable -- " + " | ".join(errs))

    def _pycan_open(self, iface, channel):
        """打开一个 python-can 适配器; gs_usb 采用"先复位再打开"的稳妥顺序。

        实测 (candleLight) 有两种状态:
          a) 正常: 直接 can.Bus() 就开;
          b) 卡死: 设备在、驱动 WinUSB、claim_interface 都成功, 但 gs_usb 的启动
             序列报 USBError [Errno 13] Access denied。
        b) 状态下【先复位 USB 设备】再打开就能成 —— 而"先尝试打开、失败后再复位"
        是不行的: 失败那一次会泄漏句柄, 同一进程里再复位也打不开了 (实测踩到)。
        所以 gs_usb 一律先复位 (约 1.5s), 再打开; 仍然失败才报错。
        """
        is_gs = iface.lower() == "gs_usb"
        for attempt in (1, 2, 3):
            try:
                bus = PyCanBus(iface, channel, self.args.baud, self.args.verbose)
                return bus, None
            except Exception as exc:
                first = exc
            if not is_gs:
                break
            # ★gs_usb 失败后的两步抢救 (实测必须做, 否则同进程里再也打不开):
            #   ① dispose_resources 释放被 claim 的接口 (库自己不会释放);
            #   ② 复位 USB 设备; 然后再试。第 3 次还失败就报错让用户拔插。
            try:
                import usb_gs
                usb_gs.dispose_gs_usb()
                usb_gs.reset_gs_usb(wait=1.5)
            except Exception:
                pass
        return None, ascii_text("cannot open %s via python-can: %s" % (iface, first))

    # ★ 适配器掉线自动重连间隔(秒)。用户痛点原话: "网页没反应, 必须稳定解决,
    #   不能总是出这种问题, 更新完了网页老是用不了" —— 根因就是适配器只在启动时
    #   打开一次, 之后从 USB 掉了(实测多次)页面就永远救不回来。现在每 3 秒重试。
    REOPEN_INTERVAL = 3.0
    # ★ 适配器"假活"判定: 曾经收到过帧, 但已连续这么多秒一帧都收不到
    #   -> 认为句柄死了, 强制重开(实测 PermissionError(13) 就是这个状态)
    DEAD_AFTER_S = 6.0

    def _ensure_bus(self):
        """适配器没了/假死了就重新开一个, 不用重启网页。"""
        if self.link is None:
            return
        if self.ota_busy():
            return
        now = time.time()
        bus = getattr(self.link, "bus", None)
        if bus is not None:
            # ★两类适配器的"假活"判据不同:
            #   - python-can (gs_usb/candleLight): 总线静默【是正常状态】(对面没上电
            #     就是零帧), 绝不能因为"没帧"就摘手柄 —— 实测那会变成每 6 秒把好好的
            #     适配器拆掉再开, 页面一直显示未连接, 还会顺带泄漏 USB 句柄。
            #     这类只认"真正的 IO 异常"(读线程置 need_reopen)。
            #   - ZQWL 串口: 有实测过的"假活"故障(句柄打开成功但再也收不到帧),
            #     所以保留原来的静默判据。
            if isinstance(bus, PyCanBus) or getattr(self.link, "need_reopen", False):
                if getattr(self.link, "need_reopen", False):
                    self.link.need_reopen = False
                    self.link.add_error("CAN 适配器 IO 异常, 重新打开")
                    self.link.detach()
                else:
                    return
            else:
                # USB heartbeat/config replies prove the ZQWL receiver is alive
                # even when the motor PSU is OFF and there are no CAN frames.
                transport_rx = getattr(bus, "last_transport_activity", 0.0)
                if transport_rx and now - transport_rx <= self.DEAD_AFTER_S and getattr(bus,"state",None) != "bus-off":
                    return
                last_rx = getattr(self.link, "last_rx", 0.0)
                # 错误帧也算"活动"
                last_act = max(last_rx, getattr(self.link, "last_activity", 0.0))
                ever_rx = (getattr(self.link, "rx_total", 0) > 0 or
                           getattr(self.link, "err_frames", 0) > 0)
                since = now - getattr(self, "_bus_since", now)
                dead = ""
                if ever_rx and last_act and (now - last_act) > self.DEAD_AFTER_S:
                    dead = "no frame for %.0fs" % (now - last_act)
                elif (not ever_rx) and since > self.DEAD_AFTER_S:
                    # ★ 打开后【一帧都没收到】也要算死。
                    #   实测: 串口句柄打开成功、设备也在发, 但就是收不到任何帧,
                    #   旧逻辑只在 bus is None 时重开 -> 永远卡死(网页一直红)。
                    dead = "no frame at all in %.0fs after opening" % since
                if not dead:
                    return
                self.link.add_error("CAN adapter not receiving (%s); reopening it" % dead)
                self.link.detach()
        if now < self._reopen_at:
            return
        self._reopen_at = now + self.REOPEN_INTERVAL
        bus, err = self.open_bus()
        if bus is None:
            self._last_open_err = err or "adapter not found"
            return
        if self.link.attach(bus):
            self.bus = bus
            self._bus_since = now
            self._last_open_err = ""
            self.link.add_log("adapter (re)opened on %s"
                              % getattr(bus, "channel", "?"))
        else:
            # ★attach 失败也要把刚开的句柄关掉, 否则 gs_usb 会一直占着 USB 接口,
            #   下一次打开就变成 Access denied (实测: 泄漏几次后设备彻底打不开)。
            try:
                bus.shutdown()
            except Exception:
                pass

    def start_poll(self):
        self._safety_thread = threading.Thread(target=self._safety_loop, name="foc-web-safety")
        self._safety_thread.daemon = True
        self._safety_thread.start()

        def loop():
            while not self._poll_stop:
                try:
                    if not self.ota_busy():
                        self._ensure_bus()      # ★ 适配器掉了就自动接回来
                        self.link.poll_job()
                except Exception as exc:
                    self.link.add_error("device poll failed: %s" % exc)
                waited = 0.0
                while waited < POLL_INTERVAL and not self._poll_stop:
                    time.sleep(0.2)
                    waited += 0.2
        self._poll_thread = threading.Thread(target=loop, name="foc-web-poll")
        self._poll_thread.daemon = True
        self._poll_thread.start()

    def stop(self):
        self._poll_stop = True
        if self.link is not None:
            self.link.shutdown()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def build_parser():
    ap = argparse.ArgumentParser(
        description="Local web dashboard for the N32G455REL7 FOC board "
                    "(status bar, live current curves, parameter channel over CAN).")
    ap.add_argument("--port", type=int, default=8888,
                    help="HTTP port on 127.0.0.1 (default: 8888)")
    ap.add_argument("--channel", default=None,
                    help="CAN adapter serial port, e.g. COM4 "
                         "(default: auto-detect the ZQWL adapter). "
                         "For --interface gs_usb this is the adapter index (0).")
    ap.add_argument("--interface", default=None,
                    help="python-can backend name: gs_usb (candleLight/CANable, "
                         "WinUSB, no COM port), slcan, pcan, vector, kvaser, ... "
                         "Omit to auto-detect: ZQWL serial first, then gs_usb.")
    ap.add_argument("--baud", type=int, default=500000,
                    help="CAN bitrate (default: 500000)")
    ap.add_argument("--node", type=int, default=0,
                    help="device node id, 0..127 (default: 0 -- must match the "
                         "firmware's OTA_CAN_NODE_DEFAULT; ota_host.py also uses 0)")
    ap.add_argument("--verbose", action="store_true",
                    help="also print every CAN frame and HTTP request")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.node < 0 or args.node > 127:
        sys.stderr.write("ERROR: --node must be 0..127\n")
        return 2

    # ★电机档案: 读持久化的选择 (motor_profile.txt), 读不到就用本工程默认
    #   (xige_foc -> 7010, FOC_5014 -> 5014)。页面下拉可随时切换。
    sel = load_motor_sel()
    sys.stdout.write("foc_web: motor profile = %s (%s), 可选: %s\n"
                     % (sel, motor_profile()["label"], ", ".join(MOTOR_PROFILES)))

    app = App(args)
    bus, err = app.open_bus()

    link = Link(bus, args.node, verbose=args.verbose)
    app.link = link
    if bus is None:
        link.ready = False
        link.reason = err or "CAN adapter is not open"
        link.add_error(link.reason)
    link.start()

    # On Windows SO_REUSEADDR lets a SECOND process bind the same port, and the
    # two servers then silently steal each other's connections.  Turn it off so
    # "port already in use" is reported instead of half-working.
    # SO_EXCLUSIVEADDRUSE additionally stops ANOTHER program that does set
    # SO_REUSEADDR from co-binding our port later (Windows only).
    class ExclusiveHTTPServer(ThreadingHTTPServer):
        # Windows requires exclusivity; Linux SO_REUSEADDR allows immediate
        # restart after TIME_WAIT and does not permit a second listening socket.
        allow_reuse_address = (os.name != "nt")

        def server_bind(self):
            excl = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if excl is not None:
                self.socket.setsockopt(socket.SOL_SOCKET, excl, 1)
            ThreadingHTTPServer.server_bind(self)

    try:
        server = ExclusiveHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    except OSError as exc:
        link.shutdown()
        sys.stderr.write("ERROR: cannot listen on 127.0.0.1:%d (%s).\n"
                         "  Port is taken - usually an older dashboard is still running.\n"
                         "  Use  启动网页.cmd -Kill  (ends every foc_web instance, then starts this one),\n"
                         "  or find the owner with  netstat -ano | findstr :%d\n"
                         % (args.port, ascii_text(exc), args.port))
        return 2
    server.daemon_threads = True
    server.app = app
    server.verbose = args.verbose
    server.request_queue_size = 16

    url = "http://127.0.0.1:%d/" % args.port
    sys.stdout.write("foc_web: dashboard on %s (Ctrl+C to stop)\n" % url)
    sys.stdout.write("foc_web: node %d, OTA id 0x%03X/0x%03X, param id 0x%03X/0x%03X\n"
                     % (args.node, link.ota_host_id, link.ota_dev_id,
                        link.param_host_id, link.param_dev_id))
    if bus is None:
        sys.stdout.write("foc_web: CAN adapter NOT open: %s\n" % link.reason)
    sys.stdout.flush()

    # ★ 轮询线程必须【无条件】启动。
    #   原来这里是 "if bus is not None: app.start_poll()", 后果是:
    #   启动时没插适配器 -> 轮询线程根本不启动 -> 即使后来适配器插上
    #   (auto-attach 成功、adapter.open=True、遥测也在流),
    #   version / foc_loops / motor 也永远是空的 —— 实测踩到过。
    #   现在无条件启动, 适配器不在时由循环里的 _ensure_bus() 负责自动重连。
    app.start_poll()

    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        sys.stdout.write("\nfoc_web: stopping\n")
    finally:
        app.stop()
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
