"""Offline fixed-current lifecycle checks; no hardware transports are opened."""
import csv
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from automatic_tests import AutomaticTests
from loaded_timeline import LoadedTimeline
from specialized_tests import SpecializedTests
from test_automatic_tests import ScriptMotor


class FixedLoadedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.motor = ScriptMotor()
        self.begin = time.time()
        self.supply_online = True
        self.drop = False
        self.frame_config = {'protocol': 'hex6', 'torqueDecimals': 2, 'speedDecimals': 1}
        self.measurements = SpecializedTests(Path(self.temp.name)/'passive', self.source, self.frames)
        self.auto = AutomaticTests(Path(self.temp.name)/'auto', self.motor, self.measurements, self.source)

    def tearDown(self):
        if self.auto.active():
            self.auto.stop(self.auto.status()['id'])
        self.temp.cleanup()

    def source(self):
        stamp = time.time()
        def supply():
            return {'connected': self.supply_online, 'config': {'protocol': 'ssj'},
                    'snapshot': {'receivedAt': stamp, 'status': 128, 'settings': [1, 2400, 300],
                                 'measured': [2350, 200, 47], 'config': {'voltageDecimals': 2, 'currentDecimals': 2},
                                 'extraFutureParameter': 123}}
        return {'motor': self.motor.status(), 'brake': supply(), 'motorSupply': supply(),
                'sensor': {'connected': True, 'config': self.frame_config,
                           'snapshot': {'receivedAt': stamp, 'torque': 1.2, 'speed': 5}}}

    def frames(self, cursor):
        count = max(1, int((time.time()-self.begin)*1000))
        return {'frames': [(seq, self.begin+seq/1000, 120, 50) for seq in range(cursor+1, count+1)],
                'config': self.frame_config, 'reset': False,
                'dropped': 1 if self.drop and self.auto.status().get('recordingStartedAt') else 0}

    def config(self, **values):
        return dict(group='loaded', loadedMode='hold', loadConfirmed=True, targetIqA=.5,
                    durationS=1, settleS=0, **values)

    def finish(self):
        until = time.monotonic()+8
        while self.auto.thread.is_alive() and time.monotonic()<until:
            self.motor.lease = time.monotonic()
            self.auto.thread.join(.05)
        self.assertFalse(self.auto.thread.is_alive())
        self.assertFalse(self.motor.enabled)
        self.assertFalse(self.motor.run['running'])
        return self.auto.status()

    def test_missing_supply_or_excess_current_never_starts(self):
        self.supply_online = False
        with self.assertRaises(ValueError):
            self.auto.start(self.config())
        self.assertEqual(self.motor.writes, [])
        self.supply_online = True
        with self.assertRaises(ValueError):
            self.auto.start(self.config() | {'targetIqA': 1.1})
        self.assertEqual(self.motor.writes, [])

    def test_loaded_speed_uses_rotor_units_and_preserves_firmware_settings(self):
        self.auto.start(self.config() | {'loadedMode':'speed_hold','targetSpeedRpm':1.5})
        report=self.finish()
        self.assertEqual(report['outcome'],'duration_complete')
        self.assertTrue(report['stopConfirmed'])
        self.assertEqual(report['steps'][0]['targetSpeedRpm'],1.5)
        self.assertTrue(any('mode=2' in route for route in self.motor.writes))
        self.assertTrue(any('field=speed_rpm&value=15.0' in route for route in self.motor.writes))
        self.assertFalse(any('/api/set?' in route for route in self.motor.writes))
        self.assertTrue(all('value=0' in route for route in self.motor.writes if 'field=current_a' in route))
        self.assertGreater(report['timeline']['counts']['sensor_frame'],500)

    def test_loaded_speed_invalid_targets_cannot_enable(self):
        for target in (0,.01,-.01,float('nan'),float('inf'),2.01,-2.01):
            with self.assertRaises(ValueError):
                self.auto.start(self.config() | {'loadedMode':'speed_hold','targetSpeedRpm':target})
        self.supply_online=False
        with self.assertRaises(ValueError):
            self.auto.start(self.config() | {'loadedMode':'speed_hold','targetSpeedRpm':1})
        self.assertEqual(self.motor.writes,[])

    def test_reverse_speed_preserves_sign_and_same_stop_guards(self):
        self.auto.start(self.config() | {'loadedMode':'speed_hold','targetSpeedRpm':-1.5})
        report=self.finish()
        self.assertEqual(report['outcome'],'duration_complete')
        self.assertTrue(report['stopConfirmed'])
        self.assertEqual(report['steps'][0]['targetSpeedRpm'],-1.5)
        self.assertTrue(any('field=speed_rpm&value=-15.0' in route for route in self.motor.writes))
        self.assertFalse(any('/api/set?' in route for route in self.motor.writes))
        self.assertLessEqual(report['peakOutputSpeedRpm'],report['controllerSpeedCeilingRpm'])

    def test_constant_current_records_every_source_and_unknown_fields_to_disk(self):
        record = self.auto.start(self.config())
        report = self.finish()
        self.assertEqual(report['outcome'], 'duration_complete')
        self.assertEqual(report['state'], 'completed')
        self.assertTrue(report['stopConfirmed'])
        self.assertFalse(self.motor.run['acquisitionManaged'])
        self.assertGreater(report['timeline']['counts']['sensor_frame'], 500)
        self.assertAlmostEqual(report['measuredPeakTorqueNm'], 1.2)
        self.assertGreaterEqual(report['recordingEndedAt']-report['recordingStartedAt'], 1)
        with self.auto.file(record['id'], 'timeline.csv').open(encoding='utf-8-sig') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([float(row['elapsed_s']) for row in rows], sorted(float(row['elapsed_s']) for row in rows))
        sources = {row['source'] for row in rows}
        self.assertTrue({'motor', 'brake', 'motorSupply', 'sensor_frame', 'command_ack', 'motor_stop_confirmed'} <= sources)
        supply = next(row for row in rows if row['source']=='motorSupply')
        self.assertEqual(float(supply['supply_target_voltage_V']), 24)
        self.assertEqual(float(supply['supply_actual_current_A']), 2)
        self.assertEqual(json.loads(supply['payload_json'])['snapshot']['extraFutureParameter'], 123)
        native = [row for row in rows if row['source']=='sensor_frame']
        self.assertTrue(all(row['actual_iq_A']=='' for row in native))
        self.assertTrue(any(row['phase']=='post_stop' for row in native))
        self.assertAlmostEqual(float(native[1]['source_recording_elapsed_s'])-float(native[0]['source_recording_elapsed_s']), .001, places=5)
        sequence = [int(row['sensor_sequence']) for row in native]
        self.assertEqual(sequence, list(range(sequence[0], sequence[-1]+1)))
        with self.auto.file(record['id'], 'timeline.jsonl').open() as stream:
            events = [json.loads(line) for line in stream]
        commands = [event['payload']['route'] for event in events if event['source']=='command_ack']
        self.assertEqual(sum('field=current_a&value=0.5' in route for route in commands), 1)
        self.assertTrue(any('enable=1&mode=5' in route for route in commands))

    def test_frame_overflow_stops_motor_and_preserves_partial_data(self):
        self.auto.start(self.config())
        until = time.monotonic()+3
        while self.auto.active() and not self.auto.status().get('recordingStartedAt') and time.monotonic()<until:
            self.motor.lease = time.monotonic()
            time.sleep(.02)
        self.drop = True
        report = self.finish()
        self.assertEqual(report['state'], 'interrupted')
        self.assertIn('缓存溢出', report['timeline']['error'])
        self.assertTrue(report['stopConfirmed'])

    def test_recording_disk_failure_still_confirms_motor_stop(self):
        original = LoadedTimeline.event
        def fail_recording(instance, source, *args, **kwargs):
            if source=='recording_start':
                raise OSError('simulated disk full')
            return original(instance, source, *args, **kwargs)
        with patch.object(LoadedTimeline, 'event', fail_recording):
            self.auto.start(self.config())
            report = self.finish()
        self.assertEqual(report['state'], 'interrupted')
        self.assertIn('simulated disk full', report['reason'])
        self.assertTrue(report['stopConfirmed'])

    def test_protection_is_logged_before_stop_overwrites_feedback(self):
        record = self.auto.start(self.config() | {'durationS': 3})
        until = time.monotonic()+3
        while not self.auto.status().get('recordingStartedAt') and time.monotonic()<until:
            self.motor.lease = time.monotonic()
            time.sleep(.02)
        time.sleep(.05)
        self.motor.fault_at = .5
        report = self.finish()
        self.assertEqual(report['outcome'], 'protection')
        self.assertTrue(report['protectionSeen'])
        self.assertTrue(report['stopConfirmed'])
        with self.auto.file(record['id'], 'timeline.jsonl').open() as stream:
            events = [json.loads(line) for line in stream]
        observed = next(event for event in events if event['source']=='terminal_observation')
        self.assertTrue(observed['payload']['sources']['motor']['snapshot']['motor']['fault'])

    def test_supply_disconnect_interrupts_recording_and_stops(self):
        self.auto.start(self.config() | {'durationS': 3})
        until = time.monotonic()+3
        while not self.auto.status().get('recordingStartedAt') and time.monotonic()<until:
            self.motor.lease = time.monotonic()
            time.sleep(.02)
        self.supply_online = False
        report = self.finish()
        self.assertEqual(report['state'], 'interrupted')
        self.assertTrue(report['stopConfirmed'])
        self.assertIn('制动电源', report['reason'])


if __name__ == '__main__':
    unittest.main()
