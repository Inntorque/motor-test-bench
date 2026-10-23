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
import json
import max_output
import os
import threading
import time

SAMPLE_PERIOD_S = 0.05
HARD_LIMIT_S = 900.0
TAIL_S = 1.0
MODE_NAMES = {0: "open_sine", 1: "torque", 2: "velocity", 3: "position",
              4: "voltage", 5: "current", 6: "mit", 7: "maximum_output"}
STATE_NAMES = {0: "idle", 1: "precharge", 2: "running", 3: "fault"}
RUN_COLUMNS = ["t_s", "host_time", "state", "speed_pll_rpm", "id_a", "iq_a",
               "speed_ref_rpm", "speed_integral_a", "flags", "theta_deg",
               "position_rotor_deg", "enc_angle_deg", "ia_ma", "ib_ma", "ic_ma",
               # bank24..26: 0x0A2D 起先锁存再分片读取，旧固件不保证同拍。
               "vd_demand_v", "vq_demand_v", "omega_e_rad_s", "id_inst_a", "iq_inst_a",
               "iq_cmd_ramped_a", "int_d_v", "int_q_v", "fw_state", "fw_flags", "loop_snapshot_coherent", "vd_alloc_v", "vq_alloc_v",
               "theta_park_deg", "theta_output_deg", "voltage_advance_us", "current_sample_age_us", "pwm_ccr_a", "pwm_ccr_b", "pwm_ccr_c", "pwm_arr", "pwm_ccen", "pwm_bkdt", 'adc_raw_a_tick', 'adc_raw_b_tick', 'adc_raw_c_tick', 'ia_tick_ma', 'ib_tick_ma', 'ic_tick_ma', 'encoder_raw_tick', 'angle_alignment_us', 'adc_seq_lo', 'adc_offset_a_tick', 'adc_offset_b_tick', 'adc_offset_c_tick', 'ff_d_v', 'ff_q_v', 'kcl_tick_ma', 'id_decouple_a', 'iq_decouple_a', 'adc_seq_hi']


def _s16(v):
    return v - 0x10000 if v >= 0x8000 else v


class RunRecorder(object):
    def __init__(self, link, log_dir, request_feedback):
        self.link = link
        self.request_feedback = request_feedback    # callable: queue one CONTROL_FEEDBACK request
        self.log_dir = log_dir
        self._lock = threading.RLock()
        self._thread = None
        self._meta = None
        self._t0 = 0.0
        self.last_dir = None
        self._mode = 0
        self._generation = 0

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
            self._generation = getattr(self.link,"max_run_generation",0)
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
                                 bytes([0x0D, 4, bank, 0, 0]), (0x91, 0x82), attempts=1, priority=2)
            if reply is None or len(reply) != 8 or reply[0] != 0x91 or reply[1] != bank:
                return None
            words.extend(reply[i] | (reply[i + 1] << 8) for i in (2, 4, 6))
        return {"flags": words[2], "theta_deg": words[3] / 10.0,
                "speed_ref_rpm": _s16(words[4]), "speed_integral_a": _s16(words[5]) / 1000.0}

    def _speed_words(self):
        """bank 20: speed_ref_rpm, speed_integral_a (modes 2/3/6)."""
        link = self.link
        reply = link.request("REC_SPEED", link.param_host_id, link.param_dev_id,
                             bytes([0x0D, 4, 20, 0, 0]), (0x91, 0x82), attempts=1, priority=2)
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
        """banks 24..26: vd/vq demand, omega_e, id/iq (same tick), iq command, integrators, state/flags; coherent on 0x0A2D+."""
        link = self.link
        words = []
        version = (link.info.get("info") or {}).get("version") or 0
        for bank in ((24,25,26,29,30,31,32,33,34,35,36,37,38) if version >= 0x0A3B else (24,25,26,29,30,31,32) if version >= 0x0A3A else (24,25,26,29,30) if version >= 0x0A39 else (24,25,26)):
            reply = link.request("REC_LOOP", link.param_host_id, link.param_dev_id,
                                 bytes([0x0D, 4, bank, 0, 0]), (0x91, 0x82), attempts=1, priority=2)
            if reply is None or len(reply) != 8 or reply[0] != 0x91 or reply[1] != bank:
                return None
            words.extend(reply[i] | (reply[i + 1] << 8) for i in (2, 4, 6))
        result = {"vd_demand_v": _s16(words[0]) / 1000.0, "vq_demand_v": _s16(words[1]) / 1000.0,
                "omega_e_rad_s": _s16(words[2]), "id_inst_a": _s16(words[3]) / 1000.0,
                "iq_inst_a": _s16(words[4]) / 1000.0, "iq_cmd_ramped_a": _s16(words[5]) / 1000.0,
                "int_d_v": _s16(words[6]) / 1000.0, "int_q_v": _s16(words[7]) / 1000.0,
                "fw_state": words[8] >> 8, "fw_flags": words[8] & 0xFF}
        if len(words)>=15:
            result.update(vd_alloc_v=_s16(words[9])/1000, vq_alloc_v=_s16(words[10])/1000,
                theta_park_deg=words[11]/10,theta_output_deg=words[12]/10,
                voltage_advance_us=words[13],current_sample_age_us=words[14])
        if len(words)>=21:
            result.update(pwm_ccr_a=words[15],pwm_ccr_b=words[16],pwm_ccr_c=words[17],
                          pwm_arr=words[18],pwm_ccen=words[19],pwm_bkdt=words[20])
        if len(words)>=39:
            result.update(zip(['adc_raw_a_tick', 'adc_raw_b_tick', 'adc_raw_c_tick', 'ia_tick_ma', 'ib_tick_ma', 'ic_tick_ma', 'encoder_raw_tick', 'angle_alignment_us', 'adc_seq_lo', 'adc_offset_a_tick', 'adc_offset_b_tick', 'adc_offset_c_tick', 'ff_d_v', 'ff_q_v', 'kcl_tick_ma', 'id_decouple_a', 'iq_decouple_a', 'adc_seq_hi'],words[21:39]))
            for k in ('ia_tick_ma','ib_tick_ma','ic_tick_ma','angle_alignment_us','kcl_tick_ma'):
                result[k]=_s16(result[k])
            for k in ('ff_d_v','ff_q_v','id_decouple_a','iq_decouple_a'):
                result[k]=_s16(result[k])/1000
        return result

    def _tracking_summary(self, folder):
        version = (self.link.info.get("info") or {}).get("version") or 0
        if self._mode != 5 or version < 0x0A37 or self.link._stop:
            return
        words=[]
        for bank in (27,28):
            reply=self.link.request("REC_TRACKING",self.link.param_host_id,self.link.param_dev_id,
                bytes([0x0D,4,bank,0,0]),(0x91,0x82),attempts=1,priority=2)
            if reply is None or len(reply)!=8 or reply[0]!=0x91 or reply[1]!=bank:
                self.event("电流启动统计不可用；不能据此判定电流跟踪")
                return
            words.extend(reply[i]|reply[i+1]<<8 for i in (2,4,6))
        target_a = ((words[5] >> 8) or None) if version >= 0x0A39 else 4.0
        result={"source":"MCU 10 kHz current-loop samples", "target_abs_a":target_a,
                "window_policy": "first continuous unsaturated window" if version >= 0x0A39 else "first startup window",
                "samples":words[0], "window_ms":words[0]*.1,
                "iq_mean_a":_s16(words[1])/1000 if words[0] else None,
                "iq_min_a":_s16(words[2])/1000 if words[0] else None,
                "iq_max_a":_s16(words[3])/1000 if words[0] else None,
                "id_abs_max_a":words[4]/1000 if words[0] else None,
                "ended_at_voltage_saturation":bool(words[5]&1),"window_complete":bool(words[5]&2)}
        import json
        with open(os.path.join(folder,"current_tracking.json"),"w",encoding="utf-8") as f:
            json.dump(result,f,ensure_ascii=False,indent=2)
        self.event("电流非饱和启动窗口："+json.dumps(result,ensure_ascii=False))

    def _run(self, folder, seconds):
        link = self.link
        t0 = self._t0
        enc_seq = 0
        seen_active = False
        max_file = None
        last_max_id = None
        idle_since = None
        try:
            if self._mode==7:max_file=open(os.path.join(folder,"maximum_windows.jsonl"),"w",encoding="utf-8")
            with open(os.path.join(folder, "run.csv"), "w", newline="", encoding="utf-8") as rf, \
                 open(os.path.join(folder, "enc.csv"), "w", newline="", encoding="utf-8") as ef:
                rw = csv.writer(rf)
                ew = csv.writer(ef)
                rw.writerow(RUN_COLUMNS)
                ew.writerow(["host_time", "raw", "valid"])
                while not link._stop:
                    cycle = time.time()
                    if max_file:
                        try:
                            with link.state_lock:maximum=dict(getattr(link,'max_output_snapshot',None) or {})
                            if not maximum:raise ValueError('等待首个 MCU 统计窗口')
                            key=(maximum['mcuTimeUs'],maximum['windowId'])
                            if key!=last_max_id:
                                maximum['previousWindowId']=last_max_id[1] if last_max_id else None
                                maximum['unreadWindows']=max(0,((maximum['windowId']-last_max_id[1])&65535)-1) if last_max_id else None
                                max_file.write(json.dumps(maximum,ensure_ascii=False)+"\n");max_file.flush()
                                last_max_id=key
                        except ValueError as exc:
                            self.event("最大输出诊断读取失败："+str(exc))

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
                        loop.get("int_d_v"), loop.get("int_q_v"), loop.get("fw_state"), loop.get("fw_flags"),
                        int(bool(loop) and (link.info.get("info", {}).get("version", 0) or 0) >= 0x0A2D),
                        loop.get("vd_alloc_v"),loop.get("vq_alloc_v"),loop.get("theta_park_deg"),
                        loop.get("theta_output_deg"),loop.get("voltage_advance_us"),loop.get("current_sample_age_us"),loop.get("pwm_ccr_a"),loop.get("pwm_ccr_b"),
                        loop.get("pwm_ccr_c"),loop.get("pwm_arr"),loop.get("pwm_ccen"),loop.get("pwm_bkdt"),
                        loop.get('adc_raw_a_tick'), loop.get('adc_raw_b_tick'), loop.get('adc_raw_c_tick'), loop.get('ia_tick_ma'), loop.get('ib_tick_ma'), loop.get('ic_tick_ma'), loop.get('encoder_raw_tick'), loop.get('angle_alignment_us'), loop.get('adc_seq_lo'), loop.get('adc_offset_a_tick'), loop.get('adc_offset_b_tick'), loop.get('adc_offset_c_tick'), loop.get('ff_d_v'), loop.get('ff_q_v'), loop.get('kcl_tick_ma'), loop.get('id_decouple_a'), loop.get('iq_decouple_a'), loop.get('adc_seq_hi')])
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
            if max_file:
                max_file.close()
                threading.Thread(target=max_output.export_trace,args=(link,folder,self._generation),
                    name="foc-maximum-trace",daemon=True).start()
            try:
                self._tracking_summary(folder)
            except Exception as exc:
                self.event("4A启动统计读取失败："+str(exc))
            with self._lock:
                if self._meta is not None:
                    try:
                        self._meta.write("[t=%7.2fs] 记录结束\n" % (time.time() - t0))
                        self._meta.close()
                    except (OSError, ValueError):
                        pass
                self._meta = None
