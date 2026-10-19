"""Automatic run recorder: every motor start writes a self-contained log folder.

logs/运行记录/<时间>_mode<M>/
    meta.txt   启动时间、模式、固件版本、启动时全部参数、目标值、运行中的参数/目标改动事件
    run.csv    约 20 Hz: 状态 / 速度(PLL) / id / iq / 速度环指令 / 速度积分 / 标志 / 位置 / 编码器 / 三相电流
    enc.csv    每个编码器帧一行 (主机接收时间 + 原始计数, 约 100 Hz), 用来离线算真实速度

Started from Link.motor_ctrl (any mode), ends when the motor returns to idle, faults,
the commanded run time elapses or after HARD_LIMIT_S.  Only reads: it never sends a
command that changes motor state.
"""
import csv
import os
import threading
import time

SAMPLE_PERIOD_S = 0.05
HARD_LIMIT_S = 900.0
TAIL_S = 1.0
MODE_NAMES = {0: "open_sine", 1: "torque", 2: "velocity", 3: "position",
              4: "voltage", 5: "current", 6: "mit"}
STATE_NAMES = {0: "idle", 1: "precharge", 2: "running", 3: "fault"}
RUN_COLUMNS = ["t_s", "host_time", "state", "speed_pll_rpm", "id_a", "iq_a",
               "speed_ref_rpm", "speed_integral_a", "flags", "theta_deg",
               "position_rotor_deg", "enc_angle_deg", "ia_ma", "ib_ma", "ic_ma",
               # 固件 bank24..26 (0x0A29 起), 与 id/iq 同一拍:
               "vd_demand_v", "vq_demand_v", "omega_e_rad_s", "id_inst_a", "iq_inst_a",
               "iq_cmd_ramped_a", "int_d_v", "int_q_v", "fw_state", "fw_flags"]


def _s16(v):
    return v - 0x10000 if v >= 0x8000 else v


class RunRecorder(object):
    def __init__(self, link, log_dir, request_feedback):
        self.link = link
        self.request_feedback = request_feedback    # callable: queue one CONTROL_FEEDBACK request
        self.log_dir = log_dir
        self._lock = threading.Lock()
        self._thread = None
        self._meta = None
        self._t0 = 0.0
        self.last_dir = None
        self._mode = 0

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    # -- hooks -------------------------------------------------------------
    def start(self, mode, seconds):
        """Begin a recording; a second start while one is running is just an event."""
        with self._lock:
            if self.active:
                self.event("再次启动命令 mode=%s seconds=%s" % (mode, seconds))
                return
            stamp = time.strftime("%Y%m%d_%H%M%S")
            folder = os.path.join(self.log_dir, "%s_mode%d_%s" % (
                stamp, mode, MODE_NAMES.get(mode, "?")))
            try:
                os.makedirs(folder, exist_ok=True)
                self._t0 = time.time()
                self._meta = open(os.path.join(folder, "meta.txt"), "w", encoding="utf-8")
            except OSError as exc:
                self.link.add_error("run recorder: %s" % exc)
                return
            self.last_dir = folder
            self._mode = mode
            self._write_header(mode, seconds)
            self._thread = threading.Thread(target=self._run, args=(folder, seconds),
                                            name="foc-run-recorder", daemon=True)
            self._thread.start()
            threading.Thread(target=self._dump_params, name="foc-run-params", daemon=True).start()

    def event(self, text):
        """Append a timestamped note (parameter / target change) to the active run."""
        with self._lock:
            if self._meta is None:
                return
            try:
                self._meta.write("[t=%7.2fs] %s\n" % (time.time() - self._t0, text))
                self._meta.flush()
            except (OSError, ValueError):
                pass

    # -- internals ---------------------------------------------------------
    def _write_header(self, mode, seconds):
        link = self.link
        m = self._meta
        info = (link.info.get("info") or {})
        m.write("启动时间: %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        m.write("模式: %d (%s)\n" % (mode, MODE_NAMES.get(mode, "?")))
        m.write("运行时限: %s s (0=不限时)\n" % seconds)
        m.write("固件版本: 0x%04X\n" % (info.get("version") or 0))
        m.write("\n[启动时的目标值 (固件单位, 原始整数)]\n")
        for field, raw in sorted(getattr(link, "control_targets", {}).items()):
            m.write("  field %d = %d\n" % (field, raw))
        m.write("\n[启动时的参数: 见下方, 由单独线程从设备重新读取, 不用上位机缓存]\n")
        m.write("\n[运行中事件]\n")
        m.flush()

    def _dump_params(self):
        """Fresh GET_ALL from the device (cache can hold a stale/garbled reading)."""
        try:
            got, err = self.link.get_all()
        except Exception as exc:
            got, err = None, str(exc)
        lines = []
        if got is None:
            lines.append("  (读取失败: %s; 以下为上位机缓存, 可能不准)" % err)
        for p in self.link.params_snapshot():
            lines.append("  ID%-2d %-16s = %s %s%s" % (
                p["id"], p["name"], p["value"], p["unit"],
                "" if p.get("saved") else "   (未保存)"))
        with self._lock:
            if self._meta is not None:
                try:
                    self._meta.write("[t=%7.2fs] 启动时参数\n%s\n" % (
                        time.time() - self._t0, "\n".join(lines)))
                    self._meta.flush()
                except (OSError, ValueError):
                    pass

    def _assist(self):
        """speed_ref_rpm, speed_integral_a, flags, theta_deg (banks 19/20)."""
        link = self.link
        words = []
        for bank in (19, 20):
            reply = link.request("REC_ASSIST", link.param_host_id, link.param_dev_id,
                                 bytes([0x0D, 4, bank, 0, 0]), (0x91, 0x82), attempts=1)
            if reply is None or len(reply) != 8 or reply[0] != 0x91 or reply[1] != bank:
                return None
            words.extend(reply[i] | (reply[i + 1] << 8) for i in (2, 4, 6))
        return {"flags": words[2], "theta_deg": words[3] / 10.0,
                "speed_ref_rpm": _s16(words[4]), "speed_integral_a": _s16(words[5]) / 1000.0}

    def _speed_words(self):
        """bank 20: speed_ref_rpm, speed_integral_a (modes 2/3/6)."""
        link = self.link
        reply = link.request("REC_SPEED", link.param_host_id, link.param_dev_id,
                             bytes([0x0D, 4, 20, 0, 0]), (0x91, 0x82), attempts=1)
        if reply is None or len(reply) != 8 or reply[0] != 0x91 or reply[1] != 20:
            return None
        w = [reply[i] | (reply[i + 1] << 8) for i in (2, 4, 6)]
        return {"theta_deg": w[0] / 10.0, "speed_ref_rpm": _s16(w[1]), "speed_integral_a": _s16(w[2]) / 1000.0}

    def _pole_pairs(self):
        for p in self.link.params_snapshot():
            if p["id"] == 0 and p.get("value"):
                return p["value"]
        return 10

    def _loopv(self):
        """banks 24..26: vd/vq demand, omega_e, id/iq (same tick), iq command, integrators, state/flags."""
        link = self.link
        words = []
        for bank in (24, 25, 26):
            reply = link.request("REC_LOOP", link.param_host_id, link.param_dev_id,
                                 bytes([0x0D, 4, bank, 0, 0]), (0x91, 0x82), attempts=1)
            if reply is None or len(reply) != 8 or reply[0] != 0x91 or reply[1] != bank:
                return None
            words.extend(reply[i] | (reply[i + 1] << 8) for i in (2, 4, 6))
        return {"vd_demand_v": _s16(words[0]) / 1000.0, "vq_demand_v": _s16(words[1]) / 1000.0,
                "omega_e_rad_s": _s16(words[2]), "id_inst_a": _s16(words[3]) / 1000.0,
                "iq_inst_a": _s16(words[4]) / 1000.0, "iq_cmd_ramped_a": _s16(words[5]) / 1000.0,
                "int_d_v": _s16(words[6]) / 1000.0, "int_q_v": _s16(words[7]) / 1000.0,
                "fw_state": words[8] >> 8, "fw_flags": words[8] & 0xFF}

    def _run(self, folder, seconds):
        link = self.link
        t0 = self._t0
        enc_seq = 0
        seen_active = False
        idle_since = None
        try:
            with open(os.path.join(folder, "run.csv"), "w", newline="", encoding="utf-8") as rf, \
                 open(os.path.join(folder, "enc.csv"), "w", newline="", encoding="utf-8") as ef:
                rw = csv.writer(rf)
                ew = csv.writer(ef)
                rw.writerow(RUN_COLUMNS)
                ew.writerow(["host_time", "raw", "valid"])
                while not link._stop:
                    cycle = time.time()
                    if cycle - t0 > HARD_LIMIT_S:
                        self.event("达到 %d s 记录上限, 停止记录" % HARD_LIMIT_S)
                        break
                    # 请求越少采样越快 (适配器每个请求约 50 ms): 固件 bank24..26 已含状态/标志/
                    # id/iq/ωe, 所以只在需要速度环量 (模式 2/3/6) 时才多读 bank20。
                    loop = self._loopv()
                    if loop is None:                      # 老固件: 退回反馈帧
                        self.request_feedback()
                    assist = (self._speed_words() if self._mode in (2, 3, 6) else None) or {}
                    fb = link.feedback_snapshot()
                    loop = loop or {}
                    state = loop.get("fw_state", fb.get("state"))
                    if state is not None and state > 3:
                        state = None
                    enc = link.encoder or {}
                    newest = link.telem.snapshot()[0]
                    w_e = loop.get("omega_e_rad_s")
                    rpm = (w_e * 60.0 / (6.2831853 * self._pole_pairs())) if w_e is not None else fb.get("speed_rpm")
                    rw.writerow([
                        "%.3f" % (cycle - t0), "%.3f" % cycle, state,
                        None if rpm is None else "%.1f" % rpm,
                        loop.get("id_inst_a", fb.get("id_a")), loop.get("iq_inst_a", fb.get("iq_a")),
                        assist.get("speed_ref_rpm"), assist.get("speed_integral_a"),
                        loop.get("fw_flags"), assist.get("theta_deg"),
                        fb.get("position_deg"), enc.get("angle_deg"),
                        newest[1] if newest else None, newest[2] if newest else None,
                        newest[3] if newest else None,
                        loop.get("vd_demand_v"), loop.get("vq_demand_v"), w_e,
                        loop.get("id_inst_a"), loop.get("iq_inst_a"), loop.get("iq_cmd_ramped_a"),
                        loop.get("int_d_v"), loop.get("int_q_v"), loop.get("fw_state"), loop.get("fw_flags")])
                    with link.state_lock:
                        rows = [r for r in link.encoder_history if r[0] > enc_seq]
                    for seq, ts, raw, valid, _cal, _err in rows:
                        ew.writerow(["%.4f" % ts, raw, int(bool(valid))])
                        enc_seq = max(enc_seq, seq)
                    rf.flush()
                    ef.flush()
                    now = time.time()
                    if state in (1, 2):
                        seen_active = True
                        idle_since = None
                    elif state == 3 or (state == 0 and (seen_active or now - t0 > 3.0)):
                        if idle_since is None:
                            idle_since = now
                            self.event("状态 %s (%s)" % (state, STATE_NAMES.get(state, "?")))
                        if now - idle_since >= TAIL_S:
                            break
                    if seconds and now - t0 > seconds + 5.0:
                        break
                    time.sleep(max(0.0, SAMPLE_PERIOD_S - (time.time() - cycle)))
        except Exception as exc:  # recorder must never take the web service down
            link.add_error("run recorder failed: %s" % exc)
        finally:
            with self._lock:
                if self._meta is not None:
                    try:
                        self._meta.write("[t=%7.2fs] 记录结束\n" % (time.time() - t0))
                        self._meta.close()
                    except (OSError, ValueError):
                        pass
                self._meta = None
