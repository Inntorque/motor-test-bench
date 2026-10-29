"""Source-separated CSV/JSONL capture used only by the specialized suite."""
import csv
import json
import math
import time
from collections import deque
from .specialized_metrics import finite, stats, performance, thermal
from .subjects import SETUP_COLUMNS, setup_columns

FIELDS = ['t_s', 'source', 'source_unix_s', 'phase', 'target', 'sensor_sequence',
          'torque_Nm', 'speed_rpm', 'power_W', 'iq_A', 'id_A', 'position_deg', 'temperature_C',
          'visit_id', 'target_deg', 'reference_deg', 'motor_name', 'driver_board_name', 'payload_json',
          'rotor_position_deg', 'rotor_speed_rpm', *SETUP_COLUMNS]


class Capture:
    def __init__(self, directory, source, frames, config, subject):
        self.source, self.frames, self.config = source, frames, config
        self.began = time.monotonic()
        self.started_unix = time.time()
        self.csv_file = (directory/'data.csv').open('w', newline='', encoding='utf-8-sig')
        try:
            self.json_file = (directory/'timeline.jsonl').open('w', encoding='utf-8')
        except Exception:
            self.csv_file.close()
            raise
        self.writer = csv.DictWriter(self.csv_file, fieldnames=FIELDS)
        self.writer.writeheader()
        self.names = {'motor_name': (subject.get('motor') or {}).get('name', ''),
                      'driver_board_name': (subject.get('driverBoard') or {}).get('name', ''), **setup_columns(subject)}
        self.stamps = {}; self.cursor = 0; self.sensor_config = None; self.last_temp = None
        self.windows = []; self.temperatures = deque(); self.preview = []; self.issues = []
        self.thermal_preview = []; self.thermal_totals = {}; self.best_windows = {}; self.kt_windows = {}
        self.valid_window_count = 0; self.steady_window_count = 0
        self.peak = None; self.speed_peak = None; self.counts = {}; self.bucket = []; self.bucket_began = 0
        self.phase = None; self.target = None; self.last_motor = None; self.last_sensor = None
        self.bucket_gap = False
        try:
            packet = frames(0)
            if packet and packet.get('frames'):
                self.cursor = packet['frames'][-1][0]
            self.write('configuration', None, config)
            self.poll('baseline')
        except Exception:
            self.csv_file.close(); self.json_file.close()
            raise

    def write(self, source, stamp, payload, **values):
        row = {'t_s': time.monotonic()-self.began, 'source': source, 'source_unix_s': stamp,
               'phase': self.phase, 'target': self.target, **self.names, **values}
        self.writer.writerow({**row, 'payload_json': json.dumps(payload, ensure_ascii=False, allow_nan=False)})
        self.json_file.write(json.dumps({**row, 'payload': payload}, ensure_ascii=False, allow_nan=False)+'\n')
        self.counts[source] = self.counts.get(source, 0)+1

    def poll(self, phase, target=None):
        elapsed = time.monotonic()-self.began
        packet = self.frames(self.cursor)
        if self.phase != phase or self.target != target:
            self.flush_window(elapsed)
            self.phase, self.target = phase, target
        source = self.source()
        for key, channel in source.items():
            snapshot = channel.get('snapshot') or {}
            stamp = snapshot.get('receivedAt')
            if not channel.get('connected') or not finite(stamp) or time.time()-stamp > 1.5:
                continue
            if key == 'sensor' and packet is not None:
                continue
            if self.stamps.get(key) == stamp:
                continue
            self.stamps[key] = stamp
            if key == 'motor':
                feedback = snapshot.get('feedback') or {}; ntc = snapshot.get('ntc') or {}
                ratio = self.config['ratio']
                self.last_motor = {'stamp': stamp, 'iq': feedback.get('iq_a'), 'id': feedback.get('id_a'),
                                   'position': feedback.get('position_deg')/ratio if finite(feedback.get('position_deg')) else None,
                                   'speed': feedback.get('speed_rpm')/ratio if finite(feedback.get('speed_rpm')) else None}
                self.bucket.append({'kind': 'motor', **self.last_motor})
                self.write('motor', stamp, channel, iq_A=feedback.get('iq_a'), id_A=feedback.get('id_a'),
                           speed_rpm=self.last_motor['speed'], position_deg=self.last_motor['position'],
                           rotor_position_deg=feedback.get('position_deg'), rotor_speed_rpm=feedback.get('speed_rpm'))
                temp_stamp = ntc.get('sourceDeviceTimeUs') or snapshot.get('feedback', {}).get('deviceTimeUs') or stamp
                if ntc.get('valid') and finite(ntc.get('temp_c')) and ntc.get('age_s', 0) <= 1.5 and temp_stamp != self.last_temp:
                    self.last_temp = temp_stamp
                    point = {'t': elapsed, 'temperatureC': ntc['temp_c'], 'phase': phase,
                             'iq': feedback.get('iq_a'), 'speedRpm': self.last_motor['speed'],
                             'torqueNm': self.last_sensor.get('torque') if self.last_sensor and time.time()-self.last_sensor['stamp']<=1.5 else None}
                    self.temperatures.append(point)
                    while self.temperatures and self.temperatures[0]['t'] < elapsed-self.config.get('thermalWindowS',600)*1.1:
                        self.temperatures.popleft()
                    self.thermal_preview.append(point)
                    if len(self.thermal_preview)>1000: self.thermal_preview=self.thermal_preview[::2]
                    totals=self.thermal_totals
                    totals.setdefault('initialC',ntc['temp_c']);totals['lastC']=ntc['temp_c']
                    totals['maxC']=max(totals.get('maxC',ntc['temp_c']),ntc['temp_c']);totals['count']=totals.get('count',0)+1
                    self.write('temperature', stamp, ntc, temperature_C=ntc['temp_c'])
            elif key == 'sensor':
                self.sensor(stamp, snapshot.get('torque'), snapshot.get('speed'), None, channel)
            else:
                self.write(key, stamp, channel)
        if packet is not None:
            config = packet.get('config') or {}
            if packet.get('dropped') or packet.get('reset') or self.sensor_config is not None and config != self.sensor_config:
                if 'sensor_stream_gap' not in self.issues: self.issues.append('sensor_stream_gap')
                self.bucket_gap = True
            if packet.get('reset'):self.cursor=0
            self.sensor_config = config
            for seq, stamp, torque, speed in packet.get('frames', []):
                if seq <= self.cursor: continue
                if seq != self.cursor+1:
                    self.bucket_gap=True
                    if 'sensor_stream_gap' not in self.issues:self.issues.append('sensor_stream_gap')
                self.cursor = seq
                if stamp < self.started_unix: continue
                self.sensor(stamp, torque/10**config.get('torqueDecimals', 2), speed/10**config.get('speedDecimals', 1), seq,
                            {'rawTorque': torque, 'rawSpeed': speed, 'config': config})
        if elapsed-self.bucket_began >= self.config['windowS']:
            self.flush_window(elapsed)
        return source, self.last_motor

    def sensor(self, stamp, torque, speed, seq, payload):
        if not finite(torque) or not finite(speed): return
        power = torque*speed*2*math.pi/60
        self.last_sensor = {'torque': torque, 'speed': speed, 'stamp': stamp}
        self.bucket.append({'kind': 'sensor', 'torque': torque, 'speed': speed, 'power': power, 'stamp': stamp})
        if self.speed_peak is None or abs(speed)>abs(self.speed_peak['speedRpm']):
            self.speed_peak={'speedRpm':speed,'torqueNm':torque,'sourceUnixS':stamp}
        self.write('sensor', stamp, payload, torque_Nm=torque, speed_rpm=speed, power_W=power, sensor_sequence=seq)
        if self.peak is None or abs(torque) > abs(self.peak['torqueNm']):
            self.peak = {'torqueNm': torque, 'speedRpm': speed, 'sourceUnixS': stamp, 't': time.monotonic()-self.began,
                         'bandwidthVerified': self.config.get('bandwidthVerified', False)}

    def flush_window(self, elapsed):
        if not self.bucket:
            self.bucket_began = elapsed
            return
        sensors = [v for v in self.bucket if v['kind'] == 'sensor']
        motors = [v for v in self.bucket if v['kind'] == 'motor']
        t = stats([v['torque'] for v in sensors]); n = stats([v['speed'] for v in sensors])
        iq = stats([v['iq'] for v in motors]); id_ = stats([v['id'] for v in motors])
        overlap = bool(sensors and motors and min(sensors[-1]['stamp'], motors[-1]['stamp']) > max(sensors[0]['stamp'], motors[0]['stamp']))
        valid = t['count'] >= 2 and iq['count'] >= 2 and overlap and not self.bucket_gap and elapsed-self.bucket_began >= self.config['windowS']*.9
        steady = valid and t['std'] <= max(self.config.get('torqueNoiseNm', .05), abs(t['mean'])*.05) and n['std'] <= max(1, abs(n['mean'])*.05)
        point = {'t': elapsed, 'durationS': elapsed-self.bucket_began, 'phase': self.phase, 'target': self.target,
                 'torque': t.get('mean'), 'n': n.get('mean'), 'iq': iq.get('mean'), 'id': id_.get('mean'),
                 'powerW': stats([v['power'] for v in sensors]).get('mean'), 'idStdA': id_.get('std'), 'valid': valid, 'steady': steady,
                 'sensorCount': len(sensors), 'motorCount': len(motors)}
        self.windows.append(point)
        self.valid_window_count+=int(valid);self.steady_window_count+=int(steady)
        if len(self.windows)>6000:self.windows=self.windows[::2]
        if valid:
            for key,field in [('torque','torque'),('power','powerW'),('speed','n')]:
                if key!='torque' and not steady:continue
                if key not in self.best_windows or abs(point[field])>abs(self.best_windows[key][field]):self.best_windows[key]=point
        if steady and str(self.phase).startswith('Kt') and finite(point['id']) and finite(point['idStdA']) and abs(point['n'])<=self.config.get('ktMaxSpeedRpm',5) and abs(point['id'])<=self.config.get('ktMaxIdA',.5) and point['idStdA']<=self.config.get('ktMaxIdA',.5):
            item=self.kt_windows.setdefault(self.phase,{'count':0,'iq':0,'torque':0})
            item['count']+=1
            for key in ('iq','torque'):item[key]+=(point[key]-item[key])/item['count']
        self.preview.append(point)
        if len(self.preview) > 1000: self.preview = self.preview[::2]
        self.write('window', None, point)
        self.bucket = []; self.bucket_began = elapsed;self.bucket_gap=False
        self.csv_file.flush(); self.json_file.flush()

    def status(self):
        metric=performance([*self.windows,*self.best_windows.values()], self.peak, self.config, self.issues)
        from .specialized_metrics import fit
        kt=fit([(item['iq'],item['torque']) for item in self.kt_windows.values()])
        metric.update(outputEquivalentKt=kt,ktState='identified_in_selected_windows' if kt else 'insufficient_quasistatic_current_levels')
        metric.update(validWindows=self.valid_window_count,steadyWindows=self.steady_window_count)
        metric['rawSpeedPeak']=self.speed_peak
        temperature=thermal(list(self.temperatures),self.config)
        temperature.update(self.thermal_totals)
        if self.thermal_totals:temperature['changeFromInitialK']=self.thermal_totals['lastC']-self.thermal_totals['initialC']
        candidate=None
        if temperature.get('stable'):
            point=next((w for w in reversed(self.windows) if w.get('steady') and w.get('phase')!='cooling'),None)
            if point:
                candidate={**point,'temperatureC':temperature.get('lastC'),'criterion':'stable under this recipe; not a universal motor rating',
                           'declaredSpeedRpm':self.config.get('ratedSpeedRpm'),'declaredTorqueNm':self.config.get('ratedTorqueNm')}
                for key,suffix in [('massKg','PerKg'),('volumeL','PerL')]:
                    size=self.config.get(key)
                    candidate['torque'+suffix]=abs(point['torque'])/size if size else None
                    candidate['powerW'+suffix]=abs(point['powerW'])/size if size else None
        return {'counts': dict(self.counts), 'preview': self.preview[-1000:], 'thermalPreview': self.thermal_preview[-1001:],
                'performance': metric, 'thermal': temperature, 'continuousCandidate':candidate,'qualityIssues': list(self.issues)}

    def close(self):
        try:
            self.flush_window(time.monotonic()-self.began)
        finally:
            self.csv_file.close(); self.json_file.close()
