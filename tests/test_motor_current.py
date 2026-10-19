import sys
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motor_current import CurrentMotor, CurrentError, XIGE_ROOT


class FakeMotor(CurrentMotor):
    def __init__(self, fail=None):
        super().__init__()
        self.enabled = False
        self.writes = []
        self.fail = fail
        self.calibrated = True
        self.last_positive = 0

    def _request(self, route, *, write=False, allow_cancel=False):
        if write:
            if not allow_cancel and self.cancel.is_set():
                raise CurrentError('cancelled')
            self.writes.append(route)
        parsed = urlsplit(route)
        query = parse_qs(parsed.query)
        fault = self.fail == 'fault' and self.enabled and self.last_positive > 0
        if parsed.path == '/api/status':
            return {'project_root': str(XIGE_ROOT), 'connected': True, 'version_raw': 0x0A1C,
                    'version': '0x0A1C', 'motor': {'available': True, 'age_s': 0,
                    'mode': 5, 'state': 3 if fault else 2 if self.enabled else 0,
                    'fault': fault, 'output_on': self.enabled and not fault, 'updated_at': time.time()},
                    'encoder': {'valid': True, 'stale': False, 'electrical_calibrated': self.calibrated,
                                'electrical_angle_valid': self.calibrated},
                    'ntc': {'valid': True, 'age_s': 0}}
        if parsed.path == '/api/control/status':
            return {'ok': True, 'control': {'mode': 5, 'iq_target_ma': 0},
                    'feedback': {'id_a': 0., 'iq_a': self.last_positive, 'stale': False}}
        if parsed.path == '/api/get':
            return {'ok': True, 'value': 1000 if query['id'] == ['7'] else 0}
        if parsed.path == '/api/motor':
            self.enabled = query['enable'] == ['1']
        if parsed.path == '/api/control/set':
            value = float(query['value'][0])
            if value > 0:
                if self.fail == 'write':
                    raise CurrentError('write ACK missing')
                self.last_positive = value
        return {'ok': True}

    def positive_writes(self):
        return [float(parse_qs(urlsplit(route).query)['value'][0]) for route in self.writes
                if '/api/control/set?' in route and float(parse_qs(urlsplit(route).query)['value'][0]) > 0]


class MotorCurrentTests(unittest.TestCase):
    def test_bad_cached_direction_is_reread_without_writes(self):
        motor = FakeMotor()
        request = motor._request
        repaired = False
        def reply(route, **kwargs):
            nonlocal repaired
            if route == '/api/get?id=2':
                repaired = True
                return {'value': 1}
            result = request(route, **kwargs)
            if route == '/api/status':
                result['params'] = [{'id':2, 'value':1 if repaired else 17484033}]
                result['encoder']['electrical_calibrated'] = repaired
                result['encoder']['electrical_angle_valid'] = repaired
            return result
        motor._request = reply
        snapshot = motor._refresh()
        self.assertTrue(repaired)
        self.assertEqual(snapshot['startBlockReason'], '')
        self.assertEqual(motor.writes, [])

    def test_persistently_invalid_direction_blocks_without_writes(self):
        motor = FakeMotor()
        request = motor._request
        def reply(route, **kwargs):
            if route == '/api/get?id=2': return {'value':17484033}
            result = request(route, **kwargs)
            if route == '/api/status': result['params'] = [{'id':2,'value':17484033}]
            return result
        motor._request = reply
        with self.assertRaisesRegex(CurrentError, 'ID2'):
            motor.start({'stepA':.1, 'intervalS':1, 'durationS':1})
        self.assertEqual(motor.writes, [])

    def test_stop_without_panel_connection_or_control_feedback(self):
        motor = FakeMotor()
        motor.enabled = True
        request = motor._request
        def reply(route, **kwargs):
            if route == '/api/control/status': raise CurrentError('control telemetry missing')
            return request(route, **kwargs)
        motor._request = reply
        result = motor.stop()
        self.assertFalse(motor.enabled)
        self.assertTrue(result['run']['stopConfirmed'])
        self.assertFalse(result['connected'])
        self.assertIn('反馈读取失败', result['error'])

    def test_failed_manual_stop_clears_previous_confirmation(self):
        motor = FakeMotor()
        motor.run['stopConfirmed'] = True
        request = motor._request
        def reply(route, **kwargs):
            if route == '/api/motor?enable=0': raise CurrentError('stop failed')
            return request(route, **kwargs)
        motor._request = reply
        with self.assertRaises(CurrentError): motor.stop()
        self.assertFalse(motor.run['stopConfirmed'])

    def test_idle_read_recovers_after_transient_timeout_without_output_or_implicit_first_connect(self):
        motor = FakeMotor()
        self.assertFalse(motor.read()['connected'])
        motor.connect()
        request = motor._request
        def fail_once(route, **kwargs):
            if route == '/api/control/status':
                raise CurrentError('temporary read timeout')
            return request(route, **kwargs)
        motor._request = fail_once
        self.assertFalse(motor.read()['connected'])
        motor._request = request
        motor.retry_after = 0
        recovered = motor.read()
        self.assertTrue(recovered['connected'])
        self.assertEqual(recovered['error'], '')
        self.assertEqual(motor.writes, [])
        motor.disconnect()
        self.assertFalse(motor.read()['connected'])

    def run_task(self, motor, duration=1):
        motor.connect()
        motor.start({'stepA': .4, 'intervalS': .2, 'durationS': duration})
        motor.thread.join(4)
        self.assertFalse(motor.thread.is_alive())
        self.assertFalse(motor.run['running'])
        self.assertTrue(motor.run['stopConfirmed'])
        self.assertFalse(motor.enabled)
        self.assertIn('/api/motor?enable=0', motor.writes)
        self.assertEqual(motor.writes[-1], '/api/control/set?field=current_a&value=0')

    def test_connect_is_read_only_and_calibration_blocks_start(self):
        motor = FakeMotor()
        motor.connect()
        self.assertEqual(motor.writes, [])
        motor.calibrated = False
        with self.assertRaises(CurrentError):
            motor.start({'stepA': .1, 'intervalS': 1, 'durationS': 1})
        self.assertEqual(motor.writes, [])

    def test_steps_are_bounded_by_device_limit_and_duration_stops_output(self):
        motor = FakeMotor()
        self.run_task(motor)
        self.assertEqual(motor.positive_writes(), [.4, .8, 1.0])
        self.assertTrue(motor.run['limitReached'])
        self.assertEqual(motor.run['reason'], '持续时间到期')
        self.assertTrue(all('mode=5' in route for route in motor.writes if '/api/motor?enable=1' in route))

    def test_protection_stops_without_further_steps(self):
        motor = FakeMotor('fault')
        self.run_task(motor)
        self.assertEqual(motor.positive_writes(), [.4])
        self.assertIn('保护', motor.run['reason'])

    def test_failed_write_stops_and_does_not_retry_positive_command(self):
        motor = FakeMotor('write')
        self.run_task(motor)
        self.assertEqual(len(motor.positive_writes()), 1)
        self.assertIn('ACK missing', motor.run['reason'])

    def test_lost_page_heartbeat_stops(self):
        motor = FakeMotor()
        motor.connect()
        motor.start({'stepA': .1, 'intervalS': 1, 'durationS': 10})
        motor.lease = time.monotonic() - 3
        motor.thread.join(4)
        self.assertFalse(motor.thread.is_alive())
        self.assertTrue(motor.run['stopConfirmed'])
        self.assertIn('心跳超时', motor.run['reason'])
        self.assertEqual(motor.positive_writes(), [])

    def test_user_stop_cancels_remaining_steps(self):
        motor = FakeMotor()
        motor.connect()
        motor.start({'stepA': .1, 'intervalS': .2, 'durationS': 10})
        time.sleep(.25)
        motor.stop()
        count = len(motor.positive_writes())
        motor.thread.join(4)
        self.assertEqual(len(motor.positive_writes()), count)
        self.assertTrue(motor.run['stopConfirmed'])
        self.assertFalse(motor.enabled)

    def test_unconfirmed_stop_is_reported_and_connection_is_not_claimed(self):
        class StopFailure(FakeMotor):
            def _request(self, route, **kwargs):
                if route == '/api/motor?enable=0':
                    raise CurrentError('stop ACK missing')
                return super()._request(route, **kwargs)
        motor = StopFailure('write')
        motor.connect()
        motor.start({'stepA': .1, 'intervalS': .2, 'durationS': 1})
        motor.thread.join(4)
        self.assertFalse(motor.run['stopConfirmed'])
        self.assertFalse(motor.status()['connected'])
        self.assertIn('stop ACK missing', motor.error)

    def test_invalid_inputs_never_send_power_commands(self):
        for data in ({'stepA': float('nan'), 'intervalS': 1, 'durationS': 1},
                     {'stepA': .0001, 'intervalS': 1, 'durationS': 1},
                     {'stepA': .1, 'intervalS': 100, 'durationS': 1},
                     {'stepA': .1, 'intervalS': 1, 'durationS': 0}):
            motor = FakeMotor()
            with self.assertRaises(CurrentError):
                motor.start(data)
            self.assertEqual(motor.writes, [])


if __name__ == '__main__':
    unittest.main()
