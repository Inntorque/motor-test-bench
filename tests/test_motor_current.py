import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
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
        self.version = 0x0A1C
        self.current_limit_ma = 1000

    def _request(self, route, *, write=False, allow_cancel=False):
        if write:
            if not allow_cancel and self.cancel.is_set():
                raise CurrentError('cancelled')
            self.writes.append(route)
        parsed = urlsplit(route)
        query = parse_qs(parsed.query)
        fault = self.fail == 'fault' and self.enabled and self.last_positive > 0
        if parsed.path == '/api/status':
            return {'project_root': str(XIGE_ROOT), 'connected': True, 'version_raw': self.version,
                    'version': hex(self.version), 'motor': {'available': True, 'age_s': 0,
                    'mode': 5, 'state': 3 if fault else 2 if self.enabled else 0,
                    'fault': fault, 'output_on': self.enabled and not fault, 'updated_at': time.time()},
                    'encoder': {'valid': True, 'stale': False, 'electrical_calibrated': self.calibrated,
                                'electrical_angle_valid': self.calibrated},
                    'ntc': {'valid': True, 'age_s': 0,'temp_c':25,'limit_c':50}}
        if parsed.path == '/api/control/status':
            return {'ok': True, 'control': {'mode': 5, 'iq_target_ma': 0},
                    'feedback': {'id_a': 0., 'iq_a': self.last_positive, 'stale': False}}
        if parsed.path == '/api/get':
            return {'ok': True, 'value': self.current_limit_ma if query['id'] == ['7'] else 0}
        if parsed.path == '/api/set' and query['id'] == ['7']:
            self.current_limit_ma = int(query['value'][0])
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
    def test_adc_clipping_reports_invalid_measurement_without_stale_peak(self):
        snapshot={'motor':{'state':3,'fault':True,'fault_reason':11,'fault_peak_ma':100},'ntc':{}}
        with self.assertRaisesRegex(CurrentError,'相电流采样超量程') as caught:
            CurrentMotor._healthy(snapshot)
        self.assertNotIn('锁存相电流峰值',str(caught.exception))

    def test_refresh_uses_fresh_motor_reply_instead_of_stale_precharge_cache(self):
        motor = FakeMotor()
        original = motor._request
        fresh = original('/api/status')['motor']
        fresh.update(state=2, output_on=True)
        def request(route, **kwargs):
            result = original(route, **kwargs)
            if route == '/api/status':
                result['motor'].update(state=1, age_s=2.0)
            elif route == '/api/control/status':
                result['motor'] = fresh
            return result
        with patch.object(motor, '_request', side_effect=request):
            snapshot = motor._refresh()
        self.assertEqual(snapshot['motor']['state'], 2)
        self.assertTrue(snapshot['motor']['output_on'])
        self.assertEqual(motor.writes, [])

    def test_refresh_does_not_fall_back_when_new_motor_reply_is_missing_or_stale(self):
        for bad in ({}, {'available':True, 'age_s':2.0}):
            motor = FakeMotor()
            original = motor._request
            def request(route, **kwargs):
                result = original(route, **kwargs)
                if route == '/api/control/status':
                    result['motor'] = bad
                return result
            with patch.object(motor, '_request', side_effect=request):
                with self.assertRaisesRegex(CurrentError, '电机状态反馈超时'):
                    motor._refresh()
            self.assertEqual(motor.writes, [])

    def test_full_current_applies_10a_target_with_firmware_slew_and_no_duration_expiry(self):
        motor=FakeMotor();motor.version=0x0A33;motor.current_limit_ma=5000
        clock=SimpleNamespace(now=100.)
        request=motor._refresh;observed=[];sent_at=[];io=motor._request
        def command(route,**kwargs):
            if '/api/control/set?' in route and float(parse_qs(urlsplit(route).query)['value'][0])>0:
                sent_at.append(clock.now)
            return io(route,**kwargs)
        def refresh():
            result=request()
            clock.now+=1.01
            if motor.last_positive>=10:
                clock.now+=300  # present controller past 255 s, no timed stop
                observed.append(clock.now)
                if len(observed)>=2:motor.cancel.set()
            motor.lease=clock.now
            return result
        fake_time=SimpleNamespace(monotonic=lambda:clock.now,time=time.time,sleep=time.sleep)
        with patch('motor_current.time',fake_time),patch.object(motor,'_refresh',side_effect=refresh),patch.object(motor,'_request',side_effect=command):
            motor.start({'mode':'full_current'})
            motor.thread.join(3)
        self.assertFalse(motor.thread.is_alive())
        self.assertEqual(motor.positive_writes(),[10])
        self.assertEqual(motor.current_limit_ma,30000)
        self.assertEqual(motor.run['limitA'],10)
        self.assertEqual(motor.run['deviceLimitA'],30)
        self.assertTrue(all(b-a>=1 for a,b in zip(sent_at,sent_at[1:])))
        self.assertGreaterEqual(len(observed),2)
        self.assertGreater(motor.run['elapsed'],255)
        self.assertEqual(motor.run['reason'],'用户停止')
        self.assertEqual(motor.run['deviceWatchdogS'],0)
        self.assertTrue(motor.run['stopConfirmed'])

    def test_full_current_preflight_is_read_only_and_older_firmware_keeps_its_ceiling(self):
        for version,maximum in ((0x0A10,1),(0x0A1C,5),(0x0A2B,5),(0x0A2C,10),(0x0A31,10),(0x0A33,10),(0x0A39,10)):
            motor=FakeMotor();motor.version=version
            prepared=motor.prepare({'mode':'full_current'})
            self.assertEqual(prepared,(10,1,0,maximum))
            self.assertEqual(motor.writes,[])

    def test_power_verification_holds_iq_increments_and_stop_still_cancels(self):
        motor=FakeMotor();motor.version=0x0A2C;motor.current_limit_ma=10000
        motor.output_hold=lambda:True
        motor.start({'mode':'full_current'})
        time.sleep(1.1)
        self.assertEqual(motor.positive_writes(),[])
        motor.stop();motor.thread.join(3)
        self.assertEqual(motor.positive_writes(),[])
        self.assertFalse(motor.enabled)
        self.assertTrue(motor.run['stopConfirmed'])

    def test_fault_during_full_current_ramp_cancels_next_steps_and_preserves_ntc_evidence(self):
        motor=FakeMotor('fault');motor.version=0x0A2C;motor.current_limit_ma=10000
        motor.start({'mode':'full_current'});motor.thread.join(3)
        self.assertFalse(motor.thread.is_alive())
        self.assertEqual(motor.positive_writes(),[10])
        self.assertIn('电机保护',motor.run['reason'])
        self.assertNotIn('温度',motor.run['reason'])
        self.assertEqual(motor.run['endProtection']['ntc']['temp_c'],25)
        self.assertTrue(motor.run['endProtection']['motor']['fault'])
        self.assertTrue(motor.run['stopConfirmed'])

    def test_motor_protection_with_normal_ntc_never_claims_temperature_trip(self):
        motor=FakeMotor();snapshot=motor._refresh()
        snapshot['motor'].update(state=3,fault=True)
        snapshot['ntc'].update(temp_c=24.6,limit_c=60,over=False,fault=False,tripped=False,tripped_latched=False)
        with self.assertRaises(CurrentError) as error:motor._healthy(snapshot)
        self.assertIn('电机保护',str(error.exception))
        self.assertNotIn('温度',str(error.exception));self.assertNotIn('过温',str(error.exception))

    def test_ntc_trip_sensor_fault_and_history_are_distinct_and_still_block(self):
        for flags,reason in ((dict(fault=True),'传感器故障'),
                             (dict(tripped_latched=True),'历史过温锁存'),(dict(temp_c=60),'温度保护边界')):
            snapshot=FakeMotor()._refresh();snapshot['ntc'].update(limit_c=60,**flags)
            with self.assertRaisesRegex(CurrentError,reason):CurrentMotor._healthy(snapshot)

    def test_ntc_restart_inhibit_is_not_running_trip_at_80c(self):
        snapshot=FakeMotor()._refresh()
        snapshot['ntc'].update(limit_c=80,temp_c=79,over=True)
        CurrentMotor._healthy(snapshot)
        with self.assertRaisesRegex(CurrentError,'重新启动'):
            CurrentMotor._healthy(snapshot,starting=True)
        snapshot['ntc']['temp_c']=80
        with self.assertRaisesRegex(CurrentError,'温度保护边界'):
            CurrentMotor._healthy(snapshot)

    def test_per_trial_command_ceiling_holds_without_changing_device_limit(self):
        motor=FakeMotor()
        motor.start({'stepA':.2,'intervalS':.2,'durationS':1,'maxTargetA':.3})
        motor.thread.join(3)
        self.assertEqual(motor.positive_writes(),[.2,.3])
        self.assertEqual(motor.run['limitA'],.3)
        self.assertEqual(motor.run['deviceLimitA'],1)
        self.assertTrue(motor.run['stopConfirmed'])
        self.assertFalse(motor.enabled)

    def test_invalid_trial_ceilings_never_enable_output(self):
        for maximum in (.01,1.2,6,float('nan'),.10001,''):
            motor=FakeMotor()
            with self.assertRaises(CurrentError):
                motor.start({'stepA':.1,'intervalS':.2,'durationS':1,'maxTargetA':maximum})
            self.assertFalse(motor.enabled)
            self.assertEqual(motor.writes,[])

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
