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
            "rs_mohm": None, "line_rs_mohm": None, "ls_uh": None,
            "kt_nm_a_rms": None, "ke_vrms_per_krpm_line": None,
            "rated_current_arms": None, "peak_current_arms": None, "peak_current_a": None,
            "rpm_rated": None, "nm_rated": None, "psi_mwb_nominal": None,
            "catalog_ls_uh": 235, "catalog_kt_nm_a": 0.0606,
            "reduction_ratio": 9.67, "ntc_r25_ohm": 10000, "ntc_beta": 3435,
        },
        "safe_cur_ma_default": 3000,
        "safe_cur_ma_max": 10000,
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
CONTROL_MODE_MAX_WEB = 6
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
    # 2026-10-14: 0.5..3 A 电流环锁定测试; ID 7 同时是相电流跳闸阈值 (0.1..5 A)。
    "current_a": (1, 1000, -10.0, 10.0),
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
    {'id': 7, 'name': 'i_limit', 'def': 3000, 'min': 100, 'max': 10000, 'unit': 'mA'},
    {'id': 8, 'name': 'pos_kp', 'def': 300, 'min': 0, 'max': 10000, 'unit': 'x100/s'},
    {'id': 9, 'name': 'pos_ki', 'def': 0, 'min': 0, 'max': 10000, 'unit': 'x100/s2'},
    {'id': 10, 'name': 'pos_kd', 'def': 0, 'min': 0, 'max': 1000, 'unit': 'x1000'},
    {'id': 11, 'name': 'speed_limit', 'def': 300, 'min': 1, 'max': 300, 'unit': 'rpm'},
    {'id': 12, 'name': 'voltage_limit', 'def': 8000, 'min': 100, 'max': 8000, 'unit': 'mV'},
    {'id': 13, 'name': 'deadtime_comp_ns', 'def': 400, 'min': 0, 'max': 1000, 'unit': 'ns;0=off'},
    {'id': 14, 'name': 'speed_ramp', 'def': 60, 'min': 1, 'max': 3000, 'unit': 'rpm/s'},
    {'id': 15, 'name': 'speed_filter', 'def': 20, 'min': 0, 'max': 1000, 'unit': 'ms'},
    {'id': 16, 'name': 'current_bw', 'def': 200, 'min': 0, 'max': 2000, 'unit': 'rad/s;0=manual'},
    {'id': 17, 'name': 'phase_rs', 'def': 275, 'min': 1, 'max': 10000, 'unit': 'mOhm equivalent'},
    {'id': 18, 'name': 'phase_ls', 'def': 600, 'min': 1, 'max': 10000, 'unit': 'uH; provisional'},
    {'id': 19, 'name': 'spd_kd', 'def': 0, 'min': 0, 'max': 10000, 'unit': 'x1000'},
    {'id': 20, 'name': 'voltage_ramp', 'def': 1000, 'min': 100, 'max': 100000, 'unit': 'mV/s'},
    {'id': 21, 'name': 'torque_limit', 'def': 2700, 'min': 1, 'max': 5000, 'unit': 'mNm'},
    {'id': 22, 'name': 'current_filter', 'def': 100, 'min': 0, 'max': 10000, 'unit': 'us'},
]
PARAM_BY_ID = dict((p["id"], p) for p in PARAM_TABLE)


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
        with self.state_lock:
            self.motor_status = {
                "available": True,
                "mode": data[1],
                "state": state,
                "state_name": MOTOR_STATE_NAMES.get(state, "unknown"),
                "step": data[3],
                "step_ms": le16(data, 4),
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
                    if data[0] == 0x91 and (len(data) < 2 or data[1] != req.data[2]):
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

    def _parse_param_reply(self, data):
        pid = data[1]
        value = le32(data, 2)
        flags = data[6] if len(data) > 6 else 0
        known = PARAM_BY_ID.get(pid)
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
        info = PARAM_BY_ID.get(pid)
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
        info = PARAM_BY_ID.get(pid)
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
        for desc in PARAM_TABLE:
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
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MotorTune Pro · SG-6010HB</title>
<!-- ★内联 favicon: 不写它浏览器会去要 /favicon.ico, 服务端没这个路由 ->
     每个页面都是唯一一条 404, 控制台一直挂着一个红点 (2026-09-27 清掉)。 -->
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%231677ff'/%3E%3Cpath d='M17.5 5 9 18h5.5L13 27l9-13.5h-5.5z' fill='%23fff'/%3E%3C/svg%3E">
<style>
:root { --bg:#0e1726; --panel:#1a0a0a; --line:#223650; --fg:#c5d1e3;
        --dim:#9ea8b8; --ok:#20b767; --bad:#e54855; --warn:#d99011;
        --acc:#1677ff; --nav:#0a131e; --nav-soft:#101e2c; }
* { box-sizing:border-box; }
html { scroll-behavior:smooth; }
body { margin:0; background:var(--bg); color:var(--fg);
       font:15px/1.5 "Microsoft YaHei UI","Microsoft YaHei",Arial,sans-serif; }
button, input, select { font:inherit; }
button { cursor:pointer; }
.app-shell { min-height:100vh; display:grid; grid-template-columns:134px minmax(0,1fr); }
#sidebar { position:sticky; top:0; height:100vh; display:flex; flex-direction:column;
           padding:14px 10px 12px; color:#d8e4f2; background:linear-gradient(180deg,#0c1622,#0a121c); }
.side-brand { height:46px; display:flex; align-items:center; justify-content:center; margin-bottom:16px; }
.brand-mark { width:31px; height:31px; display:grid; place-items:center; border-radius:10px;
              color:#ffffff; background:#1683ff; box-shadow:0 5px 14px #07182e55; }
.side-nav { display:grid; gap:5px; }
.side-nav a { min-height:46px; display:flex; align-items:center; gap:12px; padding:0 11px;
              color:#afc0d4; text-decoration:none; border-radius:8px; transition:.16s ease; }
.side-nav a:hover,.side-nav a.active { color:#ffffff; background:#16283a; }
.side-nav a.active { box-shadow:inset 3px 0 #5aa7ff; }
.side-nav svg { width:19px; height:19px; flex:none; }
.side-foot { margin-top:auto; padding:10px 9px 0; color:#91a7bf; font-size:13px; }
.workspace { min-width:0; }
#bar { min-height:52px; position:sticky; z-index:5; top:0; display:flex; align-items:center;
       justify-content:space-between; gap:16px; padding:8px 20px; color:#f4f8ff;
       background:linear-gradient(100deg,#0e1a28,#121d2c 64%,#0c1622);
       box-shadow:0 2px 9px #172a4020; }
.brand-lockup { display:flex; align-items:center; gap:12px; min-width:0; }
.brand-lockup .brand-mark { width:30px; height:30px; }
.brand-title { font-size:18px; font-weight:700; letter-spacing:.2px; white-space:nowrap; }
.brand-subtitle { margin-left:10px; color:#b9c9dc; font-size:14px; white-space:nowrap; }
.header-tools { display:flex; align-items:center; gap:12px; }
.header-device { display:flex; align-items:center; gap:8px; padding:6px 10px; color:#e9f2fc;
                 border:1px solid #3b1c1c20; border-radius:999px; background:#1a0a0a0d; }
.header-device:before { content:""; width:8px; height:8px; border-radius:50%; background:#25c46a;
                       box-shadow:0 0 0 3px #25c46a26; }
.header-device.disconnected:before { background:#e54855; box-shadow:0 0 0 3px #e5485526; }
.header-separator { height:22px; border-left:1px solid #3b1c1c35; }
#motor_label { color:#f4f8ff; font-size:15px; white-space:nowrap; }
#bar button { padding:7px 10px; color:#e6f0fd; background:#1a0a0a12; border:1px solid #3b1c1c2c;
              border-radius:7px; }
#bar button:hover { background:#1a0a0a20; }
#bar button:disabled { opacity:.5; cursor:default; }
.k { color:var(--dim); }
.ok { color:var(--ok); } .bad { color:var(--bad); } .warn { color:var(--warn); }
#v_errbox { display:block; order:1; margin:0 0 8px; padding:0 2px; color:var(--bad); font-size:14px; }
#v_errbox:empty { display:none; }
main { max-width:1900px; margin:0 auto; padding:14px 14px 22px; display:flex; flex-direction:column; }
.page-heading { display:flex; align-items:flex-end; justify-content:space-between; gap:12px;
                margin:0 0 11px; order:0; }
.page-heading { display:none; }
.page-heading h1 { margin:0; font-size:20px; font-weight:700; letter-spacing:.2px; }
.page-heading p { margin:3px 0 0; color:var(--dim); font-size:14px; }
.page-stamp { color:#94a1b2; font-size:13px; white-space:nowrap; }
.work-mode-switch { display:flex; flex-wrap:wrap; align-items:center; gap:7px; margin:0 0 10px;
  padding:9px 11px; order:1; background:#1a0a0a; border:1px solid var(--line); border-radius:9px;
  box-shadow:0 2px 7px #23395308; }
.work-mode-switch strong { margin-right:3px; color:#afbfd2; font-size:14px; }
.work-mode-switch button { min-height:32px; padding:6px 10px; color:#aab8c8; background:#0d1724;
  border:1px solid #263c54; border-radius:6px; font-weight:650; }
.work-mode-switch button.selected { color:#ffffff; background:#1677ff; border-color:#1677ff; }
.work-mode-switch button.stop-view { margin-left:auto; color:#e16572; background:#28070a;
  border-color:#6d1b23; }
.work-mode-note { color:#95a1b1; font-size:12px; }
.status-grid { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:9px; margin-bottom:10px; order:2; }
.status-card,.panel { min-width:0; background:var(--panel); border:1px solid var(--line);
                       border-radius:9px; box-shadow:0 2px 7px #23395308; }
.status-card { width:auto; min-width:0; min-height:94px;
               display:flex; align-items:center; gap:11px; padding:12px 13px; }
.status-icon { width:42px; height:42px; display:grid; place-items:center; flex:none; color:#4794ff;
               border-radius:50%; background:#081a30; }
.status-icon.green { color:#5ee8a1; background:#0e351f; }
.status-icon.red { color:#e85e6a; background:#2a070a; }
.status-icon svg { width:22px; height:22px; }
.status-copy { min-width:0; }
.status-label { display:block; margin-bottom:3px; color:#a4b0c0; font-size:13px; }
.status-value { display:block; overflow:hidden; color:#c7d3e4; font-size:17px; font-weight:700;
                text-overflow:ellipsis; white-space:nowrap; }
.status-note { display:block; overflow:hidden; margin-top:2px; color:#909eb0; font-size:12px;
               text-overflow:ellipsis; white-space:nowrap; }
.overview-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:9px;
                 margin-bottom:10px; align-items:start; order:3; }
.overview-grid > * { width:auto; min-width:0; }
.panel { padding:11px 12px; }
.resizable-module { position:relative; flex:0 1 auto; max-width:100%; min-width:150px; min-height:40px;
                    overflow:auto; resize:none; }
.resize-grip { position:absolute; z-index:4; display:block; touch-action:none; opacity:.35; }
.resize-grip:hover { background:#1677ff; opacity:.8; }
.resize-grip-n,.resize-grip-s { left:12px; right:12px; height:6px; cursor:ns-resize; }
.resize-grip-n { top:0; } .resize-grip-s { bottom:0; }
.resize-grip-e,.resize-grip-w { top:12px; bottom:12px; width:6px; cursor:ew-resize; }
.resize-grip-e { right:0; } .resize-grip-w { left:0; }
.resize-grip-ne,.resize-grip-nw,.resize-grip-se,.resize-grip-sw { width:11px; height:11px; }
.resize-grip-ne { top:0; right:0; cursor:nesw-resize; }
.resize-grip-nw { top:0; left:0; cursor:nwse-resize; }
.resize-grip-se { right:0; bottom:0; cursor:nwse-resize; }
.resize-grip-sw { bottom:0; left:0; cursor:nesw-resize; }
.dashboard-resizing,.dashboard-resizing * { user-select:none !important; }
.panel-heading { display:flex; align-items:center; justify-content:space-between; gap:8px;
                 min-height:24px; margin:0 0 8px; padding-bottom:7px; border-bottom:1px solid #223347; }
.panel-heading h2 { display:flex; align-items:center; gap:8px; margin:0; font-size:15px; font-weight:700; }
.panel-heading h2:before { content:""; width:3px; height:15px; border-radius:4px; background:#2788ff; }
.panel-heading small { color:#909eb0; font-size:13px; }
.connection-rows { display:grid; gap:8px; }
.connection-row { display:flex; justify-content:space-between; gap:10px; color:#a8b4c4; font-size:14px; }
.connection-row strong { color:#bdccdf; font-weight:600; text-align:right; }
#motor_info { display:block; margin-top:8px; padding:7px 8px; color:#9ca7b8; background:#0b1624;
              border-radius:6px; font-size:12px !important; line-height:1.5; overflow-wrap:anywhere; }
.connection-actions { margin-top:8px; }
.connection-actions button { width:100%; padding:7px 10px; color:#ffffff; background:#1677ff;
                             border:1px solid #1677ff; border-radius:6px; font-weight:600; }
.connection-actions button:hover { background:#0768e8; }
#profile_note { display:block; margin-top:7px; padding:7px 8px; color:#deb868; background:#312609;
                border-radius:6px; font-size:12px; line-height:1.5; }
.live-values { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:7px; }
.live-value { padding:10px 8px; text-align:center; border-radius:7px; background:#0b1624; }
.live-value span { display:block; color:#9ea8b8; font-size:13px; }
.live-value strong { display:block; margin-top:4px; color:#c1cfe1; font-size:17px; font-variant-numeric:tabular-nums; }
.live-value small { color:#919fb0; font-size:12px; }
.live-footnote { margin:8px 0 0; color:#919fb0; font-size:12px; }
.motor-status-body { display:grid; grid-template-columns:92px 1fr; align-items:center; gap:8px; }
.motor-illustration { width:92px; height:85px; }
.motor-readout { display:grid; gap:7px; }
.motor-readout div { display:flex; justify-content:space-between; gap:8px; color:#9ea8b8; font-size:13px; }
.motor-readout strong { color:#becde0; font-size:14px; font-variant-numeric:tabular-nums; }
.state-chip { display:inline-flex; align-items:center; gap:6px; padding:4px 8px; border-radius:6px;
              color:#a5b0bf; background:#0f1a28; font-size:13px; }
.state-chip:before { content:""; width:7px; height:7px; border-radius:50%; background:#303b48; }
.state-chip.running { color:#60e6a3; background:#0e311d; }
.state-chip.running:before { background:#20b767; }
.state-chip.bad { color:#db6c74; background:#2a070a; }
.state-chip.bad:before { background:#e54855; }
/* RPM controls remain explicit operator actions; page load never enables output. */
#rpmbar { display:grid; grid-template-columns:minmax(0,2fr) minmax(240px,1fr); align-items:start;
          gap:10px; margin:0 0 10px; padding:11px 12px; order:4;
          background:#1a0a0a; border:1px solid var(--line); border-radius:9px; box-shadow:0 2px 7px #23395308; }
#rpmbar > .ota-panel,#rpmbar > #rpm_hint { grid-column:1 / -1; }
#rpmbar > .loop-reserve { grid-column:1; }
#rpmbar > .ota-panel { grid-column:2; margin:0; }
#rpmbar .panel-heading { width:100%; }
.tune-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:6px; margin:8px 0; }
.tune-grid label { display:flex; flex-direction:column; gap:3px; font-size:13px; color:#a8b7c8; }
.tune-grid input { width:100% !important; text-align:left !important; }
[data-tune-modes][hidden] { display:none !important; }
.control-feedback { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:7px; padding:8px; background:#0b1624; margin:8px 0; }
.control-feedback strong { display:block; font-variant-numeric:tabular-nums; }
.tuning-panel { border-top:1px solid #273950; margin-top:8px; padding-top:8px; }
@media(max-width:1100px) { #rpmbar { grid-template-columns:1fr; } #rpmbar > .loop-reserve,#rpmbar > .ota-panel { grid-column:1 / -1; } }
#rpmbar b { font-size:15px; font-weight:700; }
.rpm-control-row { display:flex; flex-wrap:wrap; align-items:center; gap:7px; }
.control-specs { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; }
.control-specs div { display:flex; flex-direction:column; gap:3px; padding:7px 8px; color:#9ba8b7;
                     background:#0b1624; border-radius:6px; font-size:12px; }
.control-specs strong { color:#b4c5d9; font-size:13px; }
.loop-reserve { padding:0 8px; }
.loop-reserve summary { padding:7px 0; }
.loop-mode-grid,.loop-field-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(112px,1fr)); gap:6px; }
.loop-mode { display:flex; flex-direction:column; gap:2px; min-width:0; padding:7px 8px;
             color:#a8b7c8; background:#0d1724; border:1px solid #273950; border-radius:6px; }
.loop-mode.selected { color:#ffffff; background:#1677ff; border-color:#1677ff; }
.loop-mode small { color:#d1c09f; font-size:12px; }
.loop-mode.selected small { color:#dceaff; }
.loop-field { display:grid; gap:3px; min-width:0; color:#9ba8b7; font-size:12px; }
.loop-field input { width:100%; min-width:0; padding:5px 6px; color:#9ea7b7; background:#0e1624;
                    border:1px solid #273951; border-radius:5px; }
.loop-field[hidden] { display:none; }
.loop-reserve-note { display:block; margin-top:7px; color:#92a0b1; font-size:12px; }
/* 位置模式"当前位置 → 目标"实时读数: 是数据不是说明文字; 差值过大时标红 */
.loop-readout-strong { display:block; margin-top:6px; color:#71a2d6; font-size:14px;
                       font-variant-numeric:tabular-nums; }
.loop-readout-strong.bad { color:#e16572; font-weight:700; }
  border:1px solid #1677ff; border-radius:6px; font-weight:650; }
  border:1px solid #7a5d19; border-radius:6px; font-size:12px; line-height:1.5; }
.control-actions,.calib-row,.ota-row { display:flex; flex-wrap:wrap; align-items:center; gap:7px; margin-top:8px; }
/* ★ 四按钮直接控制 (2026-09-28): 配置动作(读取/应用)与运行动作(启动/停止)左右分组;
   启动是唯一强调色按钮, 停止是唯一的危险色按钮; 反馈行默认空白, 不留占位说明。 */
.control-actions .action-spacer { flex:1 1 12px; min-width:12px; }
.control-actions #btn_loop_run,.control-actions #btn_loop_stop { min-width:92px; }
#loop_hint:empty { display:none; }
.control-readout { display:flex; flex-wrap:wrap; gap:12px; margin-top:8px; color:#9ba8b7; font-size:13px; }
.control-readout strong { color:#b4c5d9; }
.calib-progress { width:min(260px,100%); height:8px; accent-color:#1677ff; }
.ota-panel { padding:8px; border-top:1px solid #24344b; }
.ota-panel pre { max-height:150px; overflow:auto; margin:7px 0 0; padding:8px; color:#dce8f8;
                 background:#0a111c; border-radius:6px; font:12px/1.45 Consolas,monospace; white-space:pre-wrap; }
#rpmbar input[type=number] { width:78px; min-height:34px; padding:6px 8px; font-size:16px; text-align:center; }
#rpmbar input[type=number], input[type=number] { color:#bdccdf; background:#1a0a0a; border:1px solid #2c415c;
       border-radius:6px; }
#rpmbar button { min-height:34px; padding:6px 11px; color:#b7c7da; background:#0d1622;
                 border:1px solid #283b53; border-radius:6px; font-weight:600; }
#rpmbar button:hover { filter:brightness(.98); }
#rpmbar button:disabled { opacity:.52; cursor:default; }
#rpmbar #btn_rpm_run { color:#ffffff; background:#1677ff; border-color:#1677ff; }
#rpmbar #btn_rpm_run.on { background:#168a55; border-color:#168a55; }
#rpmbar #btn_rpm_stop { color:#afbecf; }
#rpmbar #btn_rpm_recon { color:#71a2d6; }
#rpmbar #rpm_hint { display:block; min-height:30px; padding:7px 8px; color:#9ea8b8;
                    background:#0d1622; border-radius:5px; font-size:12px; }
button, select { color:#b7c7da; }
select { padding:5px 8px; background:#1a0a0a; border:1px solid #2a3f58; border-radius:6px; }
input[type=number] { width:82px; padding:5px 7px; font:inherit; }
input[type=number]:focus,select:focus { outline:2px solid #1677ff35; border-color:#0050bb; }

details { min-width:0; background:#1a0a0a; border:1px solid var(--line); border-radius:8px; }
summary { padding:8px 11px; color:#aab7c8; cursor:pointer; font-weight:650; font-size:14px; }
.dwrap { padding:2px 11px 12px; color:#b0bfd0; }
.bar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:8px; }
.bar button:disabled { opacity:.45; cursor:default; }
.chart-panel { width:100%; min-width:320px; min-height:275px; padding:7px 8px 7px;
               display:grid; grid-template-rows:auto minmax(190px,1fr) auto; }
#chart-panel { order:4; }
#tracebar { display:flex; flex-wrap:wrap; gap:5px 9px; align-items:center; margin:0 0 5px;
            padding:5px 7px; box-shadow:none; font-size:12px; }
#tracebar label { display:inline-flex; align-items:center; gap:4px; color:#abb8c9; }
#tracebar input { accent-color:#1677ff; }
#curves { width:100%; height:100%; min-height:190px; display:block; background:#070b10; border:1px solid #1e2c3c;
          border-radius:6px; }
#ctl { display:flex; flex-wrap:wrap; gap:7px; align-items:center; margin:5px 0 0; padding:5px 7px;
       box-shadow:none; font-size:13px; }
#ctl .k { color:#9ea8b8; }
#ctl button.on { color:#5ee9a5; background:#0e311d; border-color:#1f6a3c; }
#tstate { color:#9ea8b8; }
.summary-grid { display:flex; flex-wrap:wrap; align-items:stretch; gap:9px; margin-top:10px; order:5; }
.summary-grid > :nth-child(1) { width:calc(43% - 6.75px); }
.summary-grid > :nth-child(2) { width:calc(17.5% - 6.75px); }
.summary-grid > :nth-child(3) { width:calc(17.5% - 6.75px); }
.summary-grid > :nth-child(4) { width:calc(22% - 6.75px); }
.summary-grid .panel { padding:10px 11px; }
.summary-grid .panel-heading { margin-bottom:6px; }
.event-table th,.event-table td { padding:5px 6px; font-size:12px; }
.event-time { color:#9ba8b8; font-variant-numeric:tabular-nums; white-space:nowrap; }
.event-level { color:#4794ff; white-space:nowrap; }
.event-level.warn { color:#f2b855; }
.event-level.bad { color:#e85e6a; }
.sys-list { display:grid; gap:7px; }
.sys-row { display:flex; justify-content:space-between; gap:10px; color:#9ea8b8; font-size:13px; }
.sys-row strong { color:#b8c8dd; font-weight:600; text-align:right; }
.phase-body { display:grid; grid-template-columns:116px 1fr; align-items:center; gap:8px; }
.phase-wheel { width:116px; height:116px; }
.phase-list { display:grid; gap:9px; }
.phase-list div { display:flex; align-items:center; justify-content:space-between; gap:6px;
                  color:#a3afbe; font-size:13px; }
.phase-label { display:flex; align-items:center; gap:7px; }
.phase-dot { width:8px; height:8px; border-radius:50%; }
.phase-list strong { color:#b8c8dd; font-size:14px; font-variant-numeric:tabular-nums; }
.encoder-metrics { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:7px; margin-bottom:7px; }
.encoder-metrics .live-value { padding:8px 6px; }
.encoder-metrics .live-value strong { font-size:16px; }
.encoder-readout { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
.encoder-readout .encoder-metrics { grid-column:1/-1; }
.encoder-angle { display:flex; flex-direction:column; justify-content:center; min-width:0; padding:10px 11px;
  background:#071629; border:1px solid #0d3462; border-radius:7px; }
.encoder-angle span,.encoder-angle small { color:#9ea8b8; font-size:12px; }
.encoder-angle strong { margin-top:3px; color:#c1cfe1; font-size:24px; line-height:1.1;
  font-variant-numeric:tabular-nums; white-space:nowrap; }
.encoder-metrics { margin:0; }
.encoder-metrics .live-value { display:flex; flex-direction:column; justify-content:center; min-width:0; }
.encoder-meta { margin-top:7px; color:#9ea8b8; font-size:12px; }
.encoder-meta strong { color:#b4c5d9; font-weight:650; }
.encoder-state-valid { color:#60e6a3 !important; }
.encoder-state-bad { color:#db6c74 !important; }
.encoder-note { display:block; margin-top:6px; color:#919fb0; font-size:12px; line-height:1.45; }
.details-grid { display:flex; flex-wrap:wrap; align-items:stretch; gap:10px; margin-top:10px; order:8; }
.details-grid > details { width:calc(50% - 5px); }
#adv_motor,#params_box { overflow:auto; }
#adv_motor .dwrap,#params_box .dwrap { padding:4px 11px 12px; }
table { width:100%; border-collapse:collapse; }
th,td { text-align:left; padding:6px 7px; border-bottom:1px solid #22334b; }
th { color:#9ea8b8; background:#0c1521; font-weight:600; font-size:12px; text-transform:uppercase; }
td { color:#b5c5d9; }
#v_errbox:empty { display:none; }
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden;
           clip:rect(0,0,0,0); white-space:nowrap; border:0; }
@media (max-width:1280px) {
  .app-shell { grid-template-columns:112px minmax(0,1fr); }
  .overview-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .status-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .summary-grid > :nth-child(1) { width:calc(58% - 4.5px); }
  .summary-grid > :nth-child(2) { width:calc(42% - 4.5px); }
  .summary-grid > :nth-child(n+3) { width:calc((100% - 9px)/2); }
}
@media (max-width:860px) {
  .app-shell { display:block; }
  #sidebar { position:static; width:100%; height:auto; padding:7px 10px; }
  .side-brand,.side-foot { display:none; }
  .side-nav { grid-template-columns:repeat(6,minmax(0,1fr)); gap:4px; }
  .side-nav a { justify-content:center; gap:5px; min-height:38px; padding:3px; font-size:12px; }
  .side-nav a.active { box-shadow:inset 0 -2px #5aa7ff; }
  #bar { position:static; padding:8px 12px; }
  .brand-title { font-size:16px; }
  .brand-subtitle { display:none; }
  main { padding:10px; }
  .status-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .overview-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  #rpmbar { grid-template-columns:minmax(0,1fr); }
  #rpmbar > * { grid-column:1 / -1; }
  .details-grid > details { width:100%; }
  .summary-grid > :first-child { width:100%; }
  .summary-grid > :nth-child(n+2) { width:calc((100% - 18px)/3); }
}
@media (max-width:560px) {
  .side-nav { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .side-nav a { justify-content:flex-start; padding-left:10px; }
  #bar { align-items:flex-start; }
  .header-tools { gap:5px; }
  .header-device { display:none; }
  .resizable-module { min-width:0; }
  .status-grid,.overview-grid { grid-template-columns:minmax(0,1fr); }
  .page-heading { align-items:flex-start; }
  .page-stamp { display:none; }
  .motor-status-body { grid-template-columns:72px 1fr; }
  .motor-illustration { width:72px; }
  .encoder-readout { grid-template-columns:minmax(0,1fr); }
  #chart-panel { min-width:0; }
  #curves { min-height:180px; }
}

#rpmbar { grid-template-columns:minmax(0,1fr); }
#rpmbar > .loop-reserve,#rpmbar > .ota-panel { grid-column:1 / -1; }
.calibration-panel { background:#1a0a0a; border:1px solid var(--line); border-radius:9px; padding:10px 12px; margin:0 0 10px; order:3; }
.calibration-panel .calib-row { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-top:7px; }
.calibration-panel button { min-height:34px; padding:6px 12px; border:1px solid #2c415c; border-radius:6px; color:#b0c4d9; background:#0b1624; }
.calibration-panel button:disabled { opacity:.5; }
.calibration-panel #btn_calibrate { color:#ffffff; background:#1677ff; border-color:#1677ff; }
.mode-picker { display:flex; align-items:center; flex-wrap:wrap; gap:8px; margin-bottom:10px; }
.mode-picker select { min-height:36px; min-width:230px; padding:6px 10px; color:#bdccdf; background:#1a0a0a; border:1px solid #2f4968; border-radius:6px; font-size:16px; }
.mode-picker span { font-size:14px; color:#9ea7b7; }
.loop-field-grid { grid-template-columns:repeat(4,minmax(0,1fr)); }
.loop-field input { width:100% !important; text-align:left !important; }
.tune-grid { grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px 12px; }
#mode_tuning[hidden],#mode_feedback[hidden],#btn_capture_position[hidden],#tune_advanced[hidden] { display:none !important; }
#tune_advanced summary { cursor:pointer; font-size:14px; color:#a9bccf; padding:6px 0; }
#btn_loop_run { color:#ffffff; background:#136f8a; border-color:#136f8a; }
#btn_loop_stop { color:#e16572 !important; background:#28070a !important; border-color:#6d1b23 !important; }
.ota-panel { border-top:1px solid #223650; padding-top:10px; }
.ota-log summary { cursor:pointer; color:#9ca7b7; font-size:14px; margin-top:5px; }
@media(max-width:900px){.tune-grid,.loop-field-grid{grid-template-columns:repeat(2,minmax(0,1fr));}}
/* ======================================================================
 * ★运行模式选择器 (7 模式: 0 正弦开环 / 1 力矩 / 2 速度 / 3 位置 /
 *   4 电压 / 5 电流 / 6 MIT) 全部并列成 7 个 chip, 外加一个【独立的校准卡片】。
 *   规则:
 *     ① 校准是一张独立卡片, 放在模式卡【上面】, 与运行模式互不绑定 ——
 *        选模式不会启动校准, 校准也不会启动模式; 观察视图下它照样可见。
 *     ② 选哪个模式就只显示哪个模式的目标字段, 其余模式的目标一律不渲染。
 *     ③ 调参面板只显示"本模式真正会用到的"参数, 电流环 PI/电机 R·L/斜坡/滤波
 *        这些通用项收进折叠的「高级」区, 不再一次性铺开。
 * ====================================================================== */
#rpmbar > #calibration_panel { grid-column:1 / -1; }
#calibration_panel { width:100%; margin:0 0 12px; order:0; border-left:3px solid #2788ff; }
#calibration_panel .calib-head { margin-top:2px; }
#calibration_panel .calib-row { margin-top:7px; }
.calib-head { display:flex; flex-wrap:wrap; align-items:center; gap:7px; }
.calib-badge { display:inline-flex; align-items:center; gap:5px; padding:3px 8px; border-radius:999px;
  color:#aab8c8; background:#0f1b2a; font-size:13px; font-weight:650; }
.calib-badge:before { content:""; width:7px; height:7px; border-radius:50%; background:#303b48; }
.calib-badge.done { color:#60e6a3; background:#0e311d; }
.calib-badge.done:before { background:#20b767; }
.calib-badge.busy { color:#63a9e4; background:#081a30; }
.calib-badge.busy:before { background:#2788ff; }
.calib-badge.failed { color:#db6c74; background:#2a070a; }
.calib-badge.failed:before { background:#e54855; }
.calib-note { margin:7px 0 0; color:#909eb0; font-size:12px; line-height:1.5; }

.mode-chips { display:grid; grid-template-columns:repeat(auto-fit,minmax(124px,1fr)); gap:7px; margin:0 0 10px; }
.mode-chip { display:flex; flex-direction:column; align-items:flex-start; gap:2px; min-width:0;
  padding:8px 10px; text-align:left; color:#a8b7c8; background:#0d1724;
  border:1px solid #253b54; border-radius:7px; transition:.15s ease; }
.mode-chip:hover:not(:disabled) { background:#08182f; border-color:#0f3f7c; }
.mode-chip b { color:#b8c8dd; font-size:14px; font-weight:700; }
.mode-chip small { color:#8d9cb0; font-size:12px; }
.mode-chip.selected { color:#eaf3ff; background:#1677ff; border-color:#1677ff; }
.mode-chip.selected b { color:#ffffff; }
.mode-chip.selected small { color:#cfe4ff; }
.mode-chip:disabled { opacity:.45; cursor:default; }
/* #rpmbar button 的规则比 .mode-chip 更具体, 必须在这里显式压回来,
   否则"已选中的那个模式卡片"会被刷成浅底白字(实测截图确认过)。 */
#rpmbar .mode-chip { color:#a8b7c8; background:#0d1724; border:1px solid #253b54; min-height:0;
  font-weight:600; }
#rpmbar .mode-chip b { color:#b8c8dd; }
#rpmbar .mode-chip small { color:#8d9cb0; }
#rpmbar .mode-chip:hover:not(:disabled) { background:#08182f; border-color:#0f3f7c; }
#rpmbar .mode-chip.selected,
#rpmbar .mode-chip.selected:hover:not(:disabled) { color:#ffffff; background:#1677ff; border-color:#1677ff;
  box-shadow:0 2px 7px #1677ff40; }
#rpmbar .mode-chip.selected b { color:#ffffff; }
#rpmbar .mode-chip.selected small { color:#d5e8ff; }
#rpmbar .mode-chip:disabled { opacity:.45; }
#rpmbar > .panel-heading { margin-bottom:11px; }
#rpmbar .mode-picker { margin:0 0 7px; }
#rpmbar .mode-picker b { color:#b7c7da; }
#rpmbar .target-fields { margin-bottom:9px; }

.target-fields { display:flex; flex-wrap:wrap; align-items:flex-end; gap:8px 12px;
  padding:9px 10px; margin:0 0 8px; background:#0a1322; border:1px solid #1d3451; border-radius:7px; }
.target-group[hidden] { display:none !important; }
.target-group { display:flex; flex-wrap:wrap; align-items:flex-end; gap:8px 12px; }
.target-group .loop-field { min-width:108px; }
.target-group .loop-field input { width:108px; }
/* Only fill the existing space beside the open-loop RPM input. */
#rpmbar .target-group[data-target-mode="0"] { flex:1; min-width:0; flex-wrap:nowrap; }
#rpmbar .target-group[data-target-mode="0"] > .loop-field { flex:0 0 auto; }
#in_rpm_slider { appearance:none; -webkit-appearance:none; flex:1; min-width:0;
  height:42px; margin:0 10px 0 6px; padding:0; border:0; background:transparent;
  align-self:flex-end; cursor:pointer; touch-action:pan-y; }
#in_rpm_slider::-webkit-slider-runnable-track {
  height:20px; border-radius:999px; border:1px solid #99cfff55;
  background:radial-gradient(circle,#e5f3ff 0 1px,transparent 1.8px) 7px 3px/47px 19px,
    radial-gradient(circle,#cee7ffa8 0 .7px,transparent 1.5px) 22px 11px/73px 23px,
    linear-gradient(100deg,#428ece,#527cc9 40%,#6a67bb 75%,#5559b3);
  box-shadow:inset 0 1px 5px #c2e5ff26,0 0 12px #4b9ce51a;
  animation:rpm-stars 18s linear infinite; }
#in_rpm_slider::-moz-range-track {
  height:20px; border-radius:999px; border:1px solid #99cfff55;
  background:radial-gradient(circle,#e5f3ff 0 1px,transparent 1.8px) 7px 3px/47px 19px,
    radial-gradient(circle,#cee7ffa8 0 .7px,transparent 1.5px) 22px 11px/73px 23px,
    linear-gradient(100deg,#428ece,#527cc9 40%,#6a67bb 75%,#5559b3);
  box-shadow:inset 0 1px 5px #c2e5ff26,0 0 12px #4b9ce51a;
  animation:rpm-stars 18s linear infinite; }
#in_rpm_slider::-webkit-slider-thumb { appearance:none; -webkit-appearance:none;
  width:28px; height:28px; margin-top:-5px; border:1px solid #c6e7ff;
  border-radius:50%; background:#f6fbff; box-shadow:0 0 10px #4ac8ff66; }
#in_rpm_slider::-moz-range-thumb { width:26px; height:26px; border:1px solid #c6e7ff;
  border-radius:50%; background:#f6fbff; box-shadow:0 0 10px #4ac8ff66; }
#in_rpm_slider:focus-visible { outline:2px solid #69c9ff; outline-offset:3px; border-radius:999px; }
@keyframes rpm-stars {
  from { background-position:7px 3px,22px 11px,0 0; }
  to { background-position:54px 3px,95px 11px,0 0; }
}
@media(prefers-reduced-motion:reduce) {
  #in_rpm_slider::-webkit-slider-runnable-track { animation:none; }
  #in_rpm_slider::-moz-range-track { animation:none; }
}
.calib-gate { display:block; margin:0 0 8px; padding:7px 9px; color:#deb868; background:#312609;
  border:1px solid #7a5d19; border-radius:6px; font-size:12px; line-height:1.5; }


/* Compact encoder FOC workspace; secondary information stays below controls. */
main { gap:6px; }
.page-heading { margin-bottom:4px; padding:0; }
.page-heading h1 { font-size:18px; }
.page-heading p { display:none; }
.status-grid { gap:6px; margin-bottom:6px; }
.status-card { min-height:66px; padding:8px; gap:7px; }
.status-icon { width:30px; height:30px; }
.status-value { font-size:16px; }
.overview-grid { gap:6px; margin-bottom:6px; }
.panel { padding:8px; }
#rpmbar { gap:6px; padding:8px; margin-bottom:6px; }
.calibration-panel { margin-bottom:6px; padding:7px 9px; }
.mode-chip { padding:6px 8px; }
.mode-chip small { font-size:12px; }
#chart-panel { min-height:330px; grid-template-rows:auto minmax(200px,1fr) auto; align-self:stretch; }
#curves { min-height:200px; }
.workflow-help { order:1; margin:0 0 4px; }
.workflow-help summary { padding:6px 9px; font-size:13px; }
.workflow-help p { margin:6px 0; font-size:14px; line-height:1.6; }
.workflow-help a { color:#4794ff; }
.summary-grid { margin-top:5px; gap:6px; }
#btn_rpm_stop { color:#e16574 !important; background:#2a070c !important; }


@media(min-width:1100px){
 main { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); align-items:start; gap:6px; }
 .page-heading { display:none; }
 #workflow_help { grid-column:1/-1; grid-row:1; }
 #v_errbox { grid-column:1/-1; grid-row:2; margin:0; }
 .status-grid { grid-column:1/-1; grid-row:3; margin:0; }
 .overview-grid { grid-column:1/-1; grid-row:4; margin:0; }
 #rpmbar { grid-column:1; grid-row:5; margin:0; }
 #chart-panel { grid-column:2; grid-row:5; margin:0; min-width:0; align-self:stretch; grid-template-rows:auto minmax(200px,1fr) auto; }
 .summary-grid { grid-column:1/-1; grid-row:6; }
 #device-details { grid-column:1/-1; grid-row:7; }
 .status-card { min-height:58px; }
 .encoder-note,.live-footnote { font-size:12px; margin:4px 0; }
 .encoder-angle { padding:6px; }
 .encoder-angle strong { font-size:20px; }
 .encoder-meta { margin-top:3px; }
 .connection-row { margin:3px 0; }
 .live-value { padding:6px; }
 .profile-reference summary { padding:5px; font-size:12px; }
 .loop-reserve-note,.calib-gate { font-size:12px; }
 .mode-chips { gap:4px; }
 .mode-chip { padding:4px; min-width:0; }
 .mode-chip small { display:none; }
 .target-group .loop-field { min-width:95px; }
 #rpm_hint { display:none !important; }
}


/* 7010 lab instrument: restrained steel surface, explicit phase colors. */
:root { --lab-paper:#11222c; --lab-ink:#c8d7e3; --lab-rule:#314d65; --lab-action:#136f8a; --lab-fault:#b52f40; --lab-wave:#101d27; }
body { background:var(--lab-paper); font-family:"Microsoft YaHei UI","Microsoft YaHei",sans-serif; color:var(--lab-ink); }
#sidebar { background:#121c23; width:94px; }
.app-shell { grid-template-columns:94px minmax(0,1fr); }
#bar { background:#121c23; min-height:54px; padding:9px 16px; }
.brand-title { font-size:19px; font-weight:700; letter-spacing:.02em; }
.brand-subtitle { font-size:13px; color:#c7d6df; }
.brand-mark { background:#136f8a; border-radius:5px; }
.panel,.status-card,#rpmbar,.calibration-panel { box-shadow:none; border-radius:5px; border-color:var(--lab-rule); }
.status-grid { border-bottom:1px solid var(--lab-rule); padding-bottom:5px; }
.status-card { background:transparent; border:0; border-right:1px solid var(--lab-rule); border-radius:0; min-height:52px; }
.status-card:last-child { border-right:0; }
.status-icon { display:none; }
.status-label,.status-note { color:#abbcc7; }
.status-value { font-size:16px; }
.panel-heading { border-bottom:1px solid #2b475a; padding-bottom:5px; margin-bottom:7px; }
.panel-heading:before { background:#136f8a; width:2px; }
.panel-heading h2 { font-size:15px; color:#c4d7e3; }
.panel-heading small { color:#a5b4c0; }
.motor-status-body,.phase-body { grid-template-columns:1fr; }
.motor-readout { gap:7px; }
.live-value { background:#10212a; border-radius:3px; }
.live-value strong,.encoder-angle strong,.control-feedback strong { font-family:Consolas,"Microsoft YaHei",monospace; font-variant-numeric:tabular-nums; }
.live-values > :nth-child(1) strong { color:#d1757f; }
.live-values > :nth-child(2) strong { color:#5ba3eb; }
.live-values > :nth-child(3) strong { color:#63e3bb; }
#bar #btn_header_stop { min-height:34px; padding:6px 16px; background:#b52f40; color:#ffffff; border:1px solid #a42736; border-radius:4px; font-weight:700; }
#bar #btn_header_stop:disabled { opacity:.65; }
#bar #btn_header_stop:hover { background:#962638; }
#rpmbar #btn_loop_run,#calibration_panel #btn_calibrate,.connection-actions button { background:#136f8a; border-color:#136f8a; }
#rpmbar #btn_loop_stop { color:#d96d7b; background:#28070c; border-color:#73323c; }
#rpmbar .mode-chip { border-radius:3px; background:#0f2028; box-shadow:none; }
#rpmbar .mode-chip.selected,#rpmbar .mode-chip.selected:hover:not(:disabled) { background:#136f8a; border-color:#136f8a; box-shadow:none; }
#rpmbar .mode-chip.selected b { color:#ffffff; }
.target-fields { padding:8px; border-color:#2e4a5f; border-radius:4px; background:#0f1d26; }
.calibration-panel { border-left:3px solid #136f8a; }
.calib-note { font-size:13px; color:#adbeca; line-height:1.5; margin:6px 0 0; }
.calib-note details { display:inline-block; border:0; background:transparent; }
.calib-note summary { padding:0 5px; color:#5ec9e9; font-size:13px; }
.calib-note p { padding:5px; margin:2px; }
.workflow-help { border:0; border-left:2px solid #4a789a; border-radius:0; background:#142835; }
/* ★ 2026-09-28 用户要求"右边空白的地方让波形填充":
   原来这张卡片的画布被写死 330px(≥1100px 时 355px), 卡片下方留一大片空白。
   现在卡片 align-self:stretch 跟上左列同一行的高度, 画布 flex:1 1 auto 吃掉剩余高度。
   画布的 backing store 由 resizeCanvas() + ResizeObserver 重算, 所以拉伸不会变形。 */
#chart-panel { display:flex; flex-direction:column; gap:4px; align-self:stretch; }
#chart-panel .panel-heading { margin:0; }
#chart-panel #tracebar,#chart-panel .wave-note,#chart-panel #ctl { flex:0 0 auto; }
#chart-panel #curves { flex:1 1 auto; height:auto; min-height:200px; border-radius:3px; }
.wave-note { color:#a8b8c3; font-size:12px; line-height:1.5; }
#tracebar { margin:0 !important; padding:2px 0; font-size:13px !important; gap:4px 7px !important; }
#tracebar button,#ctl button { border:1px solid #35586e; background:#11222c; border-radius:3px; color:#bcd1df; padding:4px 6px; font-size:13px; }
#ctl { padding:2px 0; gap:5px; }
#tstate { font-size:12px; color:#acbdca; line-height:1.4; }
button:focus-visible,a:focus-visible,summary:focus-visible,input:focus-visible,select:focus-visible { outline:2px solid #087eaa; outline-offset:3px; }
button { cursor:pointer; }
button:disabled { cursor:default; }
#calib_gate[hidden],#mode_feedback[hidden],#mode_tuning[hidden],#btn_capture_position[hidden] { display:none !important; }
@media(min-width:1100px){
 #rpmbar { padding:8px; }
 #chart-panel #curves { min-height:240px; }
 .overview-grid { grid-template-columns:repeat(4,minmax(0,1fr)); }
 .overview-grid .panel { padding:7px; }
 .connection-actions { margin-top:5px; }
 .connection-actions button { padding:5px; font-size:13px; }
 .connection-row { font-size:13px; }
 .calibration-panel .panel-heading small { max-width:50%; font-size:12px; }
}
@media(max-width:700px){
 #sidebar { width:58px; }
 .app-shell { grid-template-columns:58px minmax(0,1fr); }
 #bar { padding:7px; gap:6px; flex-wrap:wrap; }
 .brand-title { font-size:16px; }
 .brand-subtitle { display:none; }
 .header-tools { flex-wrap:wrap; }
 #bar #btn_header_stop { padding:5px 10px; min-height:30px; }
 #chart-panel #curves { min-height:260px; height:auto; }
 .panel-heading small { font-size:12px; }
}
@media(prefers-reduced-motion:reduce){ html { scroll-behavior:auto; } *,*:before,*:after { animation:none !important; transition:none !important; } }


.side-nav a { padding:8px 6px; gap:6px; }
.side-nav a span { white-space:nowrap; }
#bar #btn_header_stop { background:#b52f40; color:white; }
#rpmbar #btn_loop_run { background:#136f8a; border-color:#136f8a; color:#ffffff; }

@media(max-width:860px){
 #sidebar { display:none; }
 .app-shell { display:block; }
 main { padding:8px; }
 .status-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:4px; }
 .status-card { min-height:52px; padding:5px; }
 .status-note { display:none; }
 .overview-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:5px; }
 .overview-grid #encoder_panel { grid-column:1/-1; }
 .overview-grid .panel-heading small { display:none; }
 .encoder-readout { grid-template-columns:repeat(2,minmax(0,1fr)); }
 .connection-row { font-size:12px; }
 #motor_info { font-size:12px; }
 #bar { position:sticky; top:0; z-index:20; }
 .brand-lockup { gap:5px; }
 .brand-title { font-size:16px; }
 .header-tools { gap:5px; }
 .header-device,.header-separator { display:none; }
 #bar button,#bar select { font-size:13px; }
 .live-values { gap:3px; }
 .live-value { padding:5px; }
 .live-value strong { font-size:16px; }
 .summary-grid > * { min-width:0; }
}
/* ---- 运行模式下拉 ---- */
#rpmbar .mode-picker { display:flex; flex-wrap:wrap; align-items:center; gap:6px 12px; margin:0 0 10px; }
.mode-select { position:relative; flex:0 0 auto; min-width:260px; }
#rpmbar #mode_trigger { display:flex; align-items:center; justify-content:space-between; gap:12px; width:100%;
  min-height:46px; padding:6px 12px; text-align:left; color:#ffffff; background:#1677ff; border:1px solid #1677ff;
  border-radius:8px; box-shadow:0 2px 6px #1677ff33; }
#rpmbar #mode_trigger:hover:not(:disabled) { filter:brightness(1.06); }
#rpmbar #mode_trigger:disabled { opacity:.6; cursor:not-allowed; }
.mode-trigger-text { display:flex; flex-direction:column; gap:1px; min-width:0; }
#rpmbar #mode_trigger b { color:#ffffff; font-size:15px; }
#mode_trigger small { color:#d5e8ff; font-size:13px; }
.mode-caret { width:8px; height:8px; margin:-4px 2px 0; border:solid #3b1c1c; border-width:0 2px 2px 0;
  transform:rotate(45deg); transition:transform .15s; }
#mode_trigger[aria-expanded="true"] .mode-caret { transform:rotate(-135deg); margin-top:4px; }
#rpmbar #mode_chips { position:absolute; z-index:20; top:calc(100% + 4px); left:0; min-width:100%; width:320px;
  display:flex; flex-direction:column; gap:2px; margin:0; padding:5px; background:#1a0a0a;
  border:1px solid #2b4059; border-radius:9px; box-shadow:0 10px 28px #1a2d4526; }
#rpmbar #mode_chips[hidden] { display:none; }
#rpmbar #mode_chips .mode-chip { flex-direction:row; align-items:baseline; justify-content:space-between; gap:12px;
  width:100%; padding:8px 10px; background:transparent; border:1px solid transparent; border-radius:6px; }
#rpmbar #mode_chips .mode-chip:hover:not(:disabled),#rpmbar #mode_chips .mode-chip:focus-visible {
  background:#08162b; border-color:#12386c; outline:0; }
#rpmbar #mode_chips .mode-chip.selected { background:#091b34; border-color:#073c85; }
#rpmbar #mode_chips .mode-chip.selected b { color:#5696f1; }
#rpmbar #mode_chips .mode-chip.selected small { color:#88a2c6; }
#rpmbar #mode_chips .mode-chip b { font-size:15px; }
#rpmbar #mode_chips .mode-chip small { font-size:13px; white-space:nowrap; }
#selected_mode_note { flex:1 1 220px; color:#a5b2c2; font-size:14px; }
/* ---- 启动 / 停止 ---- */
.run-actions { display:flex; align-items:center; flex-wrap:wrap; gap:8px 10px; margin:10px 0 4px; }
.run-actions #btn_loop_run,.run-actions #btn_loop_stop { min-width:112px; min-height:38px; font-size:15px; font-weight:700; }
#btn_loop_run.running,#btn_loop_run.running:disabled { color:#ffffff; background:#168a55; border-color:#168a55; opacity:1; }
.run-actions #loop_hint { flex:1 1 200px; }
/* ---- 可折叠参数 ---- */
#mode_tuning { margin-top:10px; padding:0; border:1px solid #273950; border-radius:8px; background:#081020; }
#mode_tuning > summary { display:flex; align-items:baseline; flex-wrap:wrap; gap:4px 10px; padding:9px 12px;
  cursor:pointer; list-style:none; color:#b7c7da; }
#mode_tuning > summary::-webkit-details-marker { display:none; }
#mode_tuning > summary::before { content:""; align-self:center; width:6px; height:6px; margin-right:2px;
  border:solid #7b8da5; border-width:0 2px 2px 0; transform:rotate(-45deg); transition:transform .15s; }
#mode_tuning[open] > summary::before { transform:rotate(45deg); }
#mode_tuning > summary .tune-fold-note { margin-left:auto; color:#98a6b8; font-size:13px; }
#mode_tuning[open] > summary { border-bottom:1px solid #21354d; }
#mode_tuning > :not(summary) { margin-left:12px; margin-right:12px; }
#mode_tuning .tune-actions { margin-bottom:10px; }
#mode_tuning [data-tune-modes][hidden] { display:none !important; }
#rpmbar #mode_chips .mode-chip small { display:inline; }
/* ---- 消除留白: 同一行的卡片一样高, 内容区吃掉多余高度 ---- */
.overview-grid { align-items:stretch; }
.overview-grid > .panel { display:flex; flex-direction:column; }
.overview-grid .live-values,.overview-grid .motor-status-body { flex:1 1 auto; }
.overview-grid .live-values { align-items:stretch; }
/* 输出与温度: 两个读数做成和相电流一样的大数字块, 填满卡片而不是居中留白 */
.overview-grid .motor-status-body { display:flex; }
.overview-grid .motor-readout { flex:1; grid-template-rows:auto 1fr 1fr; }
.overview-grid .motor-readout div { flex-direction:column; justify-content:center; align-items:center;
  padding:8px; border-radius:7px; background:#0b1624; }
.overview-grid .motor-readout strong { font-size:17px; }
.summary-grid > * { flex:1 1 0; width:auto !important; min-width:230px; }
.summary-grid > :nth-child(1) { flex-grow:2.4; }
@media(min-width:1100px){
 /* 左栏 (校准/模式/OTA) 决定这一行的高度, 波形跟着拉伸, 不再各自留白 */
 #rpmbar { align-self:stretch; display:flex; flex-direction:column; }
 #rpmbar > * { align-self:stretch; width:auto; max-width:none; margin-left:0; margin-right:0; }
 #rpmbar > .loop-reserve { flex:1 1 auto; }
 #chart-panel { contain:size; min-height:600px; }
 /* flex-basis 0: 画布高度只由卡片决定, 不能被上一次写入的 backing store 反向撑大 */
 #chart-panel #curves { flex:1 1 0; min-height:200px; }
}

/* ==================== 深色科技感主题 2026-09-29 (只管外观) ==================== */
:root { --neon:#22d3ee; --neon2:#818cf8; --neon-dim:rgba(34,211,238,.18);
        --glass:rgba(10,20,38,.66); --glass2:rgba(6,14,28,.72); --hud-line:rgba(56,189,248,.20);
        --ink:#dcecff; --ink-dim:#7f9bb8; }
html { background:#030712; }
body { background:transparent !important; color:var(--ink) !important; }
body:before { content:""; position:fixed; inset:0; z-index:-1; pointer-events:none;
  background:
    radial-gradient(1100px 620px at 88% -8%, rgba(129,140,248,.20), transparent 62%),
    radial-gradient(900px 520px at -8% 108%, rgba(34,211,238,.16), transparent 60%),
    linear-gradient(rgba(56,189,248,.045) 1px, transparent 1px) 0 0/34px 34px,
    linear-gradient(90deg, rgba(56,189,248,.045) 1px, transparent 1px) 0 0/34px 34px,
    #030712; }
.app-shell,.workspace,main { background:transparent !important; }
::selection { background:rgba(34,211,238,.35); color:#fff; }
* { scrollbar-width:thin; scrollbar-color:#1e3a5f transparent; }

/* ---- 侧栏 ---- */
#sidebar { background:linear-gradient(180deg,rgba(6,14,30,.92),rgba(3,8,20,.96)) !important;
  border-right:1px solid var(--hud-line); box-shadow:4px 0 30px rgba(0,0,0,.45); }
.side-nav a { color:#8fb0cf !important; border:1px solid transparent; transition:all .2s; }
.side-nav a:hover { background:rgba(34,211,238,.08) !important; color:#e6fbff !important; }
.side-nav a.active { color:#e6fbff !important; background:linear-gradient(90deg,rgba(34,211,238,.18),rgba(34,211,238,.02)) !important;
  border-color:rgba(34,211,238,.25); box-shadow:inset 3px 0 0 var(--neon), 0 0 18px rgba(34,211,238,.12) !important; }
.side-nav a.active svg { filter:drop-shadow(0 0 6px var(--neon)); }
.side-foot { color:#4f6c8c !important; font-family:Consolas,monospace; letter-spacing:.08em; }
.brand-mark { background:linear-gradient(135deg,#06b6d4,#6366f1) !important; color:#fff !important;
  box-shadow:0 0 0 1px rgba(255,255,255,.12) inset, 0 0 22px rgba(34,211,238,.55) !important; }

/* ---- 顶栏 + 扫描线 ---- */
#bar { background:linear-gradient(90deg,rgba(4,10,24,.88),rgba(10,22,46,.88)) !important;
  backdrop-filter:blur(12px); border-bottom:1px solid var(--hud-line);
  box-shadow:0 10px 30px rgba(0,0,0,.35); overflow:hidden; }
#bar:after { content:""; position:absolute; left:-30%; bottom:0; height:1px; width:30%;
  background:linear-gradient(90deg,transparent,var(--neon),transparent); animation:hudScan 5s linear infinite; }
@keyframes hudScan { to { left:100%; } }
.brand-title { background:linear-gradient(90deg,#e0fbff,#67e8f9 45%,#a5b4fc); -webkit-background-clip:text; background-clip:text;
  color:transparent !important; letter-spacing:.04em; filter:drop-shadow(0 0 10px rgba(34,211,238,.35)); }
.brand-subtitle { color:#5f86ad !important; font-family:Consolas,monospace; letter-spacing:.12em; }
#bar button { background:rgba(34,211,238,.06) !important; border:1px solid rgba(34,211,238,.28) !important; color:#bfefff !important; }
#bar button:hover { background:rgba(34,211,238,.16) !important; box-shadow:0 0 14px rgba(34,211,238,.3); }
#bar #btn_header_stop { background:linear-gradient(135deg,#e11d48,#9f1239) !important; border:1px solid #fb7185 !important; color:#fff !important;
  letter-spacing:.1em; box-shadow:0 0 18px rgba(244,63,94,.45) !important; }
#bar #btn_header_stop:hover { box-shadow:0 0 28px rgba(244,63,94,.7) !important; }
.header-device { border-color:rgba(34,211,238,.25) !important; background:rgba(3,10,22,.6) !important; }
.header-device:before { animation:hudPulse 1.8s ease-in-out infinite; }
@keyframes hudPulse { 50% { box-shadow:0 0 0 5px transparent, 0 0 12px currentColor; opacity:.55; } }
#motor_label { color:#bfefff !important; font-family:Consolas,"Microsoft YaHei UI",monospace; }

/* ---- 玻璃卡片 + HUD 角标 ---- */
.panel,.calibration-panel,#rpmbar,.summary-grid > *,.overview-grid > *,#chart-panel,.status-strip,.status-card {
  color:var(--ink); }
.panel,.calibration-panel,#chart-panel,#rpmbar {
  background:
    linear-gradient(var(--neon),var(--neon)) left top/16px 1px no-repeat,
    linear-gradient(var(--neon),var(--neon)) left top/1px 16px no-repeat,
    linear-gradient(var(--neon2),var(--neon2)) right bottom/16px 1px no-repeat,
    linear-gradient(var(--neon2),var(--neon2)) right bottom/1px 16px no-repeat,
    var(--glass) !important;
  border:1px solid var(--hud-line) !important; border-radius:6px !important;
  box-shadow:0 0 0 1px rgba(255,255,255,.015) inset, 0 12px 34px rgba(0,0,0,.38) !important;
  backdrop-filter:blur(8px); }
.panel .panel,#rpmbar .calibration-panel,#rpmbar .loop-reserve { background:transparent !important; box-shadow:none !important; backdrop-filter:none; }
#rpmbar .calibration-panel { border:1px solid rgba(56,189,248,.14) !important; }
.status-card { background:transparent !important; border-color:var(--hud-line) !important; }
.status-card strong { color:#eafcff !important; text-shadow:0 0 12px rgba(34,211,238,.35); }
.panel-heading { border-color:rgba(56,189,248,.14) !important; }
.panel-heading h2 { color:#eafcff !important; letter-spacing:.08em; text-shadow:0 0 12px rgba(34,211,238,.28); }
.panel-heading h2:before,.panel-heading:before { background:linear-gradient(180deg,var(--neon),var(--neon2)) !important;
  box-shadow:0 0 10px var(--neon); width:3px !important; }
.panel-heading small { color:#5f86ad !important; font-family:Consolas,"Microsoft YaHei UI",monospace; letter-spacing:.06em; }
.page-heading h1 { color:#eafcff !important; }
.page-heading p,.page-stamp { color:#6f90b3 !important; }

/* ---- 读数块: 深色玻璃 + 发光等宽数字 ---- */
.live-value,.overview-grid .motor-readout div,.encoder-angle,.encoder-metrics .live-value,.control-feedback,.control-feedback > *,.target-fields {
  background:linear-gradient(180deg,rgba(14,30,56,.75),rgba(6,14,28,.75)) !important;
  border:1px solid rgba(56,189,248,.14) !important; border-radius:5px !important; }
.live-value strong,.encoder-angle strong,.control-feedback strong,.overview-grid .motor-readout strong,
#encoder_raw,#encoder_speed,#phase_a,#phase_b,#phase_c,#v_ntc,#dash_step {
  font-family:"Cascadia Mono",Consolas,monospace !important; color:#effdff !important;
  text-shadow:0 0 10px rgba(34,211,238,.55), 0 0 22px rgba(34,211,238,.18); }
.live-value span,.live-value small { color:#6f90b3 !important; }

/* ---- 表单 ---- */
input,select,textarea { background:rgba(2,8,20,.85) !important; color:#e6f7ff !important;
  border:1px solid rgba(56,189,248,.26) !important; border-radius:4px !important; }
input:focus,select:focus,textarea:focus { outline:0; border-color:var(--neon) !important;
  box-shadow:0 0 0 3px rgba(34,211,238,.16), 0 0 16px rgba(34,211,238,.25) !important; }
input[type=checkbox] { accent-color:var(--neon); }
label,.loop-field span,.tune-field span { color:#8fb0cf; }

/* ---- 按钮 ---- */
button { transition:box-shadow .2s, filter .2s, background .2s; }
#rpmbar button,.calibration-panel button,#tracebar button,#ctl button,.tune-actions button {
  background:rgba(34,211,238,.06) !important; color:#bfefff !important; border:1px solid rgba(56,189,248,.28) !important; }
#rpmbar button:hover:not(:disabled),#tracebar button:hover,#ctl button:hover { background:rgba(34,211,238,.14) !important;
  box-shadow:0 0 14px rgba(34,211,238,.25); }
#rpmbar #btn_loop_run,#calibration_panel #btn_calibrate,.connection-actions button {
  background:linear-gradient(135deg,#06b6d4,#4f46e5) !important; color:#fff !important; border:1px solid rgba(165,243,252,.55) !important;
  letter-spacing:.14em; font-weight:700; box-shadow:0 0 20px rgba(34,211,238,.38), 0 0 0 1px rgba(255,255,255,.08) inset !important; }
#rpmbar #btn_loop_run:hover:not(:disabled),.connection-actions button:hover { box-shadow:0 0 30px rgba(34,211,238,.6) !important; filter:brightness(1.1); }
#calibration_panel #btn_calibrate:disabled { filter:saturate(.35) brightness(.7); box-shadow:none !important; }
#rpmbar #btn_loop_run.running,#rpmbar #btn_loop_run.running:disabled {
  background:linear-gradient(135deg,#10b981,#0d9488) !important; border-color:#6ee7b7 !important;
  animation:hudRun 1.4s ease-in-out infinite; opacity:1; }
@keyframes hudRun { 50% { box-shadow:0 0 34px rgba(16,185,129,.75); } }
#rpmbar #btn_loop_stop { background:rgba(244,63,94,.10) !important; color:#fda4af !important; border:1px solid rgba(244,63,94,.55) !important;
  letter-spacing:.14em; font-weight:700; }
#rpmbar #btn_loop_stop:hover:not(:disabled) { background:rgba(244,63,94,.2) !important; box-shadow:0 0 20px rgba(244,63,94,.45) !important; }
#ctl button.on { color:#6ee7b7 !important; border-color:rgba(16,185,129,.6) !important; background:rgba(16,185,129,.12) !important; }

/* ---- 模式下拉 ---- */
#rpmbar #mode_trigger { background:linear-gradient(135deg,rgba(6,182,212,.9),rgba(79,70,229,.9)) !important;
  border:1px solid rgba(165,243,252,.55) !important; box-shadow:0 0 22px rgba(34,211,238,.35) !important; }
#rpmbar #mode_trigger small { color:#cffafe !important; }
#rpmbar #mode_chips { background:rgba(4,12,28,.96) !important; border:1px solid rgba(56,189,248,.35) !important;
  box-shadow:0 18px 50px rgba(0,0,0,.6), 0 0 24px rgba(34,211,238,.15) !important; backdrop-filter:blur(14px); }
#rpmbar #mode_chips .mode-chip { color:#cfe8ff !important; }
#rpmbar #mode_chips .mode-chip b { color:#eafcff !important; }
#rpmbar #mode_chips .mode-chip small { color:#6f90b3 !important; }
#rpmbar #mode_chips .mode-chip:hover,#rpmbar #mode_chips .mode-chip:focus { background:rgba(34,211,238,.1) !important; border-color:rgba(34,211,238,.35) !important; }
#rpmbar #mode_chips .mode-chip.selected { background:rgba(34,211,238,.16) !important; border-color:var(--neon) !important;
  box-shadow:inset 3px 0 0 var(--neon); }

/* ---- 折叠区 / details ---- */
details { background:rgba(6,14,28,.55) !important; border:1px solid rgba(56,189,248,.16) !important; }
details > summary { color:#a8d8f0; }
details[open] > summary { color:#e6fbff; }
#mode_tuning { background:rgba(6,14,28,.6) !important; border-color:rgba(56,189,248,.2) !important; }
#mode_tuning > summary b { color:#eafcff !important; }

/* ---- 波形 ---- */
#curves { background:#040a16 !important; border:1px solid rgba(56,189,248,.22) !important;
  box-shadow:inset 0 0 50px rgba(34,211,238,.06), 0 0 24px rgba(34,211,238,.06) !important; }

/* ---- 表格 / 事件 ---- */
table { color:#cfe3f7; }
th { background:rgba(34,211,238,.06) !important; color:#6fb8d8 !important; letter-spacing:.08em; }
td,th { border-color:rgba(56,189,248,.1) !important; }
tr:hover td { background:rgba(34,211,238,.04); }

/* ---- 状态色保持可读 ---- */
.bad,#v_conn.bad { color:#fb7185 !important; }
.state-chip { background:rgba(148,163,184,.1) !important; color:#9fb6cf !important; }
.state-chip.running,.calib-badge.done { background:rgba(16,185,129,.14) !important; color:#6ee7b7 !important; }
.state-chip.bad,.calib-badge.failed { background:rgba(244,63,94,.14) !important; color:#fda4af !important; }
.calib-badge.busy { background:rgba(34,211,238,.14) !important; color:#67e8f9 !important; }
.calib-gate,#profile_note { background:rgba(245,158,11,.1) !important; color:#fcd34d !important; border-color:rgba(245,158,11,.35) !important; }
.workflow-help { background:rgba(10,20,38,.6) !important; border-left:2px solid var(--neon) !important; }
progress,.calib-progress { accent-color:var(--neon); filter:drop-shadow(0 0 6px rgba(34,211,238,.5)); }

@media (prefers-reduced-motion:reduce) {
  #bar:after,.header-device:before,#rpmbar #btn_loop_run.running { animation:none !important; } }

/* 字号放大 + 隐藏装饰小字 (2026-09-29) */
.panel-heading small { display:none !important; }
.live-value strong,.overview-grid .motor-readout strong,.encoder-angle strong { font-size:28px !important; line-height:1.2; }
.live-value span,.overview-grid .motor-readout span,.live-value small { font-size:14px !important; }
/* 关键读数标签：增强相名、温度和步长的可读性，保持原布局。 */
.overview-grid .live-values .live-value > span,
.overview-grid .motor-readout > div > span {
  font-size:20px !important; font-weight:700; line-height:1.5;
  color:#d5e8f7 !important;
}
.overview-grid .live-values .live-value > small {
  font-size:18px !important; color:#c6d9eb !important;
}
.status-card strong { font-size:20px !important; }
.panel-heading h2 { font-size:17px !important; }
#events th:nth-child(2),#events td:nth-child(2),#events th:nth-child(3),#events td:nth-child(3),
.event-table th:nth-child(-n+3),.event-table td:nth-child(-n+3) { white-space:nowrap; }
</style>
</head>
<body>

<div class="app-shell">
<aside id="sidebar" aria-label="主导航">
  <div class="side-brand"><span class="brand-mark" aria-hidden="true">
    <svg viewBox="0 0 24 24" fill="none"><path d="M5 15.8 8.2 6h3l-1.1 4h5.1l-4.3 8H8.1l1.5-5.1H5Z" fill="currentColor"/></svg>
  </span></div>
  <nav class="side-nav">
    <a class="active" href="#overview"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m3 10 9-7 9 7v10H5V10M9 20v-6h6v6"/></svg><span>总览</span></a>
    <a href="#params_box"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2" fill="#203b59"/><circle cx="15" cy="12" r="2" fill="#203b59"/><circle cx="8" cy="18" r="2" fill="#203b59"/></svg><span>参数</span></a>
    <a href="#chart-panel"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 13h3l3-8 4 14 3-8h5"/></svg><span>波形</span></a>
    <a href="#rpmbar"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/></svg><span>控制</span></a>
    <a href="#event-logs"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 3h9l4 4v14H6zM14 3v5h5M9 12h7M9 16h7"/></svg><span>日志</span></a>
  </nav>
</aside>

<div class="workspace">
<header id="bar">
  <div class="brand-lockup">
    <span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="M5 15.8 8.2 6h3l-1.1 4h5.1l-4.3 8H8.1l1.5-5.1H5Z" fill="currentColor"/></svg></span>
    <div><span class="brand-title">MotorTune Pro</span><span class="brand-subtitle">SG-6010HB　<span id="brand_version">—</span></span></div>
  </div>
  <div class="header-tools"><button id="btn_header_stop" type="button">停止输出</button>
    <span class="header-device" id="header_link"><span id="v_conn" class="bad">正在连接</span></span>
    <span class="header-separator"></span>
    <span id="motor_label" title="本工程为 SG-6010HB OTA/传感器验证版；固件侧档案在 foc/foc_config.h">SG-6010HB 电机</span>
    <button id="btn_shutdown" type="button" title="停止输出并关闭后台网页服务">关闭服务</button>
    <button id="btn_reconnect_top" title="重新打开 USB-CAN 适配器；服务端也会自动重连。">重连</button>
  </div>
</header>

<main>
  <section class="page-heading">
    <div><h1>设备控制总览</h1><p>实时连接、遥测与电机运行状态</p></div>
    <span class="page-stamp">N32G455REL7 · CAN 控制台</span>
  </section>
  <details id="workflow_help" class="workflow-help"><summary>实机工作流与验收 · 每个环开始前停机，等待你确认</summary>
    <div class="dwrap"><p>① 开环 30 rpm / 1.5 V / 6 A / 5 s，检查原始编码器连续运动；起动不足先停机分析，再短测 3 V。② 停机检查 raw 抖动与错误，再做约 50 s 开环校准。③ 电压 → 电流 → 力矩 → 速度 → 位置 → MIT，逐阶段批准。</p>
    <p>运行时长默认不限（参数里可设 1–120 s）；关掉页面立即停机，页面卡死 6 s、CAN 断 1 s 自动停机；故障或数据失效立即停机。目标默认 0 不会主动转动。编码器电角度需校准成功；三相电流和角度来自不同遥测流，不能拿网页曲线当同步采样验证。</p>
    <p><a href="/guide?name=project" target="_blank">统一工程说明、进度与版本流程</a></p></div>
  </details>
  <div id="v_errbox" class="bad" role="status" aria-live="polite"></div>

  <section class="status-grid" id="overview" aria-label="设备摘要">
    <article class="status-card"><span class="status-icon green"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m5 12 4 4L19 6"/></svg></span><div class="status-copy"><span class="status-label">在线状态</span><strong class="status-value" id="dash_online">等待设备</strong><span class="status-note" id="dash_reason">CAN 链路状态</span></div></article>
    <article class="status-card"><span class="status-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/></svg></span><div class="status-copy"><span class="status-label">设备模式</span><strong class="status-value" id="dash_control_mode">等待反馈</strong><span class="status-note">设备实际运行的模式</span></div></article>
    <article class="status-card"><span class="status-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 2v3m6-3v3M9 19v3m6-3v3M2 9h3m14 0h3M2 15h3m14 0h3"/></svg></span><div class="status-copy"><span class="status-label">固件版本</span><strong class="status-value" id="v_ver">-</strong><span class="status-note">活动槽 <span id="v_slot">-</span></span></div></article>
    <article class="status-card"><span class="status-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 12h4l3-8 5 16 3-8h5"/></svg></span><div class="status-copy"><span class="status-label">遥测采样率</span><strong class="status-value"><span id="dash_rate">-</span></strong><span class="status-note" id="dash_telem_state">等待遥测</span></div></article>
    <article class="status-card"><span class="status-icon red"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m12 3 10 18H2L12 3Z"/><path d="M12 9v5m0 3h.01"/></svg></span><div class="status-copy"><span class="status-label">保护状态</span><strong class="status-value" id="dash_fault">等待读取</strong><span class="status-note" id="dash_loops_note">FOC 循环 <span id="v_loops">-</span></span></div></article>
  </section>

  <section class="overview-grid" aria-label="设备运行面板">
    <article class="panel">
      <div class="panel-heading"><h2>设备连接</h2></div>
      <div class="connection-rows">
        <div class="connection-row"><span>适配器</span><strong id="adapter_open">读取中</strong></div>
        <div class="connection-row"><span>通信通道</span><strong id="adapter_channel">-</strong></div>
        <div class="connection-row"><span>波特率</span><strong id="adapter_bitrate">-</strong></div>
        <div class="connection-row"><span>节点状态</span><strong id="device_reason">-</strong></div>
      </div>
      <details class="profile-reference"><summary>电机档案与参数说明</summary><span id="motor_info" class="k">电机档案读取中</span>
      <span id="profile_note">电机档案参数需在上电前核实。</span></details>
      <div class="connection-actions"><button id="btn_reconnect_inline" type="button">重新连接设备</button></div>
    </article>

    <article class="panel">
      <div class="panel-heading"><h2>实时相电流</h2></div>
      <div class="live-values">
        <div class="live-value"><span>A 相</span><strong id="dash_ia">—</strong><small>A</small></div>
        <div class="live-value"><span>B 相</span><strong id="dash_ib">—</strong><small>A</small></div>
        <div class="live-value"><span>C 相</span><strong id="dash_ic">—</strong><small>A</small></div>
      </div>
    </article>

    <article class="panel">
      <div class="panel-heading"><h2>输出与温度</h2></div>
      <div class="motor-status-body">
        <div class="motor-readout">
          <span class="state-chip" id="dash_motor_state">状态读取中</span>
          <div><span>换向步长</span><strong id="dash_step">— ms</strong></div>
          <div><span>绕组温度</span><strong id="v_ntc">--.-</strong></div>
        </div>
      </div>
    </article>
    <article class="panel" id="encoder_panel">
      <div class="panel-heading"><h2>磁编码器</h2><small>AS5048A · CAN 0xA4</small></div>
      <div class="encoder-readout">
        <div class="encoder-angle"><span id="encoder_electrical_label">换相电角度</span><strong id="encoder_electrical_angle">—</strong><small>° · <span id="encoder_electrical_state">等待校准参数</span></small></div>
        <div class="encoder-angle"><span>转子机械单圈角</span><strong id="encoder_angle">—</strong><small>° · 0–360°</small></div>
        <div class="encoder-metrics">
          <div class="live-value"><span>原始计数</span><strong id="encoder_raw">—</strong><small>/ 16384</small></div>
          <div class="live-value"><span>估算转速</span><strong id="encoder_speed">—</strong><small>rpm</small></div>
        </div>
      </div>
      <small class="encoder-note" id="encoder_basis">电角度按转子机械角与极对数换算，并相对校准偏置取 360° 模。</small>
      <div class="encoder-meta">传感器状态 <strong id="encoder_state">等待角度帧</strong></div>
      <div class="encoder-meta">最近帧 <strong id="encoder_age">—</strong>　累计错误 <strong id="encoder_errors">—</strong></div>
    </article>
  </section>

    <div id="rpmbar">
      <section id="calibration_panel" class="panel calibration-panel">
        <div class="panel-heading"><h2>编码器开环校准</h2></div>
        <div class="calib-head">
          <span id="calib_badge" class="calib-badge">等待校准</span>
          <button id="btn_calibrate" type="button" disabled>开始自校准</button>
          <button id="btn_calibrate_abort" type="button" disabled>中止</button>
        </div>
        <div class="calib-row">
          <progress id="calib_progress" class="calib-progress" max="100" value="0"></progress>
          <strong id="calib_status">等待校准</strong>
        </div>
        <div class="calib-note">约 50 秒，完成后自动停止并保存。<details><summary>电流限制与重新校准条件</summary><p>当前固件的相电流硬上限为 1.0 A，校准目标与过流阈值都不能超过该值。先对齐，再正反扫角计算方向和圆周平均零点。首次使用本版、更换电机或调整磁铁安装后需要重新校准；成功保存后重启仍有效。</p></details></div>
      </section>

      <section class="loop-reserve" >
        <div class="panel-heading"><h2>控制模式</h2></div>
        <div class="mode-picker">
          <div class="mode-select">
            <button id="mode_trigger" type="button" aria-haspopup="listbox" aria-expanded="false" aria-controls="mode_chips">
              <span class="mode-trigger-text"><b id="mode_trigger_name">正弦开环</b><small id="mode_trigger_sub">无需编码器 · 转速</small></span>
              <span class="mode-caret" aria-hidden="true"></span>
            </button>
            <div id="mode_chips" class="mode-chips" role="listbox" aria-label="运行模式选择" hidden>
          <button class="mode-chip selected" type="button" data-control-mode="0"><b>正弦开环</b><small>无需编码器 · 转速</small></button>
          <button class="mode-chip" type="button" data-control-mode="4"><b>电压模式</b><small>编码器定向 · Ud / Uq</small></button>
          <button class="mode-chip" type="button" data-control-mode="5"><b>电流环</b><small>编码器定向 · Iq</small></button>
          <button class="mode-chip" type="button" data-control-mode="2"><b>速度环</b><small>编码器测速 · rpm</small></button>
          <button class="mode-chip" type="button" data-control-mode="3"><b>位置环</b><small>编码器位置 · 多圈角</small></button>
          <button class="mode-chip" type="button" data-control-mode="1"><b>力矩模式</b><small>Kt 换算 · N·m</small></button>
          <button class="mode-chip" type="button" data-control-mode="6"><b>MIT 力矩</b><small>位置+速度 · 阻抗</small></button>
            </div>
          </div>
          <input id="control_mode_select" type="hidden" value="0" aria-hidden="true">
          <span id="selected_mode_note">正弦开环：旋转电压驱动，不需要编码器，也不需要先校准。</span>
        </div>
        <div class="dwrap">
          <div class="target-fields">
            <div class="target-group" data-target-mode="0">
              <label class="loop-field">开环目标转速 (rpm)<input id="in_rpm" type="number" min="3" max="200" step="1" value="30"></label>
              <input id="in_rpm_slider" type="range" min="3" max="200" step="1" value="30" aria-label="开环目标转速滑条（rpm）">
            </div>
            <div class="target-group" data-target-mode="1" hidden>
              <label class="loop-field">目标估算转子力矩 (N·m，Kt=0.0606)<input id="target_torque_nm" type="number" min="-5" max="5" step="0.01" value="0"></label>
            </div>
            <div class="target-group" data-target-mode="2" hidden>
              <label class="loop-field">速度目标 (rpm)<input id="target_speed_rpm" type="number" min="-300" max="300" step="1" value="0"></label>
            </div>
            <div class="target-group" data-target-mode="3" hidden>
              <label class="loop-field">输出轴位置目标（°，相对本次上电；允许多圈 ±200000）<input id="target_position_deg" type="number" min="-200000" max="200000" step="0.1" value="0"></label>
              <label class="loop-field">输出轴相对移动 (°)<input id="position_rel_deg" type="number" min="-360" max="360" step="0.1" value="0.5"></label>
              <button id="btn_position_rel" type="button">目标 = 当前位置 + 相对角</button>
              <span id="position_readout" class="loop-readout-strong"></span>
            </div>
            <div class="target-group" data-target-mode="4" hidden>
              <label class="loop-field">Ud 目标 (V)<input id="target_voltage_d_v" type="number" min="-8" max="8" step="0.1" value="0"></label>
              <label class="loop-field">Uq 目标 (V)<input id="target_voltage_v" type="number" min="-8" max="8" step="0.1" value="0"></label>
            </div>
            <div class="target-group" data-target-mode="5" hidden>
              <label class="loop-field">Iq 目标 (A 峰值)<input id="target_current_a" type="number" min="-5" max="5" step="0.1" value="1.0"></label>
            </div>
            <div class="target-group" data-target-mode="6" hidden>
              <label class="loop-field">MIT 位置 (rad)<input id="target_mit_position_rad" type="number" min="-6283.185" max="6283.185" step="0.01" value="0"></label>
              <label class="loop-field">MIT 速度 (rad/s)<input id="target_mit_speed_rad_s" type="number" min="-31.416" max="31.416" step="0.1" value="0"></label>
              <label class="loop-field">MIT Kp (N·m/rad)<input id="target_mit_kp" type="number" min="0" max="10" step="0.01" value="0.5"></label>
              <label class="loop-field">MIT Kd (N·m·s/rad)<input id="target_mit_kd" type="number" min="0" max="5" step="0.02" value="0.02"></label>
              <label class="loop-field">MIT 估算转子前馈力矩 (N·m)<input id="target_mit_torque_nm" type="number" min="-5" max="5" step="0.01" value="0"></label>
            </div>
          </div>
          <small id="calib_gate" class="calib-gate" hidden></small>
          <div id="mode_feedback" class="control-feedback" hidden>
            <span>Id 实测 (A)<strong id="loop_id">—</strong></span>
            <span>Iq 实测 (A)<strong id="loop_iq">—</strong></span>
            <span>转速 (rpm)<strong id="loop_speed">—</strong></span>
            <span>输出轴位置 (°)<strong id="loop_position">—</strong></span>
          </div>
          <button id="btn_capture_position" type="button" hidden>以当前位置为目标</button>
          <div class="control-actions run-actions" role="group" aria-label="启动与停止">
            <button id="btn_loop_run" type="button" disabled>启动</button>
            <button id="btn_loop_stop" type="button">停止</button>
            <span id="loop_hint" class="loop-reserve-note" aria-live="polite"></span>
          </div>
          <details id="mode_tuning" class="tuning-panel">
            <summary><b id="tune_title">调参</b><span id="tune_summary" class="loop-reserve-note"></span></summary>
            <div class="tune-grid tune-common">
              <label>运行时长 (s，0 = 不限时)<input id="mode_seconds" type="number" min="0" max="255" step="1" value="0"></label>
              <label data-tune-modes="0">电压幅值上限 (V)<input id="open_voltage" type="number" min="0.1" max="8" step="0.1" value="3"></label>
              <label data-tune-modes="0">相电流峰值上限 (A)<input id="open_current" type="number" min="0.1" max="1" step="0.1" value="1"></label>
            </div>
            <div id="tune_fields" class="tune-grid"></div><details id="tune_advanced"><summary>滤波、斜坡与电机参数</summary><div id="tune_advanced_fields" class="tune-grid"></div></details>
            <div class="control-actions tune-actions" data-tune-modes="1,2,3,4,5,6">
              <button id="btn_tune_read" type="button">读取设备参数</button>
              <button id="btn_tune_apply" type="button">应用并保存到设备</button>
              <span class="loop-reserve-note">改过的参数在点「启动」时也会自动写入。</span>
            </div>
          </details>
          <div class="control-readout">
            <span>固件模式：<strong id="control_mode_status">—</strong></span>
            <span>Iq 目标：<strong id="control_iq_status">— A</strong></span>
            <span>启动阶段：<strong id="control_assist_stage">—</strong></span>
            <span>饱和：<strong id="control_assist_flags">—</strong></span>
            <span><span hidden>标定状态：<strong id="control_calib_status">—</strong></span></span>
          </div>
        </div>
      </section>

      <details class="ota-panel"><summary>固件更新（使用你现有 CAN-OTA 流程）</summary>
        <div class="panel-heading"><h2>xige_foc CAN-OTA</h2><small>编译当前工程并升级设备</small></div>
        <div class="ota-row">
          <button id="btn_ota_onekey" type="button">一键编译并 CAN-OTA</button>
          <strong id="ota_status">待命 · 目标工程 xige_foc</strong>
        </div>
        <small class="loop-reserve-note">执行前会先发送停机命令并释放网页的 CAN 适配器；Makefile 会递增固件版本、编译 A/B 槽并更新设备。</small>
        <details class="ota-log"><summary>查看升级日志</summary><pre id="ota_output" aria-live="polite">尚未运行</pre></details>
      </details>
      <span id="rpm_hint" class="k"></span>
    </div>

  <section class="summary-grid" aria-label="事件与设备信息">
    <article class="panel" id="event-logs">
      <div class="panel-heading"><h2>设备事件</h2></div>
      <table class="event-table">
        <thead><tr><th>时间</th><th>级别</th><th>模块</th><th>事件</th></tr></thead>
        <tbody id="event_rows"><tr><td colspan="4" class="k">等待设备状态</td></tr></tbody>
      </table>
    </article>
    <article class="panel">
      <div class="panel-heading"><h2>系统信息</h2></div>
      <div class="sys-list">
        <div class="sys-row"><span>电机档案</span><strong id="sys_model">-</strong></div>
        <div class="sys-row"><span>控制器</span><strong>N32G455REL7</strong></div>
        <div class="sys-row"><span>固件 / 槽位</span><strong id="sys_version">-</strong></div>
        <div class="sys-row"><span>CAN 节点</span><strong id="sys_nodes">-</strong></div>
        <div class="sys-row"><span>收 / 发帧</span><strong id="sys_traffic">-</strong></div>
      </div>
    </article>
    <article class="panel">
      <div class="panel-heading"><h2>三相电流</h2></div>
      <div class="phase-body">
        <div class="phase-list">
          <div><span class="phase-label"><i class="phase-dot" style="background:#ef4e5b"></i>A 相</span><strong id="phase_a">— A</strong></div>
          <div><span class="phase-label"><i class="phase-dot" style="background:#1e8fff"></i>B 相</span><strong id="phase_b">— A</strong></div>
          <div><span class="phase-label"><i class="phase-dot" style="background:#20b767"></i>C 相</span><strong id="phase_c">— A</strong></div>
        </div>
      </div>
    </article>
  </section>

  <section class="panel chart-panel" id="chart-panel"><div class="panel-heading"><h2>实时波形</h2></div>
  <div id="tracebar"
       style="display:flex;flex-wrap:wrap;gap:4px 14px;align-items:center;margin:8px 0 6px;font-size:14px">
    <span class="k">显示曲线：</span>
    <label><input type="checkbox" class="tchk" data-trace="ia" checked> A 相</label>
    <label><input type="checkbox" class="tchk" data-trace="ib" checked> B 相</label>
    <label><input type="checkbox" class="tchk" data-trace="ic" checked> C 相</label>
    <label><input type="checkbox" class="tchk" data-trace="theta" checked> 编码器电角度</label>
    <button id="btn_chk_all" style="font-size:14px">全选</button>
    <button id="btn_chk_none" style="font-size:14px">全不选</button>
    <button id="btn_chk_cur" style="font-size:14px">只看电流</button>
  </div>
  <canvas id="curves"></canvas>

  <div id="ctl">
    <button id="btn_telem">遥测：关闭</button>

    <label class="k">采样周期
      <select id="sel_period">
        <option value="5">5 ms</option>
        <option value="10" selected>10 ms</option>
        <option value="20">20 ms</option>
        <option value="50">50 ms</option>
        <option value="100">100 ms</option>
      </select>
    </label>
    <button id="btn_pause">暂停</button>
    <button id="btn_clear">清空曲线</button>
    <button id="btn_export_waves">导出四路数据</button>
    <button id="btn_export_status">导出状态与参数</button>
    <button id="btn_reconnect">重连 CAN</button>
    <span id="tstate">正在等待遥测…</span>
  </div>
  </section>

  <section id="device-details" class="details-grid">
  <details id="adv_motor" >
    <summary>高级：手动设置（一般不用。上面填转速就够了）</summary>
    <div class="dwrap">
      <div class="bar" style="flex-wrap:wrap">
        <label class="k">模式
          <select id="sel_mode" title="只保留正弦开环（六步模式已删除）">
            <option value="0" selected>正弦开环</option>
          </select>
        </label>
        <label class="k">电流上限
          <input id="in_cur" type="number" step="100" min="0" max="5000" value="3000" style="width:6em"
                 title="相电流目标 mA；设备相电流硬上限为 5000 mA (参数 ID 7)">
          mA
        </label>
        <button id="btn_motor"
 disabled>输出: 状态读取中</button>
        <label class="k">目标电压
          <input id="in_volt" type="number" step="0.05" min="0.1" max="5" value="5.0"
                 style="width:5em" title="电流环的占空比天花板(V)。5V 是实测能转的值; 给太低会原地不转">
          V
        </label>
        <label class="k">母线
          <input id="in_vbus" type="number" step="0.5" min="1" max="60" value="48"
                 style="width:4.5em" title="用来把电压换算成占空比; 按当前接入的48V电源填写">
          V
        </label>
        <label class="k">换向步长
          <input id="in_stepms" type="number" step="1" min="5" max="5000" value="10"
                 style="width:5em" title="10极对: 1000/step_ms = rpm, 10ms=100rpm, 5ms=200rpm">
          ms
        </label>
      </div>
      <div class="k">高级开环参数只用于有限时长诊断。<b>电压是幅值上限，电流是相电流峰值限制</b>；
        真正决定发热的是<b>电流上限</b>（铜损 ≈ 2·I²·R_相，7010 每相 0.275Ω，与转速无关）。
        常规操作使用上面的模式面板；高级设置不代替闭环目标。
        模式只保留<b>正弦开环</b>（六步模式已删除）；默认预设 正弦开环 / 3000mA / 5V。</div>
    </div>
  </details>

  <details id="params_box">
    <summary>设备参数</summary>
    <div class="dwrap">
      <div class="bar">
        <button id="btn_refresh">读取参数</button>
        <button id="btn_save">保存到设备</button>
        <span id="pmsg" class="warn"></span>
      </div>
      <table>
        <thead><tr><th>编号</th><th>参数</th><th>当前值</th><th>单位</th>
        <th>范围</th><th>默认值</th><th>状态</th><th>写入</th></tr></thead>
        <tbody id="ptable"></tbody>
      </table>
    </div>
  </details>
  </section>
</main>
</div>
</div>

<script>
"use strict";

/* ------------------------------------------------------------------ *
 * Live curves: one plain canvas, no chart library, no CDN, fully
 * offline.
 *
 * The phase-current traces (ia, ib, ic in A) share one rolling ~10 s
 * window.  The device streams at 50..100 Hz, so the curves are fed from
 * /api/telem/batch (polled every 100 ms): that endpoint hands back EVERY
 * sample the server received since the page's cursor, oldest first, and
 * all of them are appended to a bounded ring buffer (~4000 entries).
 * Nothing is decimated any more -- /api/status only ever carried the
 * NEWEST frame, which is why the old page plotted 2 points per second and
 * showed aliased garbage instead of the real waveform.
 *
 * Samples carry no device timestamp, so their X positions are assigned
 * here: each batch is spaced evenly using the effective interval derived
 * from its measured rate_hz (falling back to the selected period), and the
 * newest sample of the batch ends at "now", which keeps the window sliding
 * smoothly.  When the client has fallen behind (dropped:true) the trace is
 * BROKEN instead of being joined across the missing stretch.
 *
 * /api/status stays on its own 200 ms timer for the status bar, the
 * parameter table and the connection state.
 *
 * Every current trace is auto-scaled with a MINIMUM span (0.2 A), so the
 * ~13 mA quantisation noise of an idle board cannot be amplified into a
 * full-screen fake waveform.  Above that minimum the scale still follows
 * the data and the zero line plus the live numeric label stay.
 *
 * The board has no bus-voltage sense channel, so vbus is never drawn as
 * "0.0 V": while it stays 0 the trace is hidden and one short note says
 * why.  If a future board reports a non-zero value it reappears.
 * ------------------------------------------------------------------ */
var WINDOW_S = 10;        /* visible time span, seconds */
var RING_MAX = 4000;      /* bounded browser-side history (== server keep) */
var POLL_MS  = 200;       /* /api/status poll (status bar, params, link) */
var BATCH_MS = 100;       /* /api/telem/batch poll (the curves) */
var RATE_MIN = 10;        /* a rate outside this range is not trusted */
var RATE_MAX = 500;       /* ...and the period selector is used instead */

/* CSS colors must mirror the :root palette (canvas cannot use var()). */
var C_BG = "#040a16", C_PANEL_LINE = "#12304a", C_GRID = "#0c2236",
    C_GRID_T = "#0a1a2b", C_ZERO = "#1f5577", C_DIM = "#6f9cc0";

/* minSpan: the autoscale never zooms in tighter than this, so sensor
   quantisation noise stays a small wiggle instead of a full-screen wave. */
var BASE_TRACES = [
  { key:"ia", label:"A 相电流", unit:"A", div:1000, dec:2, color:"#f0646e",
    minSpan:0.2 },
  { key:"ib", label:"B 相电流", unit:"A", div:1000, dec:2, color:"#61a8ff",
    minSpan:0.2 },
  { key:"ic", label:"C 相电流", unit:"A", div:1000, dec:2, color:"#4fd0a0",
    minSpan:0.2 }
];
var encoderRing = [], encoderCursor=null, encoderCalibrationKey=null;
var ring = [];            /* [{t, ia, ib, ic, vbus, brk}] oldest first */
var batchCursor = null;   /* last telemetry seq the page has absorbed */
var lastTelem = null;     /* newest sample seen (raw units) for the labels */
var telemOn = false;
var telemRequestPending = false;
var telemManuallyDisabled = false;
var telemRetryAfter = 0;
var encoderReadable = false;
var telemRate = null;     /* measured rate, from /api/telem/batch */
var telemStale = false;
var lastDataT = null;     /* wall clock of the last absorbed batch */
var lastStatusAge = null; /* fallback age from /api/status */
var periodSynced = false; /* server period mirror adopted yet? */
var paused = false;
var vbusFitted = false;   /* flips true only if the device reports != 0 */
var firstStatus = true;   /* the load-time status enables telemetry once */
var encoderParamsRequested = false;
var dashboardLogState = null;

function $(id) { return document.getElementById(id); }

/* Identical text/class values do not need another DOM/style invalidation. */
function setNodeText(el, value) {
  var text = value === null || value === undefined ? "" : String(value);
  if (el && el.textContent !== text) { el.textContent = text; }
}
function setNodeClass(el, value) {
  if (el && el.className !== value) { el.className = value; }
}

/* Keep polling cadence, but never queue another copy of an unfinished read. */
var pendingPolls = Object.create(null);
function pollOnce(key, read) {
  if (pendingPolls[key]) { return pendingPolls[key]; }
  pendingPolls[key] = Promise.resolve().then(read).finally(function () {
    delete pendingPolls[key];
  });
  return pendingPolls[key];
}

var controlToken = "__CONTROL_TOKEN__";
var commandEpoch = Number("__COMMAND_EPOCH__");
document.getElementById("btn_shutdown").onclick = async function () {
  var button=this; button.disabled=true; button.textContent="正在关闭…";
  try {
    var result=await fetchJSON('/api/shutdown');
    if(!result.ok){throw Error(result.error || "关闭失败");}
    document.querySelectorAll('button,input,select').forEach(function(el){el.disabled=true;});
    var notice=document.createElement('div');
    notice.style.cssText='position:fixed;inset:0;z-index:99999;background:#081120;display:grid;place-items:center;color:#e8f5ff;font-size:24px;text-align:center';
    notice.textContent="网页服务已关闭，可关闭此标签页。重新使用请双击启动网页。";
    document.body.appendChild(notice);
  } catch(error){button.disabled=false;button.textContent="关闭服务";alert(error.message);}
};
var commandGeneration = 0;
var stoppingOutputs = false;
var mutatingRoutes = new Set(JSON.parse('__MUTATING_ROUTES__'));
function checkCommandGeneration(g) {
 if(g !== commandGeneration || stoppingOutputs){throw Error("操作已被停止取消，请确认状态后重新启动。");}
}

async function stopOutputs(){
 commandGeneration++;
 stoppingOutputs=true;
 try {
 var began=Date.now()/1000;
 var reply=await fetchJSON("/api/motor?enable=0");
 if(!reply||!reply.ok){throw Error((reply&&reply.error)||"停止指令未确认；检查连接或现场断电。");}
 var deadline=Date.now()+3500;
 while(Date.now()<deadline){
  var status=await fetchJSON("/api/status"),m=status.motor||{};
  if(m.available&&m.updated_at>=began&&m.state===0&&m.output_on===false){renderStatus(status);return status;}
  await new Promise(function(resolve){setTimeout(resolve,250);});
 }
 throw Error("已发送停止指令，但没有取得新鲜停机反馈；检查连接，必要时现场断电。");
 } finally { stoppingOutputs=false; }
}
$("btn_header_stop").onclick=function(){var button=this;button.disabled=true;button.textContent="正在停止…";stopOutputs().then(function(){button.textContent="已确认停止";}).catch(function(e){button.textContent="停机未确认";setErr(e.message);}).finally(function(){button.disabled=false;});};

function fmt(v) { return (v === null || v === undefined) ? "-" : String(v); }

function fetchJSON(url) {
  var u=new URL(url,window.location.origin), options={cache:"no-store"};
  var generation=commandGeneration;
  var write=mutatingRoutes.has(u.pathname);
  var stop=(u.pathname==="/api/motor" && u.searchParams.get("enable")==="0") ||
           (u.pathname==="/api/control/calibrate" && u.searchParams.get("op")==="abort");
  if(write){
    if(stop){commandGeneration++;generation=commandGeneration;}
    else if(stoppingOutputs){return Promise.reject(Error("正在停止，操作未发送。"));}
    options.method="POST";
    options.headers={"X-Control-Token":controlToken,"X-Command-Epoch":String(commandEpoch)};
    options.body="";
  }
  return fetch(url,options).then(function (r) {
    var epoch=r.headers.get("X-Command-Epoch");
    if(epoch!==null){commandEpoch=Math.max(commandEpoch,Number(epoch));}
    return r.text().then(function (t) {
      var o;
      try { o = JSON.parse(t); }
      catch (e) {
        throw new Error("bad JSON from " + url + " (HTTP " + r.status + ")");
      }
      if(write&&!stop&&generation!==commandGeneration){throw Error("操作已被停止取消。");}
      return o;
    });
  });
}

function setMsg(el, text, cls) {
  if (!el) { return; }
  setNodeText(el, text || "");
  setNodeClass(el, cls || "");
}

function setErr(text) {
  var box = $("v_errbox");
  setNodeText(box, text ? ("设备提示：" + text) : "");
  setNodeClass(box, text ? "bad" : "");
}

function addDashboardEvent(level, module, message) {
  var body = $("event_rows");
  if (!body) { return; }
  if (!dashboardLogState || !dashboardLogState.started) {
    body.innerHTML = "";
    dashboardLogState = dashboardLogState || {};
    dashboardLogState.started = true;
  }
  var row = document.createElement("tr");
  var time = document.createElement("td");
  time.className = "event-time";
  time.textContent = new Date().toLocaleTimeString([], { hour12:false });
  var status = document.createElement("td");
  status.className = "event-level" + (level === "警告" ? " warn" : (level === "故障" ? " bad" : ""));
  status.textContent = level;
  var source = document.createElement("td");
  source.textContent = module;
  var detail = document.createElement("td");
  detail.textContent = message;
  row.appendChild(time); row.appendChild(status); row.appendChild(source); row.appendChild(detail);
  body.insertBefore(row, body.firstChild);
  while (body.rows.length > 5) { body.deleteRow(body.rows.length - 1); }
}

function logDashboardStatus(s) {
  var motor = s.motor || {};
  var modeLabel=$("dash_control_mode");
  if(modeLabel){setNodeText(modeLabel, motor.available ? (["正弦开环","力矩模式","速度环","位置环","电压模式","电流环","MIT 力矩"][motor.mode] || "未知模式") : "等待反馈");}
  var next = {
    connected:!!s.connected,
    version:s.version || null,
    slot:s.active_slot || null,
    output:motor.available ? !!motor.output_on : null,
    fault:motor.available ? !!motor.fault : null,
    telem:!!s.telem_enable
  };
  if (!dashboardLogState || !dashboardLogState.started) {
    addDashboardEvent("信息", "系统", "已读取当前设备状态");
    addDashboardEvent(next.connected ? "信息" : "警告", "连接", next.connected ? "设备已连接" : (s.reason || "设备未连接"));
  } else {
    var prev = dashboardLogState;
    if (next.connected !== prev.connected) {
      addDashboardEvent(next.connected ? "信息" : "警告", "连接", next.connected ? "设备已恢复连接" : "设备连接中断");
    }
    if (next.version && (next.version !== prev.version || next.slot !== prev.slot)) {
      addDashboardEvent("信息", "固件", "版本 " + next.version + " · 活动槽 " + (next.slot || "-"));
    }
    if (next.output !== null && next.output !== prev.output) {
      addDashboardEvent("信息", "电机控制", next.output ? "功率输出已开启" : "功率输出已关闭");
    }
    if (next.fault !== null && next.fault !== prev.fault) {
      addDashboardEvent(next.fault ? "故障" : "信息", "保护", next.fault ? "设备报告保护故障" : "设备保护状态已恢复");
    }
    if (next.telem !== prev.telem) {
      addDashboardEvent("信息", "遥测", next.telem ? "遥测采集已开启" : "遥测采集已关闭");
    }
  }
  next.started = true;
  dashboardLogState = next;
}

/* ---------------------------- ring buffer --------------------------- */
function pushSample(t, ia, ib, ic, vbus, brk) {
  ring.push({ t:t, ia:ia, ib:ib, ic:ic, vbus:vbus, brk:!!brk });
  if (ring.length > RING_MAX) { ring.splice(0, ring.length - RING_MAX); }
}

/* Effective sample interval of a batch, in seconds: prefer the rate the
   server actually measured; fall back to the period the operator chose
   when the rate is missing or implausible.  There is no per-sample
   timestamp on the wire, so this is what turns a batch into X positions. */
function effInterval(b) {
  var rate = (b && typeof b.rate_hz === "number") ? b.rate_hz : null;
  if (rate !== null && rate >= RATE_MIN && rate <= RATE_MAX) { return 1 / rate; }
  var ms = Number($("sel_period").value);
  if (!ms || !isFinite(ms) || ms <= 0) { ms = 10; }
  return ms / 1000;
}

/* /api/telem/batch carries EVERY sample the server has seen since our
   cursor, oldest first -- no filtering, no decimation.  X positions are
   assigned here: the batch is laid out evenly and its newest sample ends
   at "now", so the window slides smoothly.  dropped:true means the server
   had already discarded frames we never saw: the first point of this batch
   is flagged as a break so the trace is not drawn straight across a gap. */
function absorbBatch(b) {
  var rows = (b && b.samples && b.samples.length) ? b.samples : [];
  if (!rows.length) { return; }
  var n = rows.length;
  if (!paused) {
    var nowT = Date.now() / 1000;
    var dt = effInterval(b);
    var t0 = nowT - (n - 1) * dt;
    if (ring.length) {
      /* never step backwards, even if a batch is longer than the interval
         the server reported (e.g. a backlog right after connecting) */
      var prevT = ring[ring.length - 1].t;
      if (t0 <= prevT) { t0 = prevT + dt; }
    }
    var brk = !!b.dropped;
    for (var i = 0; i < n; i++) {
      var r = rows[i];
      pushSample(t0+i*dt,r[0],r[1],r[2],r[3],brk && i===0);
    }
    lastDataT = Date.now() / 1000;
  }
  var last = rows[n - 1];
  lastTelem = { ia_mA:last[0], ib_mA:last[1], ic_mA:last[2],
                vbus_dV:last[3], vbus:last[3] };
  if (last[3] && b.vbus_not_fitted !== true) { vbusFitted = true; }
  if (typeof b.rate_hz === "number") { telemRate = b.rate_hz; }
  if (typeof b.stale === "boolean") { telemStale = b.stale; }
}

/* age of the newest sample we hold, in seconds (null = nothing yet) */
function telemAge() {
  if (lastDataT !== null) {
    var a = Date.now() / 1000 - lastDataT;
    return a > 0 ? a : 0;
  }
  return lastStatusAge;
}

/* The curve poll: its own 100 ms timer, independent of /api/status. */
function absorbEncoderBatch(b){
 var key=JSON.stringify(b.encoder_calibration_key||[]);
 if(key!==encoderCalibrationKey){encoderRing=[];encoderCalibrationKey=key;}
 if(typeof b.encoder_seq==="number"){encoderCursor=b.encoder_seq;}
 if(paused){return;}
 (b.encoder_samples||[]).forEach(function(p,i){
  var prev=encoderRing.length?encoderRing[encoderRing.length-1]:null;
  var th=typeof p[2]==="number"?p[2]:NaN;
  /* brk = "这一帧要新起一段"。只在【真的断了】时置位:
       角度无效/非有限、帧间隔 > 0.25 s、主机丢段。
     ★ 2026-09-28: 原来还把 |Δ角度|>180 也算断线 —— 于是每越过一次 0/360 边界就断一次,
       画出来是一根根互不相连的竖线 (用户反馈"0->360° 时候没有连续")。
       过零是【真实运动】不是丢帧, 现在让它连线, 画成正常锯齿; 丢帧/失效仍然断线,
       不伪造连续线。 */
  encoderRing.push({t:p[1],theta:th,raw:p[3],valid:p[4],errors:p[5],
    brk:(!prev || !isFinite(th) || !isFinite(prev.theta) || p[1]-prev.t>0.25 || (b.encoder_dropped && i===0))});
 });
 if(encoderRing.length>RING_MAX){encoderRing.splice(0,encoderRing.length-RING_MAX);}
}

function pollBatch() {
  return pollOnce("batch", function () {
  var url = "/api/telem/batch";
  if (batchCursor !== null) { url += "?since=" + batchCursor; }
  if(encoderCursor!==null){url+=(url.indexOf("?")>=0?"&":"?")+"encoder_since="+encoderCursor;}
  return fetchJSON(url).then(function (b) {
    if (!b || b.ok === false) { return; }
    if (typeof b.seq === "number") { batchCursor = b.seq; }
    absorbEncoderBatch(b);
    absorbBatch(b);
  }).catch(function () {
    /* a broken link is reported once, by the /api/status poll */
  });
  });
}

/* ---------------------------- status bar ---------------------------- */
function pollStatus() {
  return pollOnce("status", function () {
  return fetchJSON("/api/status").then(function (s) {
    renderStatus(s);
    var tm = s.telem;
    if (tm) {
      /* /api/status is the newest sample only: good for the numeric label
         and as an age fallback, but the CURVES come from the batch poll. */
      lastTelem = { ia_mA:tm.ia_mA, ib_mA:tm.ib_mA, ic_mA:tm.ic_mA,
                    vbus_dV:tm.vbus_dV, vbus:tm.vbus_dV };
      if (tm.vbus_not_fitted === true) { vbusFitted = false; }
      else if (tm.vbus_dV) { vbusFitted = true; }
      if (typeof tm.stale === "boolean" && lastDataT === null) {
        telemStale = tm.stale;
      }
      lastStatusAge = (typeof tm.age_s === "number") ? tm.age_s : null;
      if (telemRate === null && typeof tm.rate_hz === "number") {
        telemRate = tm.rate_hz;   /* only until the batch poll reports one */
      }
    }
  }).catch(function (e) {
    var c = $("v_conn");
    c.textContent = "服务未响应";
    c.className = "bad";
    var online = $("dash_online");
    if (online) { online.textContent = "离线"; }
    var header = $("header_link");
    if (header) { header.className = "header-device disconnected"; }
    setErr("页面服务未响应（" + e.message + "）");
  });
  });
}
function renderStatus(s) {
  var c = $("v_conn");
  setNodeText(c, s.connected ? "设备已连接" : "未连接");
  setNodeClass(c, s.connected ? "ok" : "bad");
  var header = $("header_link");
  if (header) { setNodeClass(header, "header-device" + (s.connected ? "" : " disconnected")); }
  var online = $("dash_online");
  if (online) { setNodeText(online, s.connected ? "在线" : "离线"); }
  var reason = $("dash_reason");
  if (reason) { setNodeText(reason, s.connected ? "设备正常应答" : (s.reason || "等待设备连接")); }
  renderEncoder(s.encoder, s.motor_profile);
  var adapter = s.adapter || {};
  var adapterOpen = $("adapter_open");
  if (adapterOpen) { setNodeText(adapterOpen, adapter.open ? "适配器已打开" : "适配器未打开"); }
  var channel = $("adapter_channel");
  if (channel) { setNodeText(channel, fmt(adapter.channel || "未识别")); }
  var bitrate = $("adapter_bitrate");
  if (bitrate) {
    var rate = Number(adapter.bitrate);
    setNodeText(bitrate, isFinite(rate) && rate > 0 ? (rate >= 1000 ? (rate / 1000) + " kbit/s" : rate + " bit/s") : "-");
  }
  var deviceReason = $("device_reason");
  if (deviceReason) {
    setNodeText(deviceReason, s.connected ? "设备已响应" : (adapter.open ? "等待设备应答" : "等待适配器"));
    deviceReason.title = s.reason || "";
  }
  setNodeText($("v_ver"), fmt(s.version));
  /* 标题栏里的固件版本: 原来是写死的 0x0912, 板上早就不是这个版本了 -> 改成跟着设备读 */
  if ($("brand_version")) { setNodeText($("brand_version"), fmt(s.version)); }
  var slot = fmt(s.active_slot);
  if (s.state) { slot += " (" + s.state + ")"; }
  setNodeText($("v_slot"), slot);
  setNodeText($("v_loops"), fmt(s.foc_loops));
  var motor = s.motor || {};
  var modeLabel=$("dash_control_mode");
  if(modeLabel){setNodeText(modeLabel, motor.available ? (["正弦开环","力矩模式","速度环","位置环","电压模式","电流环","MIT 力矩"][motor.mode] || "未知模式") : "等待反馈");}
  var fault = $("dash_fault");
  if (fault) {
    setNodeText(fault, motor.available ? (motor.fault ? "保护已触发" : "未检测到保护") : "尚无状态帧");
    setNodeClass(fault, "status-value" + (motor.fault ? " bad" : ""));
  }
  var motorState = $("dash_motor_state");
  if (motorState) {
    setNodeText(motorState, !motor.available ? "状态未知" : (motor.fault ? "保护触发" : (motor.output_on ? "运行中" : "已停止")));
    setNodeClass(motorState, "state-chip" + (motor.available && motor.output_on && !motor.fault ? " running" : ""));
  }
  var step = $("dash_step");
  if (step) { setNodeText(step, motor.available && motor.step_ms ? (motor.step_ms + " ms") : "— ms"); }
  var telem = s.telem || null;
  var mp = s.motor_profile || {};
  var profileData = mp.data || {};
  var sysModel = $("sys_model");
  if (sysModel) { setNodeText(sysModel, profileData.label || mp.sel || "-"); }
  var sysVersion = $("sys_version");
  if (sysVersion) { setNodeText(sysVersion, fmt(s.version) + " / " + fmt(s.active_slot)); }
  var sysNodes = $("sys_nodes");
  if (sysNodes) { setNodeText(sysNodes, fmt(s.host_id) + " → " + fmt(s.device_id)); }
  var traffic = s.traffic || {};
  var sysTraffic = $("sys_traffic");
  if (sysTraffic) { setNodeText(sysTraffic, fmt(traffic.rx) + " / " + fmt(traffic.tx)); }
  function showCurrent(id, value) {
    var el = $(id);
    if (el) { setNodeText(el, (typeof value === "number") ? (value / 1000).toFixed(2) : "—"); }
    var phase = id === "dash_ia" ? "phase_a" : (id === "dash_ib" ? "phase_b" : "phase_c");
    var phaseEl = $(phase);
    if (phaseEl) { setNodeText(phaseEl, (typeof value === "number") ? ((value / 1000).toFixed(2) + " A") : "— A"); }
  }
  showCurrent("dash_ia", telem && telem.ia_mA);
  showCurrent("dash_ib", telem && telem.ib_mA);
  showCurrent("dash_ic", telem && telem.ic_mA);
  renderNtc(s.ntc);
  renderMotorProfile(s.motor_profile);
  setErr(s.error || null);
  var motorBtn = $("btn_motor");
  if (motorBtn) { motorBtn.disabled = !(s.adapter && s.adapter.open); }
  if (window.syncMotorStatus) { window.syncMotorStatus(s.motor); }
  renderParams(s.params || []);
  updateTelemControls(s);
  if (s.connected && !encoderParamsRequested) {
    encoderParamsRequested = true;
    fetchJSON("/api/getall").then(function (r) {
      if (r && r.ok) { renderStatus(r); }
    }).catch(function () { /* encoder panel reports unavailable calibration data */ });
  }
  if (s.connected && !telemOn && !telemManuallyDisabled &&
      !telemRequestPending && Date.now() >= telemRetryAfter) {
    telemRequest(true);
  }
  logDashboardStatus(s);
  /* ★把最新状态挂到 window 上, 给"模式运行"那个 IIFE 做启动前检查用
     (它需要 encoder/ntc/motor 才能说清"为什么不允许启动")。 */
  window.dashboardStatus = s;
}

function renderEncoder(e, profile) {
  var angle = $("encoder_angle");
  var electrical = $("encoder_electrical_angle");
  var electricalLabel = $("encoder_electrical_label");
  var basis = $("encoder_basis");
  var electricalState = $("encoder_electrical_state");
  var raw = $("encoder_raw");
  var speed = $("encoder_speed");
  var state = $("encoder_state");
  var age = $("encoder_age");
  var errors = $("encoder_errors");
  var np = ((profile || {}).data || {}).nameplate || {};
  var pp = e && Number.isInteger(e.pole_pairs) ? e.pole_pairs : null;
  var ratio = Number(np.reduction_ratio);
  if (electricalLabel) {
    setNodeText(electricalLabel, pp ? ("换相电角度（" + pp + " 极对）") : "换相电角度");
  }
  if (basis) {
    var text = pp
      ? ("电角度 = (转子机械角 − 校准偏置) × " + pp + "，按 360° 取模。")
      : "电角度 = (转子机械角 − 校准偏置) × 极对数，按 360° 取模。";
    if (isFinite(ratio) && ratio > 0) {
      text += " 编码器在转子侧；手转输出轴还要乘减速比 " + ratio + ":1。";
    }
    setNodeText(basis, text);
  }
  encoderReadable = !!(e && e.valid && !e.stale);
  if (window.syncCalibrationButton) { window.syncCalibrationButton(); }
  if (!e) {
    if (angle) { setNodeText(angle, "—"); }
    if (electrical) { setNodeText(electrical, "—"); }
    if (electricalState) { setNodeText(electricalState, "未收到角度帧"); }
    if (raw) { setNodeText(raw, "—"); }
    if (speed) { setNodeText(speed, "—"); }
    if (state) { setNodeText(state, "未收到角度帧"); setNodeClass(state, "encoder-state-bad"); }
    if (age) { setNodeText(age, "—"); }
    if (errors) { setNodeText(errors, "—"); }
    return;
  }
  if (angle) { setNodeText(angle, typeof e.angle_deg === "number" ? e.angle_deg.toFixed(2) : "—"); }
  if (electrical) {
    setNodeText(electrical, e.electrical_angle_valid && typeof e.electrical_angle_deg === "number"
      ? e.electrical_angle_deg.toFixed(2) : "—");
  }
  if (electricalState) {
    setNodeText(electricalState, e.electrical_angle_valid ? "已校准" :
      (e.electrical_angle_note || "未校准或未读取参数"));
  }
  if (raw) { setNodeText(raw, typeof e.raw === "number" ? String(e.raw) : "—"); }
  if (speed) { setNodeText(speed, typeof e.speed_rpm === "number" ? e.speed_rpm.toFixed(1) : "—"); }
  if (state) {
    setNodeText(state, e.stale ? "角度帧超时" : (e.status_text || "编码器数据无效"));
    setNodeClass(state, e.valid && !e.stale ? "encoder-state-valid" : "encoder-state-bad");
  }
  if (age) { setNodeText(age, typeof e.age_s === "number" ? e.age_s.toFixed(2) + " s" : "—"); }
  if (errors) { setNodeText(errors, typeof e.error_count === "number" ? String(e.error_count) : "—"); }
}

/* ★NTC 温度 (0xA2): 状态栏上一个数字 —— 跟着遥测流自动到, 不新开轮询接口.
   ntc == null  = 还没收到过温度帧 -> 灰色 "--.-" (不编造 0.0).
   温度只显示, 绝不参与任何控制 (页面加载/刷新照样不开功率输出). */
function renderNtc(n) {
  var el = $("v_ntc");
  if (!el) { return; }
  if (!n) {
    setNodeText(el, "--.-");
    setNodeClass(el, "k");
    el.title = "还没收到温度帧 (0xA2): 打开遥测, 或固件未上报 NTC";
    return;
  }
  /* title 里给 raw ADC 和阈值: 用手指捏 NTC 看 raw 往哪边走即可确认分压拓扑 */
  var tip = "NTC raw ADC " + fmt(n.raw) + " / 跳闸阈值 " + fmt(n.limit_c) + " °C";
  if (n.age_s !== null && n.age_s !== undefined) { tip += " / 数据 " + n.age_s + " s 前"; }
  if (n.tripped) { tip += " / 本次上电已过温跳闸"; }
  if (n.fault) { tip += " / 传感器异常(开路/短路/超范围)"; }
  el.title = tip;

  if (n.fault) {
    setNodeText(el, "NTC 异常");
    setNodeClass(el, "bad");
    return;
  }
  if (!n.valid || typeof n.temp_c !== "number") {
    setNodeText(el, "--.-");
    setNodeClass(el, "k");
    return;
  }
  /* 固件用 flags bit1 表示 >=45C; 这里再按数值兜底一次, 两者取或 */
  var over = !!n.over || (n.temp_c >= 45);
  var txt = n.temp_c.toFixed(1) + " °C / 上限 " + fmt(n.limit_c);
  if (over) {
    setNodeText(el, txt + " 过温, 已禁止启动");
    setNodeClass(el, "bad");
  } else {
    setNodeText(el, txt);
    setNodeClass(el, "ok");
  }
  if (n.tripped) { el.textContent += " 已过温跳闸"; }
}

var lastParams = [];
function setText(id,text){var el=$(id);if(el){setNodeText(el, text);}}

/* One short line about the stream state - never a log.  The rate shown here
   is the one the BATCH endpoint measured, i.e. the real stream rate. */
function updateTelemControls(s) {
  telemOn = !!s.telem_enable;
  var btn = $("btn_telem");
  setNodeText(btn, "遥测：" + (telemOn ? "开启" : "关闭"));
  setNodeClass(btn, telemOn ? "on" : "");
  var rateEl = $("dash_rate");
  if (rateEl) { setNodeText(rateEl, telemOn && telemRate !== null && telemRate !== undefined ? (telemRate.toFixed(1) + " Hz") : "—"); }
  var telemState = $("dash_telem_state");
  if (telemState) { setNodeText(telemState, telemOn ? (telemStale ? "数据已过期" : "正在采集") : "采集已关闭"); }
  var sel = $("sel_period");
  if (s.telem_period_ms) {
    var sv = String(s.telem_period_ms);
    if (sel.value === sv) {
      periodSynced = true;      /* the device is running what the page asked */
    } else if (periodSynced) {
      sel.value = sv;           /* another client changed it: follow */
    }
  }
  var txt;
  if (paused) {
    txt = "已暂停（曲线冻结）";
  } else if (telemOn) {
    txt = "正在采集";
    if (telemRate !== null && telemRate !== undefined) {
      txt += " · " + telemRate.toFixed(1) + " Hz";
    } else {
      txt += "（测量速率中）";
    }
    var age = telemAge();
    if (age !== null) {
      txt += " · 最近数据 " + age.toFixed(1) + " s 前";
      if (telemStale) { txt += "（数据过期）"; }
    }
  } else {
    txt = "遥测已关闭";
  }
  if (!vbusFitted) { txt += " · 本板未安装母线电压采样"; }
  if (s.telem_error) { txt += " · " + s.telem_error; }
  setNodeText($("tstate"), txt);
}

/* ★电机档案: 只有 7010 (页头是固定标签, 不再有下拉)。
   服务端 /api/status 的 motor_profile = {sel, default, options:[{key,label}], data:{...}}。
   固件里的档案在 foc/foc_config.h 的【电机档案】块。 */
var motorSelInit = false;
function renderMotorProfile(mp) {
  if (!mp) { return; }
  var sel = $("motor_sel");
  if (sel && !motorSelInit && mp.options) {
    sel.innerHTML = "";
    for (var i = 0; i < mp.options.length; i++) {
      var o = document.createElement("option");
      o.value = mp.options[i].key;
      setNodeText(o, mp.options[i].label);
      sel.appendChild(o);
    }
    sel.onchange = function () {
      sel.disabled = true;
      fetchJSON("/api/motor_profile?sel=" + encodeURIComponent(sel.value))
        .then(function (r) {
          sel.disabled = false;
          if (r && r.data) { renderMotorProfile({ sel: r.sel, options: mp.options, data: r.data }); }
          if (!r || !r.ok) { setMsg($("tstate"), "切换电机档案失败: " + ((r && r.error) || "?"), "bad"); }
        })
        .catch(function () { sel.disabled = false; });
    };
    motorSelInit = true;
  }
  if (sel && mp.sel && sel.value !== mp.sel) { sel.value = mp.sel; }
  var d = mp.data || {};
  var np = d.nameplate || {};
  var note = $("profile_note");
  if (note) {
    setNodeText(note, mp.sel === "SG-6010HB"
      ? "FOC调试档案：相电流过流保护阈值 5 A；电机 R/L 仍待实测辨识。"
      : "网页档案只影响铭牌对照与转速换算；固件侧参数需在对应工程内单独核实。");
  }
  var txt = (d.label || mp.sel || "?")
          + "  " + fmt(np.pole_pairs) + " 极对"
          + " · Rs " + fmt(np.rs_mohm) + " mΩ/相"
          + " · Ls " + fmt(np.ls_uh) + " µH/相"
          + " · Kt " + fmt(np.kt_nm_a_rms) + " N·m/Arms"
          + " · Ke " + fmt(np.ke_vrms_per_krpm_line) + " Vrms/krpm(线)"
          + " · 额定 " + fmt(np.rated_current_arms) + " Arms / 峰值 " + fmt(np.peak_current_a) + " A"
          + " · 默认限流 " + fmt(d.safe_cur_ma_default) + " mA / 电压天花板 " + fmt(d.safe_volt) + " V";
  setText("motor_info", txt);
}

/* ------------------------------ canvas ------------------------------ */
var cvs = $("curves");
var ctx = cvs.getContext("2d");
var CW = 0, CH = 0;
var canvasInView = true;

/* ★波形勾选: traceHidden[key]=true 表示这一路不画。
   每路一行, 行高由可见路数自动重算(见 draw()), 所以勾掉几路后剩下的会变高、更好看。 */
var traceHidden = {};

function activeTraces() {
  var base = BASE_TRACES.concat([{key:"theta",label:"编码器电角度",unit:"°",div:1,dec:1,color:"#f0c86b",minSpan:360}]);  
  var out = [];
  for (var i = 0; i < base.length; i++) {
    if (!traceHidden[base[i].key]) { out.push(base[i]); }
  }
  return out;   /* 全勾掉时返回空数组: draw() 会留空画面, 不会崩 */
}

/* 把页面上每个 checkbox[data-trace=key] 接到 traceHidden 上 */
function initTraceChecks() {
  var boxes = document.querySelectorAll("input.tchk");
  for (var i = 0; i < boxes.length; i++) {
    (function (b) {
      var key = b.getAttribute("data-trace");
      traceHidden[key] = !b.checked;
      b.onchange = function () {
        traceHidden[key] = !b.checked;
        if (typeof draw === "function") { draw(); }   /* 立刻重画, 不等下一帧 */
      };
    })(boxes[i]);
  }
}

/* ★ 2026-09-28: 波形卡片现在被拉伸填满右列(见 #chart-panel 的 CSS), 画布高度不再写死。
   这里两个要点:
     1) 盒子没变就不重设 backing store —— ResizeObserver 会反复回调, 而每写一次
        canvas.width/height 都会清空画布并触发重绘。
     2) 高度下限降到 200, 免得窗口很矮时画布把布局撑破。 */
var lastCW = 0, lastCH = 0;
function resizeCanvas() {
  var dpr = window.devicePixelRatio ? window.devicePixelRatio : 1;
  var r = cvs.getBoundingClientRect();
  var w = Math.max(320, Math.round(r.width));
  var h = Math.max(200, Math.round(r.height));
  var bw = Math.round(w * dpr), bh = Math.round(h * dpr);
  if (w === lastCW && h === lastCH && cvs.width === bw && cvs.height === bh) { return; }
  lastCW = w; lastCH = h;
  CW = w; CH = h;
  cvs.width = bw;
  cvs.height = bh;
  /* setTransform, not scale(): repeated resizes must not multiply. */
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}

function rawValue(rec, tr) { return rec[tr.key] / tr.div; }

function currentValue(tr) {
  if(tr.key==="theta"){var e=encoderRing.length ? encoderRing[encoderRing.length-1] : null;return e && (paused || Date.now()/1000-e.t<=0.25) ? e.theta : NaN;}
  if (!lastTelem || (!paused && telemAge()!==null && telemAge()>1)) { return NaN; }
  var raw = lastTelem[tr.key + "_mA"];
  if (raw === undefined || raw === null) { raw = lastTelem[tr.key]; }
  if (raw === undefined || raw === null) { return 0; }
  return raw / tr.div;
}

function traceRange(recs, tr, start, cutoff) {
  if(tr.key==="theta"){return {lo:0,hi:360};}
  var lo = 0, hi = 0, seen = false;
  for (var i = start || 0; i < recs.length; i++) {
    if (recs[i].t < cutoff) { continue; }
    var v = rawValue(recs[i], tr);
    if (!isFinite(v)) { continue; }
    if (!seen) { lo = v; hi = v; seen = true; }
    else {
      if (v < lo) { lo = v; }
      if (v > hi) { hi = v; }
    }
  }
  /* always include zero, so "is it really 0?" stays readable */
  if (lo > 0) { lo = 0; }
  if (hi < 0) { hi = 0; }
  var span = hi - lo;
  /* Never zoom in tighter than the trace's minimum span: +-13 mA of
     quantisation noise on an idle board would otherwise be stretched over
     the whole panel and look like a real waveform. */
  var minSpan = tr.minSpan || 0;
  if (span < minSpan) {
    var mid = (lo + hi) / 2;
    lo = mid - minSpan / 2;
    hi = mid + minSpan / 2;
    span = minSpan;
  }
  if (span < 1e-9) { lo -= 1; hi += 1; span = 2; }
  var pad = span * 0.10;
  return { lo: lo - pad, hi: hi + pad };
}

function drawTrace(y0, h, nowT, recs, tr, start, cutoff) {
  var x0 = 74, x1 = CW - 62;
  var plotW = x1 - x0;
  if (plotW < 40 || h < 24) { return; }

  ctx.fillStyle = C_BG;
  ctx.fillRect(0, y0, CW, h);
  ctx.strokeStyle = C_PANEL_LINE;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, y0 + 0.5);
  ctx.lineTo(CW, y0 + 0.5);
  ctx.stroke();

  var rg = traceRange(recs, tr, start, cutoff);
  var lo = rg.lo, hi = rg.hi;
  var span = hi - lo;
  if (span < 1e-9) { span = 1; }

  function vy(v) { return y0 + h - 10 - ((v - lo) / span) * Math.max(10,h-30); }
  function vx(t) {
    var x = x1 - ((nowT - t) / WINDOW_S) * plotW;
    if (x < x0) { x = x0; }
    if (x > x1) { x = x1; }
    return x;
  }

  /* horizontal grid + value labels at top and bottom */
  ctx.font = "13px Consolas, monospace";
  ctx.textBaseline = "middle";
  var d, gv, gy;
  for (d = 0; d <= 4; d++) {
    gv = lo + (span * d) / 4;
    gy = Math.round(vy(gv)) + 0.5;
    ctx.strokeStyle = C_GRID;
    ctx.beginPath();
    ctx.moveTo(x0, gy);
    ctx.lineTo(x1, gy);
    ctx.stroke();
    if (d === 0 || d === 4) {
      ctx.fillStyle = C_DIM;
      ctx.textAlign = "right";
      ctx.fillText(gv.toFixed(tr.dec), x0 - 6, gy);
    }
  }

  /* vertical grid: one line per second of the window */
  for (d = 0; d <= WINDOW_S; d++) {
    var gx = Math.round(vx(nowT - WINDOW_S + d)) + 0.5;
    if (gx < x0 || gx > x1) { continue; }
    ctx.strokeStyle = (d === WINDOW_S) ? "#2b3340" : C_GRID_T;
    ctx.beginPath();
    ctx.moveTo(gx, y0 + 1);
    ctx.lineTo(gx, y0 + h - 1);
    ctx.stroke();
  }

  /* zero line */
  var zy = Math.round(vy(0)) + 0.5;
  ctx.strokeStyle = C_ZERO;
  ctx.beginPath();
  ctx.moveTo(x0, zy);
  ctx.lineTo(x1, zy);
  ctx.stroke();

  /* the trace itself; a record flagged brk starts a NEW segment, so a gap
     the client missed (dropped batch) shows as a break, not as a line
     drawn straight across the missing stretch */
  ctx.strokeStyle = tr.color;
  ctx.lineWidth = 1.4;
  ctx.lineJoin = "round";
  var started = false;
  ctx.beginPath();
  var i, v, px, py;
  for (i = start || 0; i < recs.length; i++) {
    if (recs[i].t < cutoff) { continue; }
    v = rawValue(recs[i], tr);
    if (!isFinite(v)) { started=false; continue; }
    px = vx(recs[i].t);
    py = vy(v);
    if (!started || recs[i].brk) { ctx.moveTo(px, py); started = true; }
    else { ctx.lineTo(px, py); }
  }
  if (started) {
    ctx.stroke();
  }

  /* label + current numeric value */
  ctx.font = "14px Consolas, monospace";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillStyle = tr.color;
  ctx.fillText(tr.label + "  " + (isFinite(currentValue(tr)) ? currentValue(tr).toFixed(tr.dec) : "—") + " " + tr.unit,
               x0 + 8, y0 + 6);

  if (paused) {
    ctx.fillStyle = C_DIM;
    ctx.textAlign = "right";
    ctx.fillText("paused", x1 - 8, y0 + 6);
  }
}

/* Right edge of the window.  Live: the wall clock, so the newest sample
   (which was placed at "now" when its batch arrived) slides left smoothly
   at full frame rate instead of stepping once per 100 ms poll.  Paused: the
   newest sample itself, so a frozen curve stays on screen. */
function rightEdge() {
  var wall = Date.now() / 1000;
  if (paused) {
    return Math.max(ring.length ? ring[ring.length - 1].t : 0,encoderRing.length?encoderRing[encoderRing.length-1].t:0)||wall;
  }
  var newest = Math.max(ring.length ? ring[ring.length - 1].t : 0,encoderRing.length?encoderRing[encoderRing.length-1].t:0);
  return newest > wall ? newest : wall;
}

function draw() {
  if (!ctx || CW <= 0 || CH <= 0) { return; }
  ctx.fillStyle = C_BG;
  ctx.fillRect(0, 0, CW, CH);

  var nowT = rightEdge();
  var cutoff = nowT - WINDOW_S;
  /* Current timestamps are monotonic (absorbBatch enforces that). Search the
     visible suffix without allocating a new array every animation frame. */
  var lo = 0, hi = ring.length;
  while (lo < hi) {
    var mid = (lo + hi) >>> 1;
    if (ring[mid].t < cutoff) { lo = mid + 1; } else { hi = mid; }
  }
  var currentStart = lo, encoderStart = 0;
  /* Encoder wall-clock timestamps can jump; retain the original time filter. */
  while (encoderStart < encoderRing.length && encoderRing[encoderStart].t < cutoff) { encoderStart++; }
  var traces = activeTraces();
  var n = traces.length;
  var pad = 4;
  var h = (CH - pad * (n + 1)) / n;
  for (var k = 0; k < n; k++) {
    var encoderTrace = traces[k].key === "theta";
    drawTrace(pad + k * (h + pad), h, nowT, encoderTrace ? encoderRing : ring, traces[k],
              encoderTrace ? encoderStart : currentStart, cutoff);
  }
  if (!lastTelem) {
    ctx.fillStyle = C_DIM;
    ctx.font = "14px Consolas, monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    ctx.fillText("等待有效电流遥测；电角度需先完成开环校准", CW / 2, CH - 6);
  }

}

function frame() {
  if (!document.hidden && canvasInView) { draw(); }
  window.requestAnimationFrame(frame);
}

/* --------------------------- param table ---------------------------- */
function renderParams(params) {
  /* Update the table IN PLACE.  Rebuilding it would destroy the focused
     input (and the cursor position) while the operator is typing. */
  lastParams = params || [];
  var tb = $("ptable");
  if (!tb) { return; }
  if (tb.childNodes.length !== params.length) {
    tb.innerHTML = "";
    params.forEach(function (p) { tb.appendChild(buildRow(p)); });
  }
  params.forEach(function (p) {
    var tr = $("r" + p.id);
    if (!tr) { return; }
    setNodeText(tr.cells[1], p.name);
    setNodeClass(tr.cells[1], (p.known === false) ? "warn" : "");
    var inp = $("v" + p.id);
    if (inp && !inp.dataset.editing && document.activeElement !== inp) {
      inp.value = (p.value === null ? p.default : p.value);
    }
    setNodeText(tr.cells[5], stateText(p));
    setNodeClass(tr.cells[5], stateClass(p));
  });
}

function stateText(p) {
  if (p.known === false) { return "尚未读取（显示默认值）"; }
  if (p.saved) { return "已保存"; }
  if (p.is_default) { return "默认值"; }
  return "仅在 RAM";
}

function stateClass(p) {
  if (p.known === false) { return "warn"; }
  if (p.saved) { return "ok"; }
  if (p.is_default) { return ""; }
  return "warn";
}

function buildRow(p) {
  var tr = document.createElement("tr");
  tr.id = "r" + p.id;
  var td;

  td = document.createElement("td"); td.textContent = p.id; tr.appendChild(td);
  td = document.createElement("td"); td.textContent = p.name; tr.appendChild(td);

  td = document.createElement("td");
  var inp = document.createElement("input");
  inp.type = "number"; inp.id = "v" + p.id; inp.min = p.min; inp.max = p.max;
  inp.value = (p.value === null ? p.default : p.value);
  inp.addEventListener("focus", function () { inp.dataset.editing = "1"; });
  inp.addEventListener("blur", function () { delete inp.dataset.editing; });
  inp.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") { writeOne(p); }
  });
  td.appendChild(inp);
  tr.appendChild(td);

  td = document.createElement("td"); td.textContent = fmt(p.unit); tr.appendChild(td);
  td = document.createElement("td"); td.textContent = p.min + ".." + p.max; tr.appendChild(td);
  td = document.createElement("td"); td.textContent = stateText(p);
  td.className = stateClass(p); tr.appendChild(td);

  td = document.createElement("td");
  var btn = document.createElement("button");
  btn.textContent = "Write";
  btn.onclick = function () { writeOne(p); };
  td.appendChild(btn);
  tr.appendChild(td);
  return tr;
}

function writeOne(p) {
  var inp = $("v" + p.id);
  var raw = inp.value.trim();
  if (raw === "" || isNaN(Number(raw))) {
    setMsg($("pmsg"), "parameter " + p.name + ": enter a number", "bad");
    return;
  }
  var val = Math.trunc(Number(raw));
  if (val < p.min || val > p.max) {
    setMsg($("pmsg"), p.name + " must be " + p.min + ".." + p.max, "bad");
    return;
  }
  setMsg($("pmsg"), "writing " + p.name + " = " + val + " ...", "warn");
  fetchJSON("/api/set?id=" + encodeURIComponent(p.id) + "&value=" + encodeURIComponent(val))
    .then(function (r) {
      if (r && r.ok) {
        setMsg($("pmsg"), p.name + " written (device value " + r.value +
               ") - press Save to flash to keep it", "ok");
      } else {
        var why = (r && r.error) || "unknown error";
        setMsg($("pmsg"), "write failed: " + why, "bad");
        setErr(why);
      }
    })
    .catch(function (e) {
      setMsg($("pmsg"), "write failed: " + e.message, "bad");
      setErr("write failed: " + e.message);
    });
}

/* ------------------------- telemetry control ------------------------ */
function telemRequest(enable) {
  if (telemRequestPending) { return; }
  telemRequestPending = true;
  var period = Number($("sel_period").value) || 10;
  var btn = $("btn_telem");
  btn.disabled = true;
  setMsg($("tstate"), enable ? "正在开启遥测…" : "正在关闭遥测…", "warn");
  fetchJSON("/api/telem?enable=" + (enable ? "1" : "0") + "&period_ms=" + period)
    .then(function (r) {
      if (r && r.ok) {
        telemOn = !!r.enable;
        if (!telemOn) { telemRate = null; lastTelem = null; }
        setMsg($("tstate"), "遥测" + (telemOn ? "已开启" : "已关闭"),
               telemOn ? "ok" : "");
      } else {
        telemOn = false;
        var why = (r && r.error) || "未知错误";
        setMsg($("tstate"), "遥测请求失败：" + why, "bad");
        setErr(why);
      }
      var b = $("btn_telem");
      b.textContent = "遥测：" + (telemOn ? "开启" : "关闭");
      b.className = telemOn ? "on" : "";
    })
    .catch(function (e) {
      setMsg($("tstate"), "遥测请求失败：" + e.message, "bad");
    })
    .then(function () {
      telemRequestPending = false;
      telemRetryAfter = telemOn ? 0 : Date.now() + 3000;
      btn.disabled = false;
    });
}

$("btn_telem").onclick = function () {
  var enable = !telemOn;
  telemManuallyDisabled = !enable;
  telemRequest(enable);
};

$("sel_period").onchange = function () {
  /* A period change while streaming is applied immediately. */
  if (telemOn) { telemRequest(true); }
};

$("btn_pause").onclick = function () {
  paused = !paused;
  var b = $("btn_pause");
  b.textContent = paused ? "继续" : "暂停";
  b.className = paused ? "on" : "";
  if (!paused) {
    /* restart the window: do not splice the frozen history into live data */
    ring = [];
    lastTelem = null;
  }
  draw();
};

$("btn_clear").onclick = function () {
  encoderRing=[];
  ring = [];
  draw();
};

$("btn_reconnect").onclick = function () {
  var b = $("btn_reconnect");
  b.disabled = true;
  setMsg($("tstate"), "正在重新打开 CAN 适配器…", "warn");
  fetchJSON("/api/reconnect").then(function (r) {
    if (r && r.ok) {
      setMsg($("tstate"), "适配器已打开：" + (r.channel || "?") + "，正在重新读取…", "ok");
    } else {
      /* 适配器不在 USB 上: 插回来即可, 服务端每 3 秒自动重连一次 */
      setMsg($("tstate"), "未找到 CAN 适配器：" + ((r && r.error) || "未识别")
             + "。插好 USB-CAN 后会自动重连。", "bad");
    }
  }).catch(function () {
    setMsg($("tstate"), "重连请求失败（页面服务未响应）", "bad");
  }).then(function () {
    return pollStatus();
  }).then(function () { b.disabled = false; });
};

/* ------------------------ parameter buttons ------------------------- */
$("btn_refresh").onclick = function () {
  var b = $("btn_refresh");
  b.disabled = true;
  setMsg($("pmsg"), "reading all parameters ...", "warn");
  fetchJSON("/api/getall").then(function (r) {
    if (r && r.params) {
      renderStatus(r);
      setMsg($("pmsg"), "parameters refreshed", "ok");
    } else {
      var why = (r && r.error) || "unknown error";
      setMsg($("pmsg"), "refresh failed: " + why, "bad");
      setErr(why);
    }
  }).catch(function (e) {
    setMsg($("pmsg"), "refresh failed: " + e.message, "bad");
  }).then(function () { b.disabled = false; });
};

$("btn_save").onclick = function () {
  var b = $("btn_save");
  b.disabled = true;
  setMsg($("pmsg"), "saving to flash ...", "warn");
  fetchJSON("/api/save").then(function (r) {
    if (r && r.ok) {
      setMsg($("pmsg"), "saved to flash", "ok");
    } else {
      var why = (r && r.error) || "unknown error";
      setMsg($("pmsg"), "save failed: " + why, "bad");
      setErr(why);
    }
  }).catch(function (e) {
    setMsg($("pmsg"), "save failed: " + e.message, "bad");
  }).then(function () { b.disabled = false; });
};

/* The table is read only when the operator opens the section. */
$("params_box").addEventListener("toggle", function () {
  if ($("params_box").open) { $("btn_refresh").onclick(); }
});


function downloadText(name,text,type){var url=URL.createObjectURL(new Blob([text],{type:type}));var a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(function(){URL.revokeObjectURL(url);},1000);}
$("btn_export_waves").onclick=function(){
 var rows=ring.map(function(p){return {t:p.t,s:[p.t,p.ia/1000,p.ib/1000,p.ic/1000,"","current",p.brk?1:0,"","",""]};}).concat(encoderRing.map(function(p){return {t:p.t,s:[p.t,"","","",isFinite(p.theta)?p.theta:"","encoder",p.brk?1:0,p.raw===null?"":p.raw,p.valid,p.errors]};}));
 rows.sort(function(a,b){return a.t-b.t;});
 downloadText("7010_wave_"+Date.now()+".csv","host_time_s,ia_A,ib_A,ic_A,encoder_electrical_deg,source,break,encoder_raw,encoder_valid,encoder_errors\n"+rows.map(function(p){return p.s.join(",");}).join("\n"),"text/csv;charset=utf-8");
};
$("btn_export_status").onclick=function(){fetchJSON("/api/status").then(function(s){downloadText("7010_status_"+Date.now()+".json",JSON.stringify(s,null,2),"application/json");}).catch(function(e){setErr(e.message);});};

/* ------------------------------- startup ---------------------------- */
resizeCanvas();
window.addEventListener("resize", resizeCanvas);
/* ★波形卡片的盒子会因左列内容变化(切换模式、展开/收起调参面板、校准卡片长高)而改变,
   这时窗口尺寸并没变 —— 只监听 window resize 会让画布被拉伸变形。所以再跟一个
   ResizeObserver 盯住画布自己的盒子。resizeCanvas 内部有"尺寸未变即返回"的短路, 不会打转。 */
if (typeof ResizeObserver === "function") {
  try { new ResizeObserver(function () { resizeCanvas(); }).observe(cvs); } catch (e) { /* 老浏览器忽略 */ }
}
window.requestAnimationFrame(frame);

if (typeof IntersectionObserver === "function") {
  new IntersectionObserver(function (entries) {
    canvasInView = entries[0].isIntersecting;
  }).observe(cvs);
}

/* The page enables the stream on load (10 ms = 100 Hz by default, as
   selected in the period box).  If the device does not answer, the UI still
   renders and says so in the single status / error line. */
function tick() {
  pollStatus().then(function () {
    if (firstStatus) {
      firstStatus = false;
      if (!telemOn) { telemRequest(true); }
    }
  });
}
tick();
setInterval(tick, POLL_MS);

/* Curves: their own faster timer, pulling every sample the server holds. */
pollBatch();
setInterval(pollBatch, BATCH_MS);
/* ======================================================================
 * ★ 输出控制: 正弦开环 开/关 + 目标电压(键盘直接输入, 自动换算成占空比)
 *   占空比 = 目标电压 / 母线电压 * 1000, 固件硬上限 30%
 * ====================================================================== */
(function () {
  var btn = document.getElementById("btn_motor");
  if (!btn) { return; }
  var motorOn = false;
  var motorKnown = false;
  var motorPending = false;
  var motorFault = false;
  var motorCommandAt = 0;

  function num(id, def) {
    var e = document.getElementById(id);
    var v = e ? parseFloat(e.value) : NaN;
    return isFinite(v) ? v : def;
  }

  function label() {
    if (motorFault) {
      btn.textContent = "输出: FAULT (点此停机复位)";
      btn.className = "bad";
    } else if (!motorKnown) {
      btn.textContent = "输出: 状态未知 (点此发送停机)";
      btn.className = "warn";
    } else if (motorPending) {
      btn.textContent = motorOn ? "输出: ON? (点此停机)" : "输出: OFF?";
      btn.className = "warn";
    } else {
      btn.textContent = "输出: " + (motorOn ? "ON" : "OFF");
      btn.className = motorOn ? "on" : "";
    }
  }

  window.syncMotorStatus = function (status) {
    if (!status || !status.available || !status.updated_at ||
        (status.updated_at * 1000) < motorCommandAt) { return; }
    motorKnown = true;
    motorPending = false;
    motorOn = !!status.output_on;
    motorFault = !!status.fault;
    label();
    btn.title = "电机状态: " + (status.state_name || "unknown") +
                ", step " + status.step + ", duty " + status.duty_permille + "‰";
  };

  btn.onclick = function () {
    /* Unknown/fault/pending-on always sends STOP first; never guess-start. */
    var enable = (motorFault || !motorKnown || (motorPending && motorOn)) ? 0 : (motorOn ? 0 : 1);
    var v = num("in_volt", 1.0);
    var vb = num("in_vbus", 48);
    var sm = num("in_stepms", 500);
    if (enable && (!isFinite(v) || v < 0.1 || v > 5)) {
      setErr("目标电压须在 0.1..5 V；未发送启动命令。");
      $("in_volt").focus();
      return;
    }
    if (enable && (!isFinite(vb) || vb < 1 || vb > 60)) {
      setErr("母线电压须在 1..60 V；未发送启动命令。");
      $("in_vbus").focus();
      return;
    }
    if (enable && (!isFinite(sm) || sm < 20 || sm > 5000)) {
      setErr("换向步长须在 20..5000 ms；未发送启动命令。");
      $("in_stepms").focus();
      return;
    }
    var previousOn = motorOn;
    var previousKnown = motorKnown;
    motorOn = !!enable;
    motorKnown = true;
    motorPending = true;
    motorFault = false;
    motorCommandAt = Date.now();
    label();
    var url = "/api/motor?enable=" + (enable ? 1 : 0) +
              "&volt=" + encodeURIComponent(v) +
              "&vbus=" + encodeURIComponent(vb) +
              "&step_ms=" + encodeURIComponent(sm);
    fetchJSON(url).then(function (r) {
      if (r && r.ok) {
        motorOn = !!r.enable;
        motorKnown = true;
        motorPending = false;
        motorFault = false;
        setErr("");
      } else {
        setErr((r && r.error) || "motor command failed");
        var why = (r && r.error) || "";
        if (why.indexOf("no reply") >= 0) {
          motorOn = enable ? true : previousOn;
          motorKnown = enable ? true : previousKnown;
          motorPending = true;
        } else {
          motorOn = previousOn;
          motorKnown = previousKnown || !enable;
          motorPending = false;
        }
      }
      label();
    }).catch(function () {
      setErr("motor request failed");
      motorOn = enable ? true : previousOn;
      motorKnown = enable ? true : previousKnown;
      motorPending = true;
      label();
    });
  };

  label();
})();

/* MOTOR_UI_FIX: ??????????
   ??????? motorKnown/motorPending ??????????(???? enable=0),
   ??????(??????)?????: ????? /api/status ??? output_on,
   ???????; ?????? ??/??/????? */
(function () {
  var old = document.getElementById("btn_motor");
  if (!old || !old.parentNode) { return; }
  var fresh = old.cloneNode(true);          /* cloneNode ????? */
  old.parentNode.replaceChild(fresh, old);

  var PREFIX = "\u8f93\u51fa: ";            /* ??:  */
  var TRIP   = PREFIX + "\u8fc7\u6d41\u8df3\u95f8";   /* ???? */
  var WAIT   = PREFIX + "\u8bfb\u53d6\u4e2d";         /* ??? */

  var on = false, fault = false, known = false;

  function num(id, def) {
    var e = document.getElementById(id);
    var v = e ? parseFloat(e.value) : NaN;
    return isFinite(v) ? v : def;
  }
  function label() {
    fresh.textContent = fault ? TRIP : (PREFIX + (on ? "ON" : "OFF"));
    fresh.className = (on && !fault) ? "on" : "";
    fresh.disabled = false;
  }
  function sync(s) {
    var m = (s && s.motor) || {};
    on = !!m.output_on;
    fault = !!m.fault;
    known = true;
    label();
  }
  function refresh() {
    if (typeof fetchJSON !== "function") { return; }
    fetchJSON("/api/status").then(sync).catch(function () {});
  }

  fresh.disabled = false;
  fresh.textContent = WAIT;

  fresh.onclick = function () {
    var generation=commandGeneration;
    fresh.disabled = true;
    fetchJSON("/api/status").then(function (s) {
      checkCommandGeneration(generation);
      var m = (s && s.motor) || {};
      var enable = m.output_on ? 0 : 1;                 /* ??? = ?? */
      var v  = num("in_volt", 1.0);
      var vb = num("in_vbus", 48);
      var sm = num("in_stepms", 500);
      if (!(vb > 0)) { vb = 48; }
      var md = 0;
      var sm = document.getElementById("sel_mode");
      if (sm) {
        md = parseInt(sm.value, 10);
        if (!isFinite(md)) { md = 0; }
      } else {
        var sd = document.getElementById("sel_dir");
        if (sd) { md = parseInt(sd.value, 10) || 0; }
      }
      /* Legacy button uses the same finite test duration as the main panel. */
      var cur = num("in_cur", 0);
      var url = "/api/motor?enable=" + enable + "&mode=" + md + "&seconds=5" +
                "&cur_ma=" + encodeURIComponent(cur) +
                "&volt=" + encodeURIComponent(v) +
                "&vbus=" + encodeURIComponent(vb) +
                "&step_ms=" + encodeURIComponent(sm);
      return fetchJSON(url).then(function (r) {
        if (typeof setErr === "function") {
          setErr((r && r.ok) ? "" : ((r && r.error) || "motor cmd failed"));
        }
      });
    }).catch(function () {
      if (typeof setErr === "function") { setErr("motor request failed"); }
    }).then(function () {
      fresh.disabled = false;
      refresh();
    });
  };

  refresh();
  setInterval(refresh, 700);
})();

/* RECON_UI: ★ 显眼的"重连 CAN"按钮 (顶部状态栏 + 转速面板各一个)。
   适配器拔插/掉线后点一下就恢复。服务端本身也每 3 秒自动重连一次,
   所以就算不点、只要 USB 插好, 链路也会自己回来。 */
(function () {
  var btns = [];
  ["btn_reconnect_top", "btn_reconnect_inline", "btn_rpm_recon"].forEach(function (id) {
    var b = document.getElementById(id);
    if (b) { btns.push(b); }
  });
  if (!btns.length || typeof fetchJSON !== "function") { return; }

  function say(text, cls) {
    var t = document.getElementById("tstate");
    if (t && typeof setMsg === "function") { setMsg(t, text, cls); }
  }
  function hint(text, bad) {
    var h = document.getElementById("rpm_hint");
    if (h) { h.textContent = text; h.className = bad ? "bad" : "k"; }
  }

  function go() {
    btns.forEach(function (b) { b.disabled = true; });
    say("正在重连 USB-CAN ...", "warn");
    hint("正在重连 USB-CAN ...", false);
    fetchJSON("/api/reconnect").then(function (r) {
      if (r && r.ok) {
        say("适配器已打开: " + (r.channel || "?") + "，正在读取设备 ...", "ok");
        hint("适配器已连上 " + (r.channel || "?") + "，可以点启动了", false);
      } else {
        var why = (r && r.error) || "未找到适配器";
        say("没有 CAN 适配器: " + why, "bad");
        hint("没找到 USB-CAN 适配器，请插好（插好后约 3 秒自动连上）", true);
      }
    }).catch(function () {
      say("重连请求失败（网页服务端没响应）", "bad");
      hint("重连请求失败", true);
    }).then(function () {
      btns.forEach(function (b) { b.disabled = false; });
    });
  }

  btns.forEach(function (b) { b.onclick = go; });
})();

/* ★波形勾选: 初始化勾选框 + 几个常用预设按钮 */
(function () {
  if (typeof initTraceChecks !== "function") { return; }
  initTraceChecks();

  function setAll(keys) {
    var boxes = document.querySelectorAll("input.tchk");
    for (var i = 0; i < boxes.length; i++) {
      var k = boxes[i].getAttribute("data-trace");
      boxes[i].checked = (keys === null) ? true : (keys.indexOf(k) >= 0);
      traceHidden[k] = !boxes[i].checked;
    }
    if (typeof draw === "function") { draw(); }
  }
  function on(id, keys) {
    var b = document.getElementById(id);
    if (b) { b.onclick = function () { setAll(keys); }; }
  }
  on("btn_chk_all", null);                                  /* 全选 */
  on("btn_chk_none", []);                                   /* 全不选 */
  on("btn_chk_cur", ["ia", "ib", "ic"]);                    /* 只看电流 */
})();

(function () {
  var links = document.querySelectorAll(".side-nav a");
  for (var i = 0; i < links.length; i++) {
    links[i].addEventListener("click", function () {
      for (var j = 0; j < links.length; j++) { links[j].classList.remove("active"); }
      this.classList.add("active");
    });
  }
})();

/* Encoder FOC modes, calibration, and explicit one-key CAN-OTA controls. */
(function () {
  var mode = null;
  var modeNames = {0:"正弦开环", 1:"力矩", 2:"速度环", 3:"位置环", 4:"电压模式", 5:"电流环", 6:"MIT 力矩"};
  var fields = {
    1:["torque_nm"], 2:["speed_rpm"], 3:["position_deg"],
    4:["voltage_d_v", "voltage_v"], 5:["current_a"],
    6:["mit_position_rad", "mit_speed_rad_s", "mit_kp", "mit_kd", "mit_torque_nm"]
  };
  var inputs = {
    torque_nm:"target_torque_nm", speed_rpm:"target_speed_rpm",
    position_deg:"target_position_deg", voltage_v:"target_voltage_v", voltage_d_v:"target_voltage_d_v",
    current_a:"target_current_a", mit_position_rad:"target_mit_position_rad",
    mit_speed_rad_s:"target_mit_speed_rad_s", mit_kp:"target_mit_kp",
    mit_kd:"target_mit_kd", mit_torque_nm:"target_mit_torque_nm"
  };
  function gearboxRatio() {
    var p = (((window.dashboardStatus || {}).motor_profile || {}).data || {}).nameplate || {};
    var r = Number(p.reduction_ratio);
    return isFinite(r) && r > 0 ? r : 9.67;
  }
  function rotorToOutputDeg(rotorDeg) { return rotorDeg / gearboxRatio(); }
  function positionTargetFromFeedback(rotorDeg) {
    var output = rotorToOutputDeg(rotorDeg);
    var quantization = 4 * 360 / (16384 * gearboxRatio());
    if (output < 0 && output >= -quantization) { return 0; }
    if (output > 360 && output <= 360 + quantization) { return 360; }
    return output;
  }
  /* ★阶段编号与 foc/foc_mode.h 的 FOC_CALIB_* 一一对应
     (5..8 是按参考工程 LiFoc 重写校准后新增的中间阶段) */
  var stageNames = {0:"空闲", 1:"转子对齐", 2:"慢速扫角(旧)", 3:"已完成", 4:"失败",
                    5:"测方向/极对数", 6:"正向扫角采样", 7:"反向扫角采样", 8:"计算并保存"};
  var calibRunning = function (s) { return s === 1 || s === 2 || s === 5 || s === 6 || s === 7 || s === 8; };
  var calibrationErrors = {
    1:"校准电流超过设备相电流保护限值", 2:"转子未跟随校准磁场，编码器位移不足",
    3:"转子跟随量与设定极对数不符", 4:"校准参数写入失败", 5:"校准已中止", 6:"编码器读取中断",
    7:"校准期间没有电流 —— 检查母线是否上电、三相功率线是否接好、驱动器是否使能",
    8:"转子跟随量过小（只走了一小段）—— 校准转矩不足 / 机械卡住 / 联轴器打滑",
    9:"转子跟随量过大（走过了一整圈）—— 实际极对数可能比铭牌小",
    10:"全圈扫角未稳定跟随，零点样本分散；检查机械负载和校准电流",
    11:"旧固件启动助跑超时（当前固件使用编码器直接换相）"
  };
  var runBusy = false;
  var calibrationDone = false;
  var calibrationActive = false;
  /* ★校准卡片自己的显示状态 (由 /api/control/status 驱动, 与运行模式无关) */
  var calibStage = 0;
  var calibError = 0;
  var calibProgress = 0;

  function note(id, message, bad) {
    var e = $(id);
    if (!e) { return; }
    e.textContent = message || "";
    e.className = bad ? "bad" : "loop-reserve-note";
  }
  function request(url) {
    return fetchJSON(url).then(function (r) {
      if (!r || !r.ok) { throw new Error((r && r.error) || "设备拒绝请求"); }
      return r;
    });
  }
  var tuneDefs = [
    [7,"电流上限 (A)",1000,"1,2,3,4,5,6"],
    [12,"电压上限 (V)",1000,"1,2,3,4,5,6"],
    [13,"死区补偿 (ns，0=关)",1,"1,2,3,4,5,6"],
    [16,"电流带宽 (rad/s，0=手动)",1,"1,2,3,5,6"],
    [3,"手动电流 Kp (V/A)",100,"1,2,3,5,6"],
    [4,"手动电流 Ki (V/A/s)",100,"1,2,3,5,6"],
    [17,"等效每相 R (mΩ)",1,"1,2,3,5,6"],
    [18,"每相 L (µH，待测)",1,"1,2,3,5,6"],
    [22,"电流滤波时间 (µs)",1,"1,2,3,5,6"],
    [5,"速度 Kp (A/(rev/s))",100,"2,3"],
    [6,"速度 Ki (A/rev)",100,"2,3"],
    [19,"速度 Kd (A·s²/rev)",1000,"2,3"],
    [11,"速度上限 (rpm)",1,"2,3"],
    [14,"速度斜坡 (rpm/s)",1,"2,3"],
    [15,"速度滤波时间 (ms)",1,"2,3,6"],
    [8,"位置 Kp (1/s)",100,"3"],
    [9,"位置 Ki (1/s²)",100,"3"],
    [10,"位置 Kd",1000,"3"],
    [20,"电压斜坡 (V/s)",1000,"4"],
    [21,"力矩上限 (N·m)",1000,"1,6"]
  ];
  var tuneCatalog = __TUNE_CATALOG__;
  var feedback = {};
  function relevant(def) { return def[3].split(",").indexOf(String(mode)) >= 0; }
  var noteEl = $("selected_mode_note");
  var firstView = true;
  var modeDesc = {
    0:"正弦开环：旋转电压驱动，不需要编码器，也不需要先校准。",
    1:"力矩模式：估算转子 N·m 按产品册 Kt=0.0606 Nm/A 换算成 Iq；实际力矩口径尚待验证。",
    2:"速度环：编码器测速 -> Iq，外层 100 Hz；需要已完成的编码器校准。",
    3:"位置环：输出轴相对本次上电零点，按9.67换算；位置→速度→Iq。",
    4:"电压模式：编码器定向的 Ud/Uq 电压输出（不是电压反馈环）。",
    5:"电流环：编码器定向的 Iq 电流 PI，10 kHz 内环。",
    6:"MIT 力矩：位置/速度误差 + Kp/Kd + 前馈力矩合成 Iq。"
  };
  /* Firmware stage DONE and corrected persistent calibration must both be valid. */
  function calibrationUsable() {
    var e = (window.dashboardStatus || {}).encoder || {};
    return e.electrical_calibrated === true;
  }
  function syncCalibHint() {
    var gate = $("calib_gate");
    if (!gate) { return; }
    var needCalib = mode !== null && mode !== 0;
    if (needCalib && !calibrationUsable()) {
      gate.hidden = false;
      gate.textContent = "提示：该模式使用编码器角度，但当前没有有效的校准结果。"
        + "可以先去做上面的「① 编码器校准」；也可以直接点启动 —— 由固件按自己的状态决定是否接受（会返回具体原因）。";
    } else {
      gate.hidden = true;
      gate.textContent = "";
    }
  }
  function setCalibBadge() {
    var badge = $("calib_badge");
    if (!badge) { return; }
    var cls = "calib-badge", txt = "等待校准";
    if (calibrationActive) {
      cls += " busy"; txt = "校准中";
    } else if (calibrationDone) {
      cls += " done"; txt = "校准有效";
    } else if (calibStage === 4) {
      cls += " failed";
      txt = calibrationUsable() ? "本次失败 · 沿用上次校准"
                                : (calibrationErrors[calibError] || ("校准失败 " + calibError));
    } else if (!encoderReadable) {
      txt = "等待磁编数据";
    } else {
      txt = "未校准";
    }
    badge.className = cls;
    badge.textContent = txt;
    badge.title = "固件标定阶段：" + (stageNames[calibStage] || "未知") + "，进度 " + calibProgress + "%";
  }
  function updateTuning(values) {
    (values || []).forEach(function (p) {
      var el = $("tune_"+p.id);
      var d = tuneDefs.find(function(x) { return x[0] === p.id; });
      if (el && d && p.known && !el.dataset.dirty && document.activeElement !== el) {
        el.value = p.value / d[2];   /* ★ 2026-09-28: 原来这里把 id=7 夹到 min(value,3000),
                                        于是设备即使存着 8000, 页面也只显示 3 —— "应用参数"
                                        再把这个 3 写回去, OCP 就掉回 3000。夹子已去掉。 */
      }
    });
    var bwEl = $("tune_16");
    if (!bwEl) { return; }
    var bw = Number(bwEl.value);
    if (bw > 0) {
      var kp = Number($("tune_18").value)*1e-6*bw;
      var ki = Number($("tune_17").value)*1e-3*bw;
      $("tune_summary").textContent = " 自动电流 PI：Kp="+kp.toFixed(4)+"，Ki="+ki.toFixed(3)+"（约 "+(bw/(2*Math.PI)).toFixed(1)+" Hz）";
    } else { $("tune_summary").textContent = " 手动电流 PI"; }
    $("tune_3").disabled = bw > 0; $("tune_4").disabled = bw > 0;
    [3,4].forEach(function(id){var d=tuneDefs.find(function(x){return x[0]===id;});$("tune_"+id).parentElement.hidden=bw>0||!relevant(d);});
    if(bw===0 && relevant(tuneDefs.find(function(x){return x[0]===3;})||[null,null,null,""])){$("tune_advanced").open=true;}
    $("tune_advanced").hidden=!tuneDefs.some(function(d){return relevant(d)&&[3,4,13,14,15,16,17,18,22].indexOf(d[0])>=0;});
  }
  tuneDefs.forEach(function(d) {
    var p = tuneCatalog.find(function(p) { return p.id === d[0]; });
    var label = document.createElement("label"); label.dataset.tuneModes = d[3];
    label.textContent = d[1];
    var input = document.createElement("input"); input.type="number"; input.id="tune_"+d[0];
    input.min=p.min/d[2]; input.max=p.max/d[2]; input.step=1/d[2]; input.value=p.default/d[2];   /* id=7 不再特判 3000 */
    input.addEventListener("input", function() { input.dataset.dirty="1"; updateTuning([]); });
    /* ★分两区: "通用项"(电流环 PI/带宽、电机 R·L、斜坡、滤波) 收进折叠的「高级」区;
       主区只留"本模式真正会用到的"参数 —— 例如位置环只给限幅 + 速度增益 + 位置增益。
       [19]速度 Kd 和 [20]电压斜坡 是模式自己的旋钮, 留在主区。 */
    label.appendChild(input); var advanced=[3,4,13,14,15,16,17,18,22].indexOf(d[0])>=0; $(advanced ? "tune_advanced_fields" : "tune_fields").appendChild(label);
  });
  window.updateModeTuning = updateTuning;
  /* 给视图切换用的最小接口 (只改显示, 不发 CAN) */
  window.selectControlMode = function (id) { chooseMode(id); };
  window.dashboardMode = null;
  function applyTuning(onlyDirty) {
    var writes = tuneDefs.filter(relevant).filter(function(d) {
      return !onlyDirty || !!$("tune_"+d[0]).dataset.dirty;
    }).filter(function(d) {
      return !((d[0]===3 || d[0]===4) && Number($("tune_16").value)>0);
    }).map(function(d) {
      var el=$("tune_"+d[0]), raw=Math.round(Number(el.value)*d[2]);
      var p=tuneCatalog.find(function(p){return p.id===d[0];});
      if (el.value==="" || !isFinite(raw) || raw<p.min || raw>p.max) { throw new Error(d[1]+"超出范围"); }
      return [d[0],raw];
    });
    return writes.reduce(function(chain,w) {
      return chain.then(function() { return request("/api/set?id="+w[0]+"&value="+w[1]); })
        .then(function(){ delete $("tune_"+w[0]).dataset.dirty; });
    }, Promise.resolve());
  }
  /* ★四按钮直接控制 (2026-09-28): 读取参数 / 应用参数 / 启动 / 停止。
     "应用参数" = 写参数 + 立即保存到设备 (原来拆成"应用调参"和"保存到设备"两个按钮, 容易漏掉保存)。
     "启动" 内部已经把调参与目标一起下发, 所以不再需要单独的"下发当前目标"。 */
  $("btn_tune_read").addEventListener("click",function() {
    request("/api/getall").then(function(r){
      tuneDefs.forEach(function(d){delete $("tune_"+d[0]).dataset.dirty;});
      updateTuning(r.params); note("loop_hint","已读取设备参数。",false);
    }).catch(function(e){note("loop_hint",e.message,true);});
  });
  $("btn_tune_apply").addEventListener("click",function() {
    if (runBusy) { return; }
    runBusy = true;
    Promise.resolve().then(function(){return applyTuning(false);}).then(function(){return request("/api/save");})
      .then(function(){note("loop_hint","参数已应用并保存到设备。",false);})
      .catch(function(e){note("loop_hint",e.message,true);})
      .then(function(){ runBusy = false; });
  });
  $("btn_capture_position").addEventListener("click",function() {
    if (feedback.stale || feedback.position_stale || typeof feedback.position_deg !== "number") { note("loop_hint","等待新鲜位置反馈",true); return; }
    var outputPosition = positionTargetFromFeedback(feedback.position_deg);
    if (outputPosition < 0 || outputPosition > 360) { note("loop_hint","当前位置超出输出轴 0–360° 位置环范围",true); return; }
    $("target_position_deg").value=outputPosition.toFixed(3);
    $("target_mit_position_rad").value=(feedback.position_deg*Math.PI/180).toFixed(3);
    updatePositionReadout();
  });
  /* Firmware position is rotor-side multi-turn angle; UI converts it to gearbox output degrees.
     The position reference restarts at power-up and is not an absolute/homed world angle.
     If the user has not changed the target, selecting position mode captures current output angle.
     Now:
       1) Labels identify output-axis degrees and the power-up-relative reference;
       2) entering position mode with an untouched target captures current position;
       3) 加"目标 = 当前位置 + 相对角"按钮, 支持相对移动的直觉;
       4) 实时读数显示 当前位置 -> 目标 以及差值;
       5) 启动前若差值 > 30° 必须确认。 */
  function updatePositionReadout() {
    var el = $("position_readout");
    if (!el) { return; }
    var cur = (typeof feedback.position_deg === "number" && !feedback.position_stale) ? rotorToOutputDeg(feedback.position_deg) : null;
    var tgt = Number($("target_position_deg").value);
    if (cur === null || !isFinite(tgt)) { el.textContent = "当前位置 —（等待新鲜反馈）"; el.className = "loop-readout-strong bad"; return; }
    var d = tgt - cur;
    el.textContent = "输出轴位置 " + cur.toFixed(2) + "° → 目标 " + tgt.toFixed(2) +
                     "°（差 " + (d >= 0 ? "+" : "") + d.toFixed(2) + "°）";
    el.className = "loop-readout-strong" + (Math.abs(d) > 30 ? " bad" : "");
  }
  /* ★ 2026-09-28 审查要求: 把"启动助跑阶段 / 控制电角度 / 饱和"显示出来,
     不要再让操作者从"累计 820 counts"猜交接时刻。
     走轻量端点 /api/diag/assist (只拉 bank19/20, 2 次 CAN 请求), 运行中约 2Hz。 */
  var lastAssistPoll = 0;
  function pollAssistDiag() {
    var t = Date.now();
    if (t - lastAssistPoll < 450) { return; }
    lastAssistPoll = t;
    return pollOnce("assist", function () { return fetchJSON("/api/diag/assist").then(function (a) {
      if (!a || !a.ok) { return; }
      var st = $("control_assist_stage");
      if (st) { st.textContent = a.stage_name + "（行程 " + a.travel_counts + " cnt · θ " + a.theta_deg.toFixed(1) + "°）"; }
      var fl = $("control_assist_flags");
      if (fl) {
        var s = [];
        if (a.voltage_saturated) { s.push("电压"); }
        if (a.iq_limited) { s.push("Iq限幅"); }
        if (a.speed_loop_saturated) { s.push("速度环"); }
        if (a.position_loop_saturated) { s.push("位置环"); }
        fl.textContent = s.length ? s.join(" + ") : "无";
        fl.className = s.length ? "bad" : "";
      }
    }).catch(function () { /* 旧固件没有 bank19/20 -> 静默忽略 */ }); });
  }
  $("btn_position_rel").addEventListener("click",function() {    if (feedback.stale || feedback.position_stale || typeof feedback.position_deg !== "number") { note("loop_hint","等待新鲜位置反馈",true); return; }
    var rel = Number($("position_rel_deg").value);
    if (!isFinite(rel)) { note("loop_hint","相对角度无效",true); return; }
    var curOutput = rotorToOutputDeg(feedback.position_deg);
    var nextOutput = curOutput + rel;
    if (nextOutput < 0 || nextOutput > 360) { note("loop_hint","目标超出输出轴 0–360° 位置范围",true); return; }
    $("target_position_deg").value = nextOutput.toFixed(3);
    $("target_position_deg").dataset.dirty = "1";
    updatePositionReadout();
    note("loop_hint","目标已设为 输出轴当前位置 + " + rel + "°",false);
  });
  /* ★模式与校准彻底解绑:
       - 选模式只改显示 (目标字段 / 调参 / 反馈 / 提示), 不发任何 CAN。
       - 校准是上面那个独立卡片里的独立动作, 不再作为运行模式的前置条件。
       - 需要编码器角度的模式若还没校准, 只给一条提示, 启动按钮照常可点,
         由固件按自己的状态决定是否接受 (会回具体原因)。 */
  function chooseMode(id) {
    mode = id;
    window.dashboardMode = id;
    $("control_mode_select").value = String(id);
    if ($("tune_title")) { $("tune_title").textContent = (modeNames[id] || "模式") + " · 参数"; }
    if (noteEl) { noteEl.textContent = modeDesc[id] || ""; }
    $("mode_feedback").hidden = id === 0;
    document.querySelectorAll("[data-tune-modes]").forEach(function(el) {
      el.hidden = el.dataset.tuneModes.split(",").indexOf(String(id)) < 0;
    });
    $("btn_capture_position").hidden = id !== 3 && id !== 6;
    updateTuning(lastParams);
    document.querySelectorAll("#mode_chips .mode-chip").forEach(function (b) {
      b.classList.toggle("selected", parseInt(b.getAttribute("data-control-mode"), 10) === id);
    });
    document.querySelectorAll(".target-group[data-target-mode]").forEach(function (g) {
      g.hidden = parseInt(g.getAttribute("data-target-mode"), 10) !== id;
    });
    /* ★位置模式: 用户没改过目标就以【当前位置】为目标(等价于"先保持"),
       避免默认 0° 与当前位置差很远时一启动就整圈跑飞。 */
    if (id === 3) {
      var el3 = $("target_position_deg");
      if (el3 && !el3.dataset.dirty &&
          typeof feedback.position_deg === "number" && !feedback.position_stale) {
        var capture = positionTargetFromFeedback(feedback.position_deg);
        if (capture >= 0 && capture <= 360) { el3.value = capture.toFixed(3); }
      }
      updatePositionReadout();
    }
    /* 开环(mode 0)只有电压/电流上限两项 (随启动命令下发), 没有设备参数可读写 */
    $("btn_tune_read").disabled = (id === 0);
    $("btn_tune_apply").disabled = (id === 0);
    if (id === 0) { $("tune_summary").textContent = ""; }
    syncModeTrigger();
    syncCalibHint();
    note("loop_hint", "", false);
  }
  /* 模式下拉: 按钮显示当前模式, 点开是七个模式的列表, 选完自动收起。
     选中即生效: 之后点「启动」就是这个模式, 不再二次确认。 */
  function syncModeTrigger() {
    var sel = document.querySelector("#mode_chips .mode-chip.selected");
    if (!sel) { return; }
    $("mode_trigger_name").textContent = sel.querySelector("b").textContent;
    $("mode_trigger_sub").textContent = sel.querySelector("small").textContent;
    document.querySelectorAll("#mode_chips .mode-chip").forEach(function (b) {
      b.setAttribute("role", "option");
      b.setAttribute("aria-selected", b === sel ? "true" : "false");
    });
  }
  function setModeList(open) {
    var list = $("mode_chips");
    list.hidden = !open;
    $("mode_trigger").setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      var sel = list.querySelector(".mode-chip.selected") || list.querySelector(".mode-chip");
      if (sel) { sel.focus(); }
    }
  }
  $("mode_trigger").addEventListener("click", function (ev) {
    ev.stopPropagation();
    setModeList($("mode_chips").hidden);
  });
  document.querySelectorAll("#mode_chips .mode-chip").forEach(function (b) {
    b.addEventListener("click", function () { setModeList(false); $("mode_trigger").focus(); });
  });
  $("mode_chips").addEventListener("keydown", function (ev) {
    var items = Array.prototype.slice.call(document.querySelectorAll("#mode_chips .mode-chip"));
    var i = items.indexOf(document.activeElement);
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      i = (i + (ev.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
      items[i].focus();
    } else if (ev.key === "Tab" || ev.key === "Escape") {
      setModeList(false);
    }
  });
  document.addEventListener("click", function (ev) {
    if (!$("mode_chips").hidden && !ev.target.closest(".mode-select")) { setModeList(false); }
  });
  /* ★运行态: 启动后按钮显示"运行中"、模式锁定 (固件运行中不接受换模式, 回 -9);
     目标框改完 (回车/失焦) 直接下发, 开环滑条拖动时合并发送, 不用停机重启。 */
  var running = false;
  var openRpmTimer = null, openRpmQueued = null, openRpmPending = false;
  function cancelOpenRpm() {
    if (openRpmTimer !== null) { clearTimeout(openRpmTimer); openRpmTimer = null; }
    openRpmQueued = null;
  }
  function flushOpenRpm() {
    openRpmTimer = null;
    if (openRpmPending) { return; }
    var target = openRpmQueued;
    openRpmQueued = null;
    if (!target || !running || mode !== 0 || runBusy || otaBusy || target.generation !== commandGeneration) { return; }
    openRpmPending = true;
    request("/api/control/set?field=open_speed_rpm&value=" + encodeURIComponent(target.value))
      .then(function () {
        if (running && target.generation === commandGeneration) { note("loop_hint", "开环转速目标已更新。", false); }
      }).catch(function (e) {
        if (target.generation === commandGeneration) { note("loop_hint", e.message, true); }
      }).finally(function () {
        openRpmPending = false;
        if (openRpmQueued && openRpmTimer === null) { openRpmTimer = setTimeout(flushOpenRpm, 150); }
      });
  }
  function queueOpenRpm(immediate) {
    if (!running || mode !== 0 || runBusy || otaBusy) { cancelOpenRpm(); return; }
    var value = Number($("in_rpm").value);
    if ($("in_rpm").value === "" || !isFinite(value) || value < 3 || value > 200) {
      cancelOpenRpm(); note("loop_hint", "开环目标须为 3～200 rpm。", true); return;
    }
    openRpmQueued = {value:value, generation:commandGeneration};
    if (immediate && !openRpmPending) {
      if (openRpmTimer !== null) { clearTimeout(openRpmTimer); }
      flushOpenRpm();
    } else if (openRpmTimer === null && !openRpmPending) {
      openRpmTimer = setTimeout(flushOpenRpm, 150);
    }
  }
  function syncRunUi() {
    var m = (window.dashboardStatus || {}).motor || {};
    var on = !!(m.available && m.output_on);
    var run = $("btn_loop_run");
    run.textContent = on ? "运行中" : "启动";
    run.classList.toggle("running", on);
    run.disabled = on || otaBusy || mode === null || runBusy;
    $("mode_trigger").disabled = on || otaBusy;
    if (on) { setModeList(false); }
    $("mode_trigger").title = on ? "停止后才能切换模式" : "";
    if (on !== running) {
      running = on;
      if (!on) { cancelOpenRpm(); }
      if (!on && !runBusy) { note("loop_hint", m.fault ? "设备已停止（保护触发，详见上方保护状态）。" : "", !!m.fault); }
    }
  }
  function liveTarget(ev) {
    if (!running || runBusy || mode === null || mode === 0) { return; }
    if (ev.type === "keydown" && ev.key !== "Enter") { return; }
    var generation = commandGeneration;
    setTargets(generation).then(function () {
      note("loop_hint", "新目标已下发。", false);
    }).catch(function (e) { note("loop_hint", e.message, true); });
  }
  document.querySelectorAll(".target-group[data-target-mode] input").forEach(function (el) {
    if (el.id === "in_rpm" || el.id === "in_rpm_slider" || el.id === "position_rel_deg") { return; }
    el.addEventListener("change", liveTarget);
    el.addEventListener("keydown", liveTarget);
  });
  $("in_rpm").addEventListener("change", function () {
    queueOpenRpm(true);
  });
  $("in_rpm").addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") { queueOpenRpm(true); }
  });
  (function () {
    var input = $("in_rpm"), slider = $("in_rpm_slider");
    function syncSlider() {
      var value = Number(input.value);
      if (input.value !== "" && isFinite(value) && value >= Number(slider.min) && value <= Number(slider.max)) {
        slider.value = String(value);
      }
      slider.title = slider.value + " rpm";
    }
    input.addEventListener("input", syncSlider);
    input.addEventListener("change", syncSlider);
    slider.addEventListener("input", function () {
      input.value = slider.value;
      input.dispatchEvent(new Event("input", {bubbles:true}));
      queueOpenRpm(false);
    });
    slider.addEventListener("change", function () {
      input.dispatchEvent(new Event("change", {bubbles:true}));
    });
    syncSlider();
  })();
  function setTargets(generation) {
    if (mode === 0) { return Promise.resolve(); }
    if (mode === null || !fields[mode]) { return Promise.reject(new Error("请先选择运行模式")); }
    return fields[mode].reduce(function (chain, field) {
      return chain.then(function () {
        checkCommandGeneration(generation);
        var input = $(inputs[field]);
        var value = input ? parseFloat(input.value) : NaN;
        if (!isFinite(value)) { throw new Error("请输入有效的目标值"); }
        return request("/api/control/set?field=" + encodeURIComponent(field) +
                       "&value=" + encodeURIComponent(value));
      });
    }, Promise.resolve());
  }
  document.querySelectorAll("#mode_chips .mode-chip").forEach(function (b) {
    b.addEventListener("click", function () {
      if (mode === Number(b.getAttribute("data-control-mode"))) { return; }
      chooseMode(parseInt(b.getAttribute("data-control-mode"), 10));
    });
  });
  /* ======================================================================
   * ★启动前闸门检查 (2026-09-27)
   * 固件对模式 1..6 有一串互锁, 但旧固件把所有拒绝都回成同一个 "out of range",
   * 页面上只能看到"参数非法" —— 分不清是没校准、没编码器帧, 还是采样没和 PWM
   * 同步(-10)。所以在上位机侧先把已知条件查一遍, 直接说出"哪一条没过"。
   *
   * 检查项与固件 foc_motor_ctrl() 的返回码一一对应:
   *   -7 未校准      <- control.calibration_stage != 3
   *   -8 无新鲜编码器 <- encoder.valid && !stale
   *   -9 非 IDLE      <- motor.state_name
   *   -10 采样未同步  <- /api/diag/stat 的 sync_locked (诊断帧 bit2)
   *   -6 过温        <- ntc.over / tripped / tripped_latched
   * 只在"确定不过"时才拦: 拿不到诊断帧(未知)就不拦, 交给固件裁决。
   * ====================================================================== */
  var diagStat = null;
  /* ★ 故障原因: 只报"当前 fault"没用, 得说清【哪一个】故障, 否则现场只能猜。
     码表来自 foc/foc_mode.c 与 ota/ota_params.c。 */
  var motorFault = null;
  var FAULT_NAMES = {1: "ADC 采样陈旧", 2: "通用过流 (OCP 跳闸)", 3: "NTC 过温",
                     4: "闭环过流 (超 i_limit 约 300us)", 5: "硬过流 (>1.5×i_limit)",
                     6: "编码器失效", 7: "校准过流",
                     10: "控制漏拍 (连续错过 100us 截止)", 12: "电流环未锁住 (诊断用)"};
  function fetchDiagStat() {
    // /api/diag/stat 是"请求固件回一帧 0x90"再返回缓存快照 -> 第一次可能还没到, 取第二次
    return request("/api/diag/stat").then(function () {
      return new Promise(function (res) { setTimeout(res, 250); });
    }).then(function () {
      return request("/api/diag/stat");
    }).then(function (r) {
      diagStat = (r && r.stat) || null;
      /* 故障码/峰值/OCP 只在 /api/diag/raw 里; 只在真的要启动前拉一次 */
      return request("/api/diag/raw").then(function (raw) {
        motorFault = raw || null;
      }).catch(function () { motorFault = null; });
    }).then(function () {
      return diagStat;
    }).catch(function () { return null; });
  }
  function faultText() {
    if (!motorFault || !motorFault.motor_fault_reason) { return ""; }
    var c = motorFault.motor_fault_reason;
    return "  最近一次故障码 " + c + "（" + (FAULT_NAMES[c] || "未知") + "），峰值 "
           + motorFault.motor_fault_peak_ma + " mA，当前 OCP 上限 " + motorFault.motor_ocp_ma + " mA。";
  }
  function startGateReason(diagOverride) {
    if (mode === null || mode === 0) { return null; }
    var s = window.dashboardStatus || {};
    if (!calibrationUsable()) {
      return "没有可用的编码器校准 (零位/方向) —— 先做上面的「① 编码器校准」(固件会以 -7 拒绝)。";
    }
    var e = s.encoder || {};
    if (!e.valid || e.stale) {
      return "没有新鲜有效的 AS5048A 角度帧 —— 检查磁铁/SPI2 接线 (固件会以 -8 拒绝)。";
    }
    var m = s.motor || {};
    if (m.state_name && m.state_name !== "idle") {
      return "电机不在 IDLE (当前 " + m.state_name + ") —— 先点「停止」清掉状态再启动（固件会以 -9 拒绝）。"
             + faultText();
    }
    var n = s.ntc || {};
    if (n.over || n.tripped || n.tripped_latched) {
      /* ★2026-09-28: 阈值改用设备上报的 limit_c(不再硬编码 50/45 —— 用户已把跳闸提到 60) */
      return "过温 (≥" + fmt(s.ntc.limit_c) + " °C 或未复归) —— 需降到滞回点以下才能再启动 (固件会以 -6 拒绝)。";
    }
    var ds = diagOverride || diagStat;
    if (ds && ds.sync_locked === false) {
      return "采样未与 PWM 同步 —— 电流环会被拒绝 (固件 -10); sync_code=" + ds.sync_code +
             (ds.sync_fallback ? " (已回退自由跑)" : "") + "，详见诊断页。";
    }
    return null;
  }
  /* 暴露给控制台/自动化测试 (只读纯函数, 不发电机命令) */
  window.startGateReason = startGateReason;
  $("btn_loop_run").addEventListener("click", function () {
    if (runBusy || mode === null) { return; }
    runBusy = true;
    var generation=commandGeneration;
    $("btn_loop_run").disabled = true;
    /* ★模式 1..6 先拉一次诊断帧(拿 ADC 同步状态), 再把"过不了的闸门"直接说清楚;
       开环(mode 0)不需要编码器/同步采样, 不查。 */
    var pre = (mode === 0) ? Promise.resolve() : fetchDiagStat();
    pre.then(function () {
      checkCommandGeneration(generation);
      var why = startGateReason();
      if (why) { throw new Error(why); }
      /* ★ 2026-09-28 两个会让人白跑一趟的坑, 启动前明确确认 (不是常驻小字, 只在真踩到时弹):
         ① 电压模式里的 Ud: d 轴电压只产生径向电流, 【完全不产生转矩】。用户实测把 Ud 填成 5 V,
            结果 Id=3.09 A / Iq≈0 / 转速 0, 而且 5V/0.275Ω≈18A 直接顶到静止电流上限 -> fault。
            转矩全部来自 Uq; 正常情况下 Ud 必须保持 0。
         ② 电流上限 < 4.5 A: 见工程说明.txt的历史电流限制与当前机械状态 —— 本机静起动门槛约 0.44 N·m,
            3 A 档已被证明让电机完全起不来。 */
      if (mode === 4) {
        var ud = Number($("target_voltage_d_v").value);
        if (isFinite(ud) && Math.abs(ud) > 0.1) {
          if (!window.confirm("电压模式的 Ud 现在是 " + ud + " V。Ud 是 d 轴电压, 只产生径向电流, "
              + "【不产生任何转矩】; 转矩全部来自 Uq。若只是想让电机转, 请把 Ud 改回 0。\n\n仍要用 Ud=" + ud + " V 启动吗?")) {
            throw new Error("已取消: 请先把 Ud 改回 0（除非确实要做弱磁）。");
          }
        }
      }
      /* Confirm large moves in gearbox output-axis degrees before sending a rotor-side target. */
      if (mode === 3) {
        var tgt3 = Number($("target_position_deg").value);
        var cur3 = (typeof feedback.position_deg === "number" && !feedback.position_stale)
                   ? rotorToOutputDeg(feedback.position_deg) : null;
        if (cur3 !== null && isFinite(tgt3) && Math.abs(tgt3 - cur3) > 30) {
          if (!window.confirm("位置目标使用输出轴角度，参考点为本次上电位置。\n\n"
              + "输出轴当前位置 " + cur3.toFixed(2) + "° → 目标 " + tgt3.toFixed(2) + "°\n"
              + "启动后输出轴目标位移 " + (tgt3 - cur3).toFixed(1) + "°（约 "
              + (Math.abs(tgt3 - cur3) / 360).toFixed(2) + " 输出轴圈）。\n\n"
              + "确认要移动这么多吗?（想小幅移动请用「目标 = 当前位置 + 相对角」）")) {
            throw new Error("已取消: 位置目标与当前位置相差 "
              + Math.abs(tgt3 - cur3).toFixed(1) + "°, 请确认目标值。");
          }
        }
      }
      /* Read back device parameters before applying only fields explicitly changed by the operator. */
      return request("/api/getall").then(function (r) {
        updateTuning(r.params);   /* 改过 (dirty) 的框不会被覆盖, 下面随启动写入 */
        var p7 = (r.params || []).filter(function (x) { return x.id === 7; })[0];
        var dev_a = (p7 && isFinite(p7.value)) ? (p7.value / 1000) : null;
        window.deviceILimitA = dev_a;
      });
    }).then(function(){checkCommandGeneration(generation);return mode===0?null:applyTuning(true);})
      .then(function(){checkCommandGeneration(generation);return setTargets(generation);}).then(function () {
      checkCommandGeneration(generation);
      /* ★ "启动" 只下发【目标】, 不再写调参!
         原因: 页面里的调参框可能是几小时前的旧值 (例如设备已改成 8 A, 而框里还留着 3),
         原来在启动链里调 applyTuning 会把这个陈旧值【写回设备】, 把 OCP 从 8 A 打回 3 A ——
         2026-09-28 实测踩到: 电压环突然"间歇卡住", 查下来是 i_limit 被写回 3000。
         所以启动只写【用户改过的】参数框 (dataset.dirty), 其余以设备为准。 */
      var seconds=Number($("mode_seconds").value);
      if(!Number.isInteger(seconds)||seconds<0||seconds>255){throw new Error("运行时长须为 0～255 秒（0 = 不限时）");}
      var url="/api/motor?enable=1&mode="+mode+"&seconds="+seconds;
      if(mode===0){
        var rpm=Number($("in_rpm").value),v=Number($("open_voltage").value),i=Number($("open_current").value);
        if(!isFinite(rpm)||rpm<3||rpm>200||!isFinite(v)||v<.1||v>5||!isFinite(i)||i<.1||i>6){throw new Error("开环目标超出范围");}
        url+="&rpm="+encodeURIComponent(rpm)+"&volt="+encodeURIComponent(v)+"&vbus=48&cur_ma="+Math.round(i*1000);
      }
      return request(url);
    }).then(function () {
      var sec = Number($("mode_seconds").value);
      note("loop_hint", modeNames[mode] + (sec > 0 ? "运行中，" + sec + " 秒后自动停止。" : "运行中（不限时，点「停止」结束）。")
           + (mode === 0 ? "" : " 改目标后回车即下发。"), false);
      pollControl();
    }).catch(function (e) {
      var msg = e.message || String(e);
      /* ★固件把模式 1..6 的所有拒绝都回成同一个 "out of range"(见
         ota/ota_params.c 的 MOTOR_CTRL 分支), 所以这里补一句可能性清单,
         免得只看到"参数非法"却不知道往哪查。 */
      if (mode !== 0 && /out of range|OTA_PERR_RANGE/i.test(msg)) {
        msg += " —— 固件没细分原因, 可能依次是: 未校准(-7) / 无新鲜编码器帧(-8) /"
             + " 非 IDLE(-9) / 采样未与 PWM 同步(-10) / 过温(-6)。"
             + (diagStat ? (" 当前 sync_locked=" + diagStat.sync_locked + " sync_code=" + diagStat.sync_code + "。") : "");
      }
      note("loop_hint", msg, true);
    }).then(function () {
      runBusy = false;
      syncRunUi();
    });
  });
  $("btn_loop_stop").addEventListener("click", function () {
    stopOutputs().then(function () {
      note("loop_hint", "已确认设备停止输出。", false);
      pollControl();
    }).catch(function (e) { note("loop_hint", e.message, true); });
  });

  function pollControl() {
    if (document.hidden) { return; }
    return pollOnce("control", function () { return fetchJSON("/api/control/status").then(function (r) {
      if (!r || !r.ok || !r.control) { return; }
      feedback = r.feedback || {};
      if (typeof updatePositionReadout === "function") { updatePositionReadout(); }
      /* 只在真有输出时拉诊断, 免得空转时白占 CAN 带宽 */
      if ((window.dashboardStatus && window.dashboardStatus.motor &&
           window.dashboardStatus.motor.output_on) || !feedback.stale) {
        if (typeof pollAssistDiag === "function") { pollAssistDiag(); }
      }
      [["loop_id","id_a",3],["loop_iq","iq_a",3],["loop_speed","speed_rpm",1]].forEach(function(d) {
        $(d[0]).textContent = !feedback.stale && typeof feedback[d[1]] === "number" ? feedback[d[1]].toFixed(d[2]) : "—";
      });
      $("loop_position").textContent = !feedback.stale && typeof feedback.position_deg === "number"
        ? rotorToOutputDeg(feedback.position_deg).toFixed(3) : "—";
      var c = r.control;
      var stage = c.calibration_stage;
      calibrationDone = stage === 3;
      calibrationActive = calibRunning(stage);
      calibStage = stage;
      calibError = c.calibration_error || 0;
      calibProgress = c.calibration_progress || 0;
      var progress = $("calib_progress");
      progress.value = calibProgress;
      /* ★ 只要本次尝试留下过错误码就一直显示; 并且当 stage===3 时明确说明
       *   "沿用已保存标定" —— AS5048A 是绝对编码器, 零点只跟机械装配有关,
       *   一次失败/被中止的尝试不该让人以为必须重新校准 (2026-09-28 修)。 */
      var calibErrText = calibError
        ? (calibrationErrors[calibError] || ("错误 " + calibError)) : "";
      setNodeText($("calib_status"), (stageNames[stage] || "未知") +
        (calibErrText ? " · " + calibErrText : "") +
        ((stage === 3 && calibErrText) ? " · 沿用已保存的标定" : "") +
        " · " + calibProgress + "%");
      $("btn_calibrate_abort").disabled = !calibRunning(stage);
      if (window.syncCalibrationButton) { window.syncCalibrationButton(); }
      setCalibBadge();
      syncCalibHint();
      setNodeText($("control_mode_status"), modeNames[c.mode] ||
        (c.mode === 0 ? "正弦开环" : "—"));
      setNodeText($("control_iq_status"), (c.iq_target_ma / 1000).toFixed(3) + " A");
      setNodeText($("control_calib_status"), stageNames[stage] || "未知");
      syncRunUi();
    }).catch(function () {}); });
  }
  $("btn_calibrate").addEventListener("click", function () {
    if (!encoderReadable) {
      note("loop_hint", "尚未收到有效且实时的磁编码器角度帧；读取状态正常后才能开始校准。", true);
      return;
    }
    if (!window.confirm("编码器自校准使用开环三相正弦，当前相电流硬上限为 1.0 A，随后扫角采样（正反各一遍，10 极对约 50 秒；期间电机会连续慢转）。请确认极对数与电机铭牌一致，电机轴可自由转动。开始吗？")) {
      return;
    }
    request("/api/control/calibrate?op=start").then(function () {
      note("loop_hint", "校准已开始；固件已确认磁编码器连续有效读取，请保持电机轴可自由转动。", false);
      pollControl();
    }).catch(function (e) { note("loop_hint", e.message, true); });
  });
  $("btn_calibrate_abort").addEventListener("click", function () {
    request("/api/control/calibrate?op=abort").then(function () {
      note("loop_hint", "已中止编码器校准。", false);
      pollControl();
    }).catch(function (e) { note("loop_hint", e.message, true); });
  });

  var otaBusy = false;
  /* ★校准按钮的唯一裁决点: 只有"适配器在跑 OTA / 正在校准 / 没有新鲜磁编帧"才禁用。
     它【不】看当前选了哪个运行模式 —— 校准与模式运行是两件事。 */
  window.syncCalibrationButton = function () {
    var calibrationButton = $("btn_calibrate");
    if (!calibrationButton) { return; }
    calibrationButton.disabled = otaBusy || calibrationActive || !encoderReadable;
    calibrationButton.textContent = encoderReadable ? "开始自校准" : "开始自校准（等待磁编数据）";
  };
  function renderOta(s) {
    if (!s) { return; }
    otaBusy = !!s.running;
    var labels = {idle:"待命", stopping:"确认停机并释放 CAN", running:"编译与 CAN-OTA 进行中", done:"升级流程完成", failed:"执行失败"};
    $("ota_status").textContent = (labels[s.state] || s.state) + " · 目标工程 " + (s.project || "xige_foc") +
      (s.returncode !== null && s.returncode !== undefined ? " · exit " + s.returncode : "");
    $("btn_ota_onekey").disabled = otaBusy;
    $("control_mode_select").disabled = otaBusy;
    syncRunUi();
    $("btn_loop_stop").disabled = otaBusy;
    $("btn_calibrate_abort").disabled = otaBusy || !calibrationActive;
    $("btn_calibrate").disabled = otaBusy || calibrationActive || !encoderReadable;
    document.querySelectorAll("#mode_chips .mode-chip").forEach(function (b) {
      b.disabled = otaBusy;
    });
    window.syncCalibrationButton();
    var lines = (s.output || []).slice(-80);
    if (s.error) { lines.push("ERROR: " + s.error); }
    $("ota_output").textContent = lines.length ? lines.join("\n") : "尚未运行";
    $("ota_output").scrollTop = $("ota_output").scrollHeight;
  }
  function pollOta() {
    return pollOnce("ota", function () { return fetchJSON("/api/ota/status").then(renderOta).catch(function () {}); });
  }
  $("btn_ota_onekey").addEventListener("click", function () {
    if (otaBusy) { return; }
    if (!window.confirm("将先停止电机，再在 xige_foc 目录递增版本号、编译 A/B 固件并通过 CAN-OTA 更新当前连接的设备。继续吗？")) {
      return;
    }
    $("btn_ota_onekey").disabled = true;
    request("/api/ota/onekey").then(function (r) {
      renderOta(r.ota);
      pollOta();
    }).catch(function (e) {
      $("btn_ota_onekey").disabled = false;
      $("ota_status").textContent = "启动失败 · " + e.message;
    });
  });
  chooseMode(0);
  /* ★运行时长默认不限, 三道自动停机:
       页面关闭/刷新 -> 这里立即发停止 (keepalive, 页面卸载后也会送达);
       页面卡死/断网 -> 服务端 6 s 没收到请求就停机 (后台 Worker 每秒报到, 标签页进后台被节流也不误停);
       服务端/USB-CAN/CAN 断 -> 固件 1 s 收不到心跳就停机。 */
  window.addEventListener("pagehide", function () {
    try {
      fetch("/api/motor?enable=0", {method: "POST", keepalive: true, cache: "no-store",
        headers: {"X-Control-Token": controlToken, "X-Command-Epoch": String(commandEpoch)}});
    } catch (e) { /* 页面已经在卸载 */ }
  });
  try {
    var aliveSrc = "setInterval(function(){fetch('" + location.origin + "/api/alive',{cache:'no-store'}).catch(function(){});},1000);";
    new Worker(URL.createObjectURL(new Blob([aliveSrc], {type: "text/javascript"})));
  } catch (e) { /* 没有 Worker: 页面自己的 200 ms 轮询照样喂看门狗 */ }
  pollControl();
  pollOta();
  setInterval(pollControl, 200);
  setInterval(pollOta, 1500);
})();
</script>
</body>
</html>
"""


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
                    for p in PARAM_TABLE
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

        if route == "/api/control/status":
            if app.ota_busy():
                self._json({"ok": True, "control": link.control_snapshot(),
                            "ota_busy": True})
                return
            control, err = link.control_stat()
            self._json({"ok": control is not None, "control": control,
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
                self._json({"ok": False, "error": "mode must be in 0..6"}, status=400)
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
            if isinstance(bus, PyCanBus):
                if getattr(self.link, "need_reopen", False):
                    self.link.need_reopen = False
                    self.link.add_error("CAN 适配器 IO 异常, 重新打开")
                    self.link.detach()
                else:
                    return
            else:
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
