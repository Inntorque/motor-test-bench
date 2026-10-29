"""Disk-backed, source-timestamped acquisition; contains no actuator commands."""
import csv
import queue
import json
import threading
import time
from specialized_tests import Stats, finite
from workbench.subjects import SETUP_COLUMNS, setup_columns


FIELDS = ['elapsed_s', 'recording_elapsed_s', 'source_recording_elapsed_s', 'pc_recorded_unix_s', 'phase', 'source',
          'source_received_unix_s', 'sensor_sequence', 'torque_Nm', 'sensor_speed_rpm',
          'shaft_power_W', 'commanded_iq_A', 'reported_iq_reference_A', 'actual_iq_A',
          'actual_id_A', 'rotor_position_deg', 'output_position_deg', 'rotor_speed_rpm',
          'output_speed_rpm', 'motor_ntc_C', 'motor_output_on', 'motor_mode', 'motor_fault',
          'supply_target_voltage_V', 'supply_target_current_A', 'supply_actual_voltage_V',
          'supply_actual_current_A', 'supply_actual_power_W', 'supply_output_on',
          'supply_max_voltage_V', 'supply_max_current_A', 'brake_target_torque_Nm',
          'brake_feedback_torque_Nm', 'brake_command_current_A', 'payload_json', 'motor_name', 'driver_board_name', *SETUP_COLUMNS]


class LoadedTimeline:
    def __init__(self, directory, source, frames, ratio, configuration, event_hub=None):
        self.source, self.frames, self.ratio = source, frames, ratio
        self.recording_id=configuration.get('recordingId')
        subject = configuration.get('testSubject') or {}
        self.subject_names = {'motor_name': (subject.get('motor') or {}).get('name', ''),
                              'driver_board_name': (subject.get('driverBoard') or {}).get('name', ''), **setup_columns(subject)}
        self.event_hub=event_hub;self.incoming=queue.Queue(maxsize=4096);self.unsubscribe=None
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.thread = None
        self.began = time.monotonic()
        self.recording_at = self.recording_clock = self.measurement_end = None
        self.cursor = 0
        self.phase = 'preparation'
        self.error = ''
        self.counts = {}
        self.stats = {key: Stats() for key in ('torqueNm', 'speedRpm', 'shaftPowerW', 'actualIqA', 'temperatureC')}
        self.csv_file = (directory/'timeline.csv').open('w', newline='', encoding='utf-8-sig')
        try:
            self.json_file = (directory/'timeline.jsonl').open('w', encoding='utf-8')
        except Exception:
            self.csv_file.close()
            raise
        self.writer = csv.DictWriter(self.csv_file, fieldnames=FIELDS)
        self.writer.writeheader()
        self.event('configuration', configuration)

    def event(self, source, payload, stamp=None, **values):
        # Per-source rows leave unrelated channels empty. No interpolation or future-value fill.
        if source in ('torque_feedback','torque_loading_start','torque_target_step'):
            values.update(brake_target_torque_Nm=payload.get('targetNm'),
                          brake_feedback_torque_Nm=payload.get('measuredNm'),
                          brake_command_current_A=payload.get('currentA',payload.get('brakeCurrentA')))
        with self.lock:
            now = time.monotonic()
            row = dict(elapsed_s=now-self.began,
                       recording_elapsed_s=None if self.recording_clock is None else now-self.recording_clock,
                       source_recording_elapsed_s=stamp-self.recording_at if finite(stamp) and self.recording_at is not None else None,
                       pc_recorded_unix_s=time.time(), phase=self.phase, source=source,
                       source_received_unix_s=stamp, **self.subject_names, **values)
            encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            self.json_file.write(json.dumps({**row,'recordSchema':2,'recordingId':self.recording_id,'payload':payload}, ensure_ascii=False,
                                           separators=(',', ':'), allow_nan=False)+'\n')
            self.writer.writerow({**row, 'payload_json': encoded})
            self.counts[source] = self.counts.get(source, 0)+1

    def start_recording(self):
        packet = self.frames(0)
        with self.lock:
            self.sensor_config = (self.source()['sensor'].get('config') or {}).copy()
            self.cursor = packet['frames'][-1][0] if packet and packet.get('frames') else 0
            self.recording_at = time.time()
            self.recording_clock = time.monotonic()
            self.phase = 'recording'
            self.event('recording_start', {'recordingStartedAt': self.recording_at})
        if self.event_hub:
            def receive(event):
                if event['type']!='acquisition':return
                try:self.incoming.put_nowait(event['data'])
                except queue.Full:self.error='记录事件队列已满，存在数据缺口'
            self.unsubscribe=self.event_hub.subscribe(receive)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def end_measurement(self):
        with self.lock:
            if self.measurement_end is None:
                self.measurement_end = time.time()
                self.phase = 'stopping'
                self.event('recording_end', {'recordingEndedAt': self.measurement_end})

    def _run(self):
        previous = {}
        last_flush = time.monotonic()
        try:
            while True:
                packet=None
                if self.event_hub:
                    try:event=self.incoming.get(timeout=.1)
                    except queue.Empty:
                        if self.cancel.is_set():break
                        continue
                    sources=event['sources'];packet=event['packet']
                else:sources=self.source()
                for key, channel in sources.items():
                    snapshot = channel.get('snapshot') or {}
                    stamp = snapshot.get('receivedAt')
                    signature = (channel.get('connected'), stamp, channel.get('error'))
                    if key == 'motor':
                        run = channel.get('run') or {}
                        signature += (run.get('targetA'), run.get('steps'), run.get('phase'))
                    if previous.get(key) == signature:
                        continue
                    previous[key] = signature
                    values = {}
                    if key == 'motor':
                        feedback, motor, ntc = (snapshot.get(name) or {} for name in ('feedback', 'motor', 'ntc'))
                        values = dict(commanded_iq_A=(channel.get('run') or {}).get('targetA'),
                                      reported_iq_reference_A=feedback.get('iq_reference_a'),
                                      actual_iq_A=feedback.get('iq_a'), actual_id_A=feedback.get('id_a'),
                                      rotor_position_deg=feedback.get('position_deg'), rotor_speed_rpm=feedback.get('speed_rpm'),
                                      output_position_deg=feedback['position_deg']/self.ratio if finite(feedback.get('position_deg')) and finite(self.ratio) and self.ratio>0 else None,
                                      output_speed_rpm=feedback['speed_rpm']/self.ratio if finite(feedback.get('speed_rpm')) and finite(self.ratio) and self.ratio>0 else None,
                                      motor_ntc_C=ntc.get('temp_c'), motor_output_on=motor.get('output_on'),
                                      motor_mode=motor.get('mode'), motor_fault=motor.get('fault'))
                        with self.lock:
                            if self.measurement_end is None and finite(stamp) and stamp >= self.recording_at:
                                self.stats['actualIqA'].add(feedback.get('iq_a'))
                                self.stats['temperatureC'].add(ntc.get('temp_c'))
                    elif key in ('motorSupply', 'brake') and snapshot:
                        config = snapshot.get('config') or channel.get('config') or {}
                        measured, settings = snapshot.get('measured') or [], snapshot.get('settings') or []
                        vscale, ascale = 10**config.get('voltageDecimals', 2), 10**config.get('currentDecimals', 2)
                        actual_ascale = 10**config.get('measuredCurrentDecimals', config.get('currentDecimals',2))
                        values = dict(supply_target_voltage_V=settings[1]/vscale if len(settings)>1 else None,
                                      supply_target_current_A=settings[2]/ascale if len(settings)>2 else None,
                                      supply_actual_voltage_V=measured[0]/vscale if measured else None,
                                      supply_actual_current_A=measured[1]/actual_ascale if len(measured)>1 else None,
                                      supply_actual_power_W=snapshot.get('calculatedPowerW') if snapshot.get('calculatedPowerW') is not None else measured[2] if len(measured)>2 else None,
                                      supply_output_on=bool(snapshot['status'] & 128) if isinstance(snapshot.get('status'), int) else None,
                                      supply_max_voltage_V=config.get('maxVoltage'), supply_max_current_A=config.get('maxCurrent'))
                    self.event(key, channel, stamp, **values)
                if not self.event_hub:packet = self.frames(self.cursor)
                if packet is not None:
                    if packet.get('reset') or packet.get('dropped'):
                        raise ValueError('完整时间序列检测到扭矩序号重置或缓存溢出')
                    if (packet.get('config') or {}) != self.sensor_config:
                        raise ValueError('记录期间扭矩通信参数改变')
                    for sequence, stamp, raw_torque, raw_speed in packet.get('frames', []):
                        if sequence<=self.cursor:continue
                        if self.cursor and sequence != self.cursor+1:
                            raise ValueError('完整时间序列扭矩帧不连续')
                        self.cursor = sequence
                        if stamp < self.recording_at:
                            continue
                        torque = raw_torque/10**self.sensor_config.get('torqueDecimals', 2)
                        speed = raw_speed/10**self.sensor_config.get('speedDecimals', 1)
                        power = abs(torque*speed)/9550*1000
                        self.event('sensor_frame', {'rawTorque': raw_torque, 'rawSpeed': raw_speed}, stamp,
                                   sensor_sequence=sequence, torque_Nm=torque, sensor_speed_rpm=speed, shaft_power_W=power)
                        with self.lock:
                            if self.measurement_end is None or stamp <= self.measurement_end:
                                for key, value in (('torqueNm', torque), ('speedRpm', speed), ('shaftPowerW', power)):
                                    self.stats[key].add(value)
                else:
                    # Modbus has no high-rate frame stream; retain each genuinely new snapshot.
                    sensor = sources['sensor'].get('snapshot') or {}
                    stamp = sensor.get('receivedAt')
                    if finite(stamp) and stamp >= self.recording_at and stamp != previous.get('sensor_sample'):
                        previous['sensor_sample'] = stamp
                        torque, speed = sensor.get('torque'), sensor.get('speed')
                        if finite(torque) and finite(speed):
                            power = abs(torque*speed)/9550*1000
                            self.event('sensor_frame', sensor, stamp, torque_Nm=torque, sensor_speed_rpm=speed, shaft_power_W=power)
                            with self.lock:
                                if self.measurement_end is None or stamp <= self.measurement_end:
                                    for key, value in (('torqueNm', torque), ('speedRpm', speed), ('shaftPowerW', power)):
                                        self.stats[key].add(value)
                if time.monotonic()-last_flush >= .5:
                    with self.lock:
                        self.csv_file.flush(); self.json_file.flush()
                    last_flush = time.monotonic()
                if self.cancel.is_set():
                    break
                self.cancel.wait(.02)
        except Exception as error:
            self.error = str(error)
            try:
                self.event('acquisition_error', {'error': self.error})
            except Exception:
                pass

    def status(self):
        with self.lock:
            return {'counts': dict(self.counts), 'error': self.error, 'recordingStartedAt': self.recording_at,
                    'recordingEndedAt': self.measurement_end, 'statistics': {key: stat.result() for key, stat in self.stats.items()}}

    def close(self):
        if self.unsubscribe:self.unsubscribe();self.unsubscribe=None
        self.cancel.set()
        if self.thread:
            self.thread.join(3)
            if self.thread.is_alive():
                raise ValueError('完整时间序列采集线程未结束')
        with self.lock:
            try:
                self.event('session_end', self.status())
            finally:
                self.csv_file.close(); self.json_file.close()
        if self.error:
            raise ValueError(self.error)
