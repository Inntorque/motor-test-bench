import threading
import time
import unittest
from unittest.mock import patch
import http.client
import json
from http.server import ThreadingHTTPServer

from coupled_current import CoupledCurrent, RampSynchronization
from brake_current import BrakeCurrent
from test_brake_current import Supply
from test_motor_current import FakeMotor


class TimedMotor(FakeMotor):
    def __init__(self):
        super().__init__()
        self.step_times = []
        self.precharge_delay = 0
        self.enabled_at = 0
    def _request(self, route, *, write=False, allow_cancel=False):
        result=super()._request(route,write=write,allow_cancel=allow_cancel)
        if route.startswith('/api/motor?enable=1'):self.enabled_at=time.monotonic()
        if route=='/api/status' and self.enabled and time.monotonic()<self.enabled_at+self.precharge_delay:
            result['motor']['state']=1
        return result
    def _set_current(self, value):
        if value > 0:self.step_times.append(time.monotonic())
        return super()._set_current(value)


class TimedSupply(Supply):
    def __init__(self):
        super().__init__()
        self.step_times = []
        self.fail_off = False
        self.fail_enable = False
    def set_values(self, data):
        if data['current'] > 0:self.step_times.append(time.monotonic())
        return super().set_values(data)
    def set_output(self, on, data):
        if on and self.fail_enable:raise ValueError('brake enable failed')
        if not on and self.fail_off:raise ValueError('OFF readback unavailable')
        return super().set_output(on, data)


class Sensor:
    connected = True
    speed = 0
    torque = 0
    def status(self):return {'connected':self.connected,'snapshot':{'torque':self.torque,'speed':self.speed,'receivedAt':time.time()}}


class CoupledTests(unittest.TestCase):
    def setUp(self):
        self.motor = TimedMotor()
        self.supply = TimedSupply()
        self.sensor = Sensor()
        self.power = Supply();self.power.on = True
        self.events = []
        self.brake = BrakeCurrent(self.supply, lambda *args:None)
        self.runner = CoupledCurrent(self.motor, self.brake, self.sensor, self.power,
                                     lambda:False, lambda *args:self.events.append(args))

    def tearDown(self):
        self.supply.fail_off = False
        if self.runner.active():self.runner.stop(wait=True)
        if self.supply.on:self.brake.stop()

    def params(self, duration=1):
        return {'motor':{'stepA':.4,'intervalS':.2,'durationS':duration},
                'brake':{'mode':'hold','currentA':.1,'durationS':duration}}

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic()+timeout
        while not predicate():
            if time.monotonic() > deadline:self.fail('timed out waiting for joint lifecycle')
            time.sleep(.01)

    def finish(self):
        self.wait_for(lambda:not self.runner.active())
        return self.runner.status()['run']

    def test_fixed_brake_is_prepared_before_motor_steps_and_both_off_at_expiry(self):
        self.motor.precharge_delay=.25
        self.runner.start(self.params())
        while self.runner.active():
            self.runner.status(heartbeat=True);time.sleep(.03)
        run = self.runner.status()['run']
        self.assertEqual(run['reason'], '持续时间到期')
        self.assertTrue(run['stopConfirmed'])
        self.assertEqual(self.motor.run['startedAt'], self.brake.run['startedAt'])
        self.assertGreaterEqual(self.runner.sync.began,self.motor.enabled_at+.25)
        self.assertEqual(self.motor.run['deviceWatchdogS'],16)
        self.assertGreater(len(self.motor.step_times), 1)
        self.assertEqual(len(self.supply.step_times), 1)
        self.assertEqual(self.brake.run['targetA'],.1)
        self.assertEqual(self.brake.run['steps'],0)
        self.assertLess(self.supply.step_times[0], self.runner.sync.began)
        self.assertGreaterEqual(self.motor.step_times[0], self.runner.sync.began+.19)
        self.assertGreater(run['measurementEndedAt'],run['startedAt'])
        self.assertFalse(self.motor.enabled or self.supply.on)
        self.assertEqual(self.motor.run['coupledId'],run['id'])
        self.assertEqual(self.brake.run['coupledId'],run['id'])
        self.assertIn('coupled_end', [e[0] for e in self.events])

    def test_brake_preflight_failure_does_not_enable_motor(self):
        self.supply.config['currentDecimals'] = 2
        self.runner.start(self.params())
        run = self.finish()
        self.assertIn('精度',run['reason'])
        self.assertEqual(self.motor.writes, [])
        self.assertFalse(self.motor.enabled or self.supply.on)

    def test_confirmed_100nm_range_and_overrange_stop(self):
        self.sensor.torque=75
        self.runner._check_sensor()
        for torque in (-100,100,float('nan')):
            self.sensor.torque=torque
            with self.assertRaisesRegex(ValueError,'100 N'):self.runner._check_sensor()

    def test_sensor_zero_alone_does_not_end_both(self):
        self.sensor.speed=5
        self.runner.start(self.params(5))
        self.wait_for(lambda:self.motor.run.get('elapsed',0)>.65)
        self.sensor.speed=0
        deadline=time.monotonic()+1.4
        while time.monotonic()<deadline:
            self.runner.status(heartbeat=True);time.sleep(.02)
        self.assertTrue(self.runner.active())
        self.runner.stop(wait=True)
        run=self.runner.status()['run']
        self.assertNotIn('疑似停转',run['reason'])
        self.assertTrue(run['stopConfirmed'])
        self.assertFalse(self.motor.enabled or self.supply.on)
        self.assertLess(run['measurementEndedAt'],time.time())

    def test_fixed_load_required_no_silent_legacy_dual_ramp(self):
        data=self.params();data['brake']={'stepA':.1,'intervalS':1,'durationS':5}
        with self.assertRaisesRegex(ValueError,'固定制动'):self.runner.start(data)

    def test_trial_motor_ceiling_survives_joint_normalization(self):
        data=self.params();data['motor']['maxTargetA']=.5
        self.runner.start(data)
        while self.runner.active():self.runner.status(heartbeat=True);time.sleep(.02)
        self.assertEqual(self.motor.run['limitA'],.5)
        self.assertEqual(self.motor.run['deviceLimitA'],1)
        self.assertEqual(self.motor.positive_writes(),[.4,.5])
        self.assertTrue(self.runner.run['stopConfirmed'])

    def test_motor_power_off_or_sensor_missing_blocks_both(self):
        for source, attribute in ((self.power,'on'),(self.sensor,'connected')):
            with self.subTest(attribute=attribute):
                setattr(source,attribute,False)
                self.runner.start(self.params());self.finish()
                self.assertEqual(self.motor.writes, [])
                self.assertFalse(self.supply.on)
                setattr(source,attribute,True)

    def test_brake_write_failure_stops_motor(self):
        self.supply.fail = True
        self.runner.start(self.params(5))
        run = self.finish()
        self.assertIn('readback mismatch',run['reason'])
        self.assertTrue(run['stopConfirmed'])
        self.assertFalse(self.motor.enabled or self.supply.on)

    def test_motor_protection_stops_brake(self):
        self.motor.fail = 'fault'
        self.runner.start(self.params(5))
        run = self.finish()
        self.assertIn('保护',run['reason'])
        self.assertTrue(run['stopConfirmed'])
        self.assertFalse(self.motor.enabled or self.supply.on)

    def test_second_output_enable_failure_confirms_both_off_without_positive_steps(self):
        self.supply.fail_enable=True
        self.runner.start(self.params(5))
        run=self.finish()
        self.assertIn('brake enable failed',run['reason'])
        self.assertEqual(run['stopResults'],{'motor':True,'brake':True})
        self.assertTrue(run['stopConfirmed'])
        self.assertEqual(self.motor.step_times,[])
        self.assertFalse(self.motor.enabled or self.supply.on)

    def test_sensor_loss_and_lease_loss_stop_both(self):
        for cause in ('sensor','lease'):
            with self.subTest(cause=cause):
                self.sensor.connected = True
                self.runner.start(self.params(5))
                self.wait_for(lambda:self.runner.status()['run']['phase']=='running')
                if cause=='sensor':self.sensor.connected=False
                else:self.runner.lease=time.monotonic()-3
                run=self.finish()
                self.assertIn('传感器' if cause=='sensor' else '心跳',run['reason'])
                self.assertFalse(self.motor.enabled or self.supply.on)

    def test_user_stop_and_stop_failure_reported_truthfully(self):
        self.runner.start(self.params(5))
        self.wait_for(lambda:self.runner.status()['run']['phase']=='running')
        self.supply.fail_off = True
        run=self.runner.stop(wait=True)['run']
        self.assertFalse(run['stopConfirmed'])
        self.assertFalse(run['stopResults']['brake'])
        self.assertTrue(run['stopResults']['motor'])
        self.assertTrue(run['stopErrors'])
        self.assertFalse(self.motor.enabled)
        self.assertTrue(self.runner.blocks_controls())
        self.supply.fail_off = False
        retried=self.runner.stop(wait=True)['run']
        self.assertTrue(retried['stopConfirmed'])
        self.assertFalse(self.runner.blocks_controls())
        self.assertFalse(self.supply.on)

    def test_http_blocks_independent_start_and_brake_stop_stops_both_without_deadlock(self):
        import hardware_bridge as bridge
        server=ThreadingHTTPServer(('127.0.0.1',0),bridge.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def post(path, data):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            connection.request('POST',path,json.dumps(data),{'Host':'127.0.0.1:8766',
                'X-MotorBench-Token':bridge.TOKEN,'Content-Type':'application/json'})
            response=connection.getresponse();result=response.status,json.loads(response.read());connection.close()
            return result
        try:
            with patch.multiple(bridge,COUPLED_CURRENT=self.runner,CURRENT_MOTOR=self.motor,BRAKE_CURRENT=self.brake):
                self.assertEqual(post('/api/coupled-current/start',self.params(5))[0],200)
                self.wait_for(lambda:self.runner.status()['run']['phase']=='running')
                with patch.object(bridge.CHANNELS['brake-supply'],'read',side_effect=AssertionError('duplicate RTU poll')) as read:
                    self.assertEqual(post('/api/supplies/brake-supply/read',{})[0],200)
                    read.assert_not_called()
                self.assertEqual(post('/api/motor-current/start',self.params()['motor'])[0],400)
                code, result=post('/api/brake-current/stop',{})
                self.assertEqual(code,200)
                self.assertTrue(result['run']['stopConfirmed'])
                self.assertFalse(self.motor.enabled or self.supply.on)
        finally:
            server.shutdown();server.server_close()

    def test_mismatched_duration_and_duplicate_start_rejected(self):
        bad=self.params();bad['brake']['durationS']=2
        with self.assertRaisesRegex(ValueError,'相同'):self.runner.start(bad)
        with self.assertRaisesRegex(ValueError,'240'):self.runner.start(self.params(241))
        self.runner.start(self.params(5))
        with self.assertRaises(ValueError):self.runner.start(self.params(5))
        self.runner.stop(wait=True)

    def test_cancel_releases_other_output_waiting_at_barrier(self):
        sync=RampSynchronization('fake',timeout=1)
        result=[]
        def waiting():
            try:sync.ready('motor')
            except ValueError as error:result.append(str(error))
        thread=threading.Thread(target=waiting);thread.start()
        self.wait_for(lambda:bool(sync.ready_at))
        sync.fail('brake','startup failure');thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertIn('startup failure',result[0])


if __name__ == '__main__':unittest.main()
