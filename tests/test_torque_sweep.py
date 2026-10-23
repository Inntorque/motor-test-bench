"""Hardware-free plant and actuator fakes. Never connect to live services."""
import csv
import json
import math
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from brake_torque import BrakeTorque
from brake_current import BrakeCurrent
from coupled_current import CoupledCurrent
from loaded_timeline import LoadedTimeline
from test_motor_current import FakeMotor
from test_brake_current import Supply


class MeteredSupply(Supply):
    def __init__(self):
        super().__init__();self.current=0;self.enable_trace=[]
    def cached_status(self):
        return dict(connected=True,config=self.config,snapshot=dict(
            status=128 if self.on else 0,receivedAt=time.time(),
            settings=[1,2400,round(self.current*1000)],
            measured=[100 if self.on else 0,round(self.current*1000) if self.on else 0,0]))
    def set_values(self,data):
        self.current=data['current'];return super().set_values(data)
    def set_output(self,on,data):
        self.enable_trace.append(on);return super().set_output(on,data)


class Plant:
    def __init__(self,motor,brake):
        self.motor=motor;self.brake=brake;self.connected=True;self.stall=False;self.stale=False;self.baseline=.05
    def status(self):
        return dict(connected=self.connected,snapshot=dict(
            torque=self.baseline+8*self.brake.current if self.brake.on else self.baseline,
            speed=0 if self.stall or not self.motor.enabled else 5,
            receivedAt=time.time()-(3 if self.stale else 0)))


class MotorPower(Supply):
    def cached_status(self):
        return dict(connected=True,config=dict(voltageDecimals=2,currentDecimals=2),
                    snapshot=dict(status=128 if self.on else 0,receivedAt=time.time(),settings=[1,2400,2000],
                                  measured=[2400 if self.on else 0,0,0]))


class TorqueTests(unittest.TestCase):
    def test_reference_is_a_bounded_seed_not_rated_linear_extrapolation(self):
        self.assertEqual(BrakeTorque.feedforward(0),0)
        self.assertAlmostEqual(BrakeTorque.feedforward(1.3986),.35)
        for target in (-1,.1,2.8,100,float('nan')):
            with self.assertRaises(ValueError):BrakeTorque.feedforward(target)
        control=BrakeTorque()
        previous=0
        for _ in range(250):
            current=control.update(2.7,0)
            self.assertLessEqual(abs(current-previous),.0250001)
            self.assertLess(current,2);previous=current
        self.assertEqual(current,1.999)
        for _ in range(20):current=control.update(2.7,4)
        self.assertLess(current,1.999)
        before=current
        # No feedforward jump above the measured region, despite a huge target.
        self.assertLessEqual(abs(control.update(99,4)-before),.0250001)
        self.assertEqual(control.update(0,1),0)

    def test_recorded_csv_separates_target_measured_and_commanded(self):
        with tempfile.TemporaryDirectory() as tmp:
            timeline=LoadedTimeline(Path(tmp),lambda:None,lambda _:None,None,{})
            timeline.event('torque_feedback',dict(targetNm=1.2,measuredNm=1.1,currentA=.27))
            timeline.close()
            with open(Path(tmp)/'timeline.csv',encoding='utf-8-sig') as f:
                row=list(csv.DictReader(f))[-2]  # session_end is last
            self.assertEqual(row['brake_target_torque_Nm'],'1.2')
            self.assertEqual(row['brake_feedback_torque_Nm'],'1.1')
            self.assertEqual(row['brake_command_current_A'],'0.27')


class SweepTests(unittest.TestCase):
    def setUp(self):
        self.no_network=patch('urllib.request.urlopen',side_effect=AssertionError('live API forbidden in offline test'))
        self.no_network.start()
        self.motor=FakeMotor();self.motor.version=0x0A2C;self.motor.current_limit_ma=5000
        self.supply=MeteredSupply();self.brake=BrakeCurrent(self.supply,lambda *_:None)
        self.power=MotorPower();self.power.on=True
        self.plant=Plant(self.motor,self.supply);self.events=[]
        self.encoder_stall=False
        original=self.motor._request
        def encoder_feedback(route,**kwargs):
            result=original(route,**kwargs)
            if route=='/api/control/status':
                now=time.time()
                result['feedback'].update(speed_rpm=0 if self.encoder_stall else 50,
                    position_deg=600 if self.encoder_stall else time.monotonic()*300,
                    updated_at=now,position_at=now,position_stale=False)
            return result
        self.encoder_feedback_patch=patch.object(self.motor,'_request',side_effect=encoder_feedback)
        self.encoder_feedback_patch.start()
        self.runner=CoupledCurrent(self.motor,self.brake,self.plant,self.power,lambda:False,lambda *args:self.events.append(args))
    def tearDown(self):
        if self.runner.active():self.runner.stop(wait=True)
        self.encoder_feedback_patch.stop()
        self.no_network.stop()
    def params(self):
        return dict(mode='torque_sweep',
                    brake=dict(startNm=.3,stepNm=.1,maxNm=.4,intervalS=.5))
    def wait(self,predicate,seconds=6,heartbeat=True):
        end=time.monotonic()+seconds
        while not predicate():
            if heartbeat:self.runner.status(heartbeat=True)
            if time.monotonic()>end:self.fail(str(self.runner.status()['run']))
            time.sleep(.01)
    def ready(self):
        self.runner.start(self.params())
        self.wait(lambda:self.runner.run['phase']=='awaiting_load')
        return self.runner.run['id']
    def test_two_separate_starts_zero_brake_first_then_measured_torque_steps(self):
        identifier=self.ready()
        self.assertEqual(self.motor.run['targetA'],10)
        self.assertEqual(self.motor.positive_writes(),[10])
        self.assertEqual(self.motor.current_limit_ma,10000)
        self.assertIn('/api/motor?enable=1&mode=5&seconds=0',self.motor.writes)
        self.assertFalse(any('/api/save' in row for row in self.motor.writes))
        self.assertEqual(self.supply.enable_trace,[])
        self.assertEqual(self.runner.run['targetNm'],0)
        time.sleep(.15)
        self.assertFalse(self.supply.on)
        with self.assertRaisesRegex(ValueError,'试验已变化'):self.runner.begin_loading({'id':'old'})
        self.assertFalse(self.supply.on)
        self.runner.begin_loading({'id':identifier})
        self.wait(lambda:not self.runner.active())
        self.assertIn('阶梯完成',self.runner.run['reason'])
        self.assertTrue(self.runner.run['stopConfirmed'])
        self.assertFalse(self.motor.enabled or self.supply.on)
        self.assertTrue(any(k=='torque_feedback' for k,_ in self.events))
        self.assertEqual(self.runner.run['loadSteps'],2)
        self.assertTrue(all(v['current']<=.4 for v in self.supply.values))
        self.assertGreater(self.runner.run['curveStartedAt'],self.runner.sync.started_at-1)
    def test_loading_cannot_precede_ramp_or_restart_after_stop(self):
        with self.assertRaises(ValueError):self.runner.begin_loading({'id':'x'})
        self.runner.start(self.params())
        with self.assertRaises(ValueError):self.runner.begin_loading({'id':self.runner.run['id']})
        self.runner.stop(wait=True)
        with self.assertRaises(ValueError):self.runner.begin_loading({'id':self.runner.run['id']})
        self.assertFalse(self.motor.enabled or self.supply.on)

    def test_pause_during_target_write_cancels_command_and_never_loads_brake(self):
        request=self.motor._request;blocked=threading.Event();release=threading.Event()
        def delayed(route,**kwargs):
            if route=='/api/control/set?field=current_a&value=10.0':
                blocked.set();release.wait(2)
            return request(route,**kwargs)
        with patch.object(self.motor,'_request',side_effect=delayed):
            self.runner.start(self.params());self.wait(blocked.is_set)
            self.assertEqual(self.runner.run['phase'],'ramping')
            self.runner.cancel_requests('离线注入：下达电流目标时暂停')
            release.set();self.wait(lambda:not self.runner.active())
        self.assertEqual(self.motor.positive_writes(),[])
        self.assertTrue(self.runner.run['stopConfirmed'])
        self.assertFalse(self.motor.enabled or self.supply.on)
        self.assertNotIn(True,self.supply.enable_trace)

    def test_full_iq_pause_confirms_zero_target_and_never_starts_brake_or_rearms(self):
        identifier=self.ready()
        before=len(self.motor.writes)
        stopped=self.runner.stop(wait=True)
        self.assertFalse(stopped['run']['running'])
        self.assertTrue(stopped['run']['stopConfirmed'])
        self.assertEqual(self.motor.run['targetA'],0)
        self.assertFalse(self.motor.enabled or self.supply.on)
        self.assertNotIn(True,self.supply.enable_trace)
        with self.assertRaises(ValueError):self.runner.begin_loading({'id':identifier})
        time.sleep(.15)
        self.assertFalse(self.motor.enabled)
        self.assertFalse(any('/api/motor?enable=1' in row for row in self.motor.writes[before:]))
        self.assertTrue(self.motor.writes[-1].endswith('field=current_a&value=0'))

    def test_pause_ack_and_cached_idle_are_not_fresh_off_confirmation(self):
        self.ready();cached=threading.Event();cached.set();off_seen=threading.Event()
        request=self.motor._request;results=[]
        def reply(route,**kwargs):
            result=request(route,**kwargs)
            if route=='/api/status' and not self.motor.enabled and cached.is_set():
                result['motor']['updated_at']=0
                off_seen.set()
            return result
        with patch.object(self.motor,'_request',side_effect=reply):
            thread=threading.Thread(target=lambda:results.append(self.runner.stop(wait=True)))
            thread.start()
            self.assertTrue(off_seen.wait(2))
            self.assertFalse(self.motor.run['stopConfirmed'])
            self.assertNotIn(True,self.supply.enable_trace)
            cached.clear();thread.join(4)
            self.assertFalse(thread.is_alive())
        self.assertTrue(results[0]['run']['stopConfirmed'])
        self.assertFalse(self.motor.enabled or self.supply.on)

    def test_confirmed_pause_allows_new_run_without_resetting_configuration(self):
        previous=self.ready();self.runner.stop(wait=True)
        self.assertFalse(self.runner.blocks_controls())
        current=self.ready()
        self.assertNotEqual(previous,current)
        self.assertTrue(self.runner.active())
        self.assertEqual(self.motor.run['targetA'],10)
        self.assertEqual(self.runner.run['targetNm'],0)
        self.assertNotIn(True,self.supply.enable_trace)

    def test_maximum_limit_readback_mismatch_never_enables_motor_or_brake(self):
        request=self.motor._request
        def reply(route,**kwargs):
            result=request(route,**kwargs)
            if route.startswith('/api/set?id=7'):
                self.motor.current_limit_ma=5000  # ACK without an applied value
            return result
        with patch.object(self.motor,'_request',side_effect=reply):
            self.runner.start(self.params());self.wait(lambda:not self.runner.active())
        self.assertIn('读回不一致',self.runner.run['reason'])
        self.assertFalse(any('/api/motor?enable=1' in row for row in self.motor.writes))
        self.assertEqual(self.motor.positive_writes(),[])
        self.assertNotIn(True,self.supply.enable_trace)

    def test_stop_during_limit_configuration_cannot_be_followed_by_enable(self):
        request=self.motor._request;blocked=threading.Event();release=threading.Event()
        def reply(route,**kwargs):
            if route.startswith('/api/set?id=7'):
                blocked.set();release.wait(2)
            return request(route,**kwargs)
        with patch.object(self.motor,'_request',side_effect=reply):
            self.runner.start(self.params());self.wait(blocked.is_set)
            self.runner.cancel_requests('离线注入：最大电流配置时停止')
            release.set();self.wait(lambda:not self.runner.active())
        self.assertFalse(any('/api/motor?enable=1' in row for row in self.motor.writes))
        self.assertEqual(self.motor.positive_writes(),[])
        self.assertNotIn(True,self.supply.enable_trace)
    def test_parameter_limits_reject_before_output(self):
        for key,value in (('startNm',-.1),('maxNm',0),('maxNm',100),('stepNm',float('nan')),('intervalS',.1)):
            data=self.params();data['brake'][key]=value
            with self.assertRaises(ValueError):self.runner.start(data)
        self.assertEqual(self.motor.writes,[]);self.assertEqual(self.supply.values,[])

    def test_zero_start_holds_brake_off_despite_background_then_loads_and_can_restart(self):
        self.plant.baseline=.18
        params=self.params();params['brake'].update(startNm=0,stepNm=.5,maxNm=.5)
        self.runner.start(params);self.wait(lambda:self.runner.run['phase']=='awaiting_load')
        self.runner.begin_loading({'id':self.runner.run['id']})
        self.wait(lambda:any(k=='torque_feedback' and v['targetNm']==0 for k,v in self.events))
        self.assertFalse(self.supply.on)
        self.assertEqual(self.supply.current,0)
        self.assertAlmostEqual(self.runner.run['measuredNm'],.18)
        self.wait(lambda:not self.runner.active())
        self.assertIn('阶梯完成',self.runner.run['reason'])
        self.assertTrue(self.runner.run['stopConfirmed'])
        self.assertFalse(self.motor.enabled or self.supply.on)
        previous=self.runner.run['id']
        self.runner.start(params);self.wait(lambda:self.runner.run['phase']=='awaiting_load')
        self.assertNotEqual(previous,self.runner.run['id'])
        self.assertEqual(self.runner.run['targetNm'],0)
        self.assertFalse(self.supply.on)
    def test_loss_of_owner_during_wait_stops_motor_without_enabling_brake(self):
        self.ready();self.runner.lease=time.monotonic()-3
        self.wait(lambda:not self.runner.active(),heartbeat=False)
        self.assertIn('心跳',self.runner.run['reason'])
        self.assertFalse(self.motor.enabled or self.supply.on)
        self.assertNotIn(True,self.supply.enable_trace)
    def test_slow_torque_response_keeps_loading_past_ten_seconds(self):
        identifier=self.ready();respond=threading.Event()
        original=self.plant.status
        def delayed_response():
            status=original()
            if not respond.is_set():status['snapshot']['torque']=.05
            return status
        with patch.object(self.plant,'status',side_effect=delayed_response):
            self.runner.begin_loading({'id':identifier})
            self.wait(lambda:self.supply.on)
            began=time.monotonic()
            self.wait(lambda:not self.runner.active() or time.monotonic()-began>=11,seconds=12)
            self.assertTrue(self.runner.active(),self.runner.run['reason'])
            self.assertTrue(self.motor.enabled and self.supply.on)
            self.assertEqual(self.runner.run['loadSteps'],1)
            self.assertEqual(self.supply.enable_trace,[True])
            respond.set()
            self.wait(lambda:not self.runner.active())
        self.assertIn('阶梯完成',self.runner.run['reason'])
        self.assertEqual(self.runner.run['loadSteps'],2)
        self.assertTrue(self.runner.run['stopConfirmed'])
        self.assertFalse(self.motor.enabled or self.supply.on)

    def test_torque_ripple_keeps_advancing_until_confirmed_stall(self):
        params=self.params();params['brake'].update(intervalS=1,maxNm=99.9)
        torque=[.48];feedback=[]
        original=self.plant.status
        def noisy_response():
            status=original();status['snapshot']['torque']=torque[0]
            return status
        def observe(kind,payload):
            self.events.append((kind,payload))
            if kind=='torque_feedback':
                feedback.append(payload)
                # Entire control intervals alternate above/below the old
                # +/-0.1 band, as with the measured mechanical background.
                torque[0]=payload['targetNm']+(-.18 if len(feedback)%2 else .18)
                if payload['targetNm']>=.6:
                    self.plant.stall=True;self.encoder_stall=True
        self.runner.observe=observe
        with patch.object(self.plant,'status',side_effect=noisy_response):
            self.runner.start(params);self.wait(lambda:self.runner.run['phase']=='awaiting_load')
            self.runner.begin_loading({'id':self.runner.run['id']})
            self.wait(lambda:not self.runner.active(),seconds=9)
        self.assertIn('编码器连续确认',self.runner.run['reason'])
        self.assertEqual(self.runner.run['loadSteps'],4)
        self.assertTrue(any(row['measuredNm']<row['targetNm']-.1 for row in feedback))
        self.assertTrue(any(row['measuredNm']>row['targetNm']+.1 for row in feedback))
        self.assertGreaterEqual(self.runner.run['elapsed'],3)
        endpoint=self.runner.run['stallEndpoint']
        self.assertEqual(endpoint['speed'],0)
        self.assertEqual(endpoint['receivedAt'],self.runner.run['measurementEndedAt'])
        self.assertTrue(endpoint['encoderConfirmation']['confirmed'])
        self.assertTrue(self.runner.run['stopConfirmed'])
        self.assertFalse(self.motor.enabled or self.supply.on)

    def test_stall_during_loading_freezes_and_stops_both(self):
        identifier=self.ready();self.runner.begin_loading({'id':identifier})
        self.wait(lambda:self.supply.on)
        self.plant.stall=True;self.encoder_stall=True
        self.wait(lambda:self.runner.run.get('stallDetectedAt') is not None)
        count=len(self.supply.values)
        self.wait(lambda:not self.runner.active())
        self.assertEqual(len(self.supply.values),count)
        self.assertIn('编码器连续确认',self.runner.run['reason'])
        self.assertTrue(self.runner.run['stopConfirmed'])
        self.assertFalse(self.motor.enabled or self.supply.on)
        self.assertIsNotNone(self.runner.run['measurementEndedAt'])
        self.assertGreaterEqual(self.runner.run['measurementEndedAt']-self.runner.run['stallDetectedAt'],.9)
        endpoint=self.runner.run['stallEndpoint']
        self.assertEqual(endpoint['speed'],0)
        self.assertEqual(endpoint['receivedAt'],self.runner.run['measurementEndedAt'])
        self.assertTrue(endpoint['encoderConfirmation']['confirmed'])
    def test_sensor_zero_while_encoder_moves_does_not_stop_or_unload(self):
        params=self.params();params['brake'].update(maxNm=10,intervalS=5)
        self.runner.start(params);self.wait(lambda:self.runner.run['phase']=='awaiting_load')
        self.runner.begin_loading({'id':self.runner.run['id']})
        self.wait(lambda:self.supply.on)
        self.plant.stall=True
        deadline=time.monotonic()+1.7
        while time.monotonic()<deadline:
            self.runner.status(heartbeat=True);time.sleep(.02)
        self.assertTrue(self.runner.active())
        self.assertTrue(self.motor.enabled and self.supply.on)
        self.assertEqual(self.runner.run['stopConfirmation']['state'],'encoder_moving')
        self.assertNotIn('stallEndpoint',self.runner.run)
    def test_stale_sensor_during_wait_never_enables_brake(self):
        self.ready();self.plant.stale=True
        self.wait(lambda:not self.runner.active())
        self.assertIn('过期',self.runner.run['reason'])
        self.assertNotIn(True,self.supply.enable_trace)

    def test_unpowered_preflight_never_writes_any_actuator(self):
        self.power.on=False
        self.runner.start(self.params());self.wait(lambda:not self.runner.active())
        self.assertEqual(self.motor.writes,[]);self.assertEqual(self.supply.values,[])
        self.assertNotIn(True,self.supply.enable_trace)

    def test_current_boundary_is_not_mislabeled_as_motor_limit(self):
        with patch.object(BrakeTorque,'MAX_CURRENT_A',.05):
            data=self.params();data['brake'].update(startNm=1,maxNm=1)
            self.runner.start(data);self.wait(lambda:self.runner.run['phase']=='awaiting_load')
            self.runner.begin_loading({'id':self.runner.run['id']})
            self.wait(lambda:not self.runner.active())
            self.assertIn('未确认电机极限',self.runner.run['reason'])
            self.assertTrue(all(row['current']<=.05 for row in self.supply.values))
            self.assertFalse(self.motor.enabled or self.supply.on)

    def test_motor_protection_never_power_cycles_or_restarts_sweep(self):
        self.runner.recovery=True
        self.ready();self.motor.fail='fault'
        self.wait(lambda:not self.runner.active())
        self.assertIn('保护',self.runner.run['reason'])
        self.assertNotIn('recoveryCount',self.runner.run)
        self.assertEqual(sum('/api/motor?enable=1' in row for row in self.motor.writes),1)
        self.assertNotIn(True,self.supply.enable_trace)

    def test_cancel_during_brake_set_does_not_send_enable_afterward(self):
        identifier=self.ready();blocked=threading.Event();release=threading.Event()
        original=self.supply.set_values
        def delayed(data):
            if data['current']>0:
                blocked.set();release.wait(2)
            return original(data)
        with patch.object(self.supply,'set_values',side_effect=delayed):
            self.runner.begin_loading({'id':identifier})
            self.wait(blocked.is_set)
            self.runner.cancel_requests('离线注入：设置电流期间停止')
            release.set();self.wait(lambda:not self.runner.active())
        self.assertNotIn(True,self.supply.enable_trace)
        self.assertFalse(self.motor.enabled or self.supply.on)
        self.assertTrue(self.runner.run['stopConfirmed'])


if __name__=='__main__':unittest.main()
