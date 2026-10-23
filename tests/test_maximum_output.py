"""Mode-7 integration with in-memory actuators; all network I/O is prohibited."""
import threading
import time
import unittest
from urllib.parse import urlsplit,parse_qs
from unittest.mock import patch
from motor_current import CurrentError
from test_motor_current import FakeMotor
import test_torque_sweep as torque_fixture

class MaximumMotor(FakeMotor):
    def __init__(self):
        super().__init__();self.version=0x0A3C;self.device_mode=5;self.window=0
    def _request(self,route,**kwargs):
        p=urlsplit(route);q=parse_qs(p.query)
        if p.path=='/api/control/set' and q['field']!=['current_a']:
            self.writes.append(route);return {'ok':True}
        if p.path=='/api/max-output/status':
            self.window+=1
            return {'ok':True,'maximumOutput':dict(schema=1,iqReferenceA=24.,idReferenceA=0,
                windowId=self.window,mcuTimeUs=self.window*200000,receivedAt=time.time(),
                quality=['sampling_window_unverified'],limits=['adc_range'])}
        result=super()._request(route,**kwargs)
        if p.path=='/api/motor' and q['enable']==['1']:self.device_mode=int(q['mode'][0])
        if p.path=='/api/status':result['motor']['mode']=self.device_mode
        if p.path=='/api/control/status':
            now=time.time();result['control']['mode']=self.device_mode
            result['feedback'].update(id_a=0.,iq_a=23.9 if self.enabled else 0,
                speed_rpm=100,position_deg=time.monotonic()*600,updated_at=now,
                position_at=now,position_stale=False)
        return result

class MaximumTests(unittest.TestCase):
    def wait_for(self,predicate,heartbeat=None,timeout=5):
        deadline=time.monotonic()+timeout
        while not predicate():
            if heartbeat:heartbeat()
            if time.monotonic()>deadline:self.fail('maximum-output lifecycle timed out')
            time.sleep(.02)
    def test_mode7_runtime_settings_no_fixed_iq_and_quality_does_not_stop(self):
        m=MaximumMotor()
        with patch('urllib.request.urlopen',side_effect=AssertionError('hardware forbidden')):
            try:
                m.start(dict(mode='maximum_output',busLimitA=5,busVoltageV=24))
                self.wait_for(lambda:m.run.get('maximumRequested'),heartbeat=lambda:setattr(m,'lease',time.monotonic()))
                self.wait_for(lambda:m.run.get('targetA')==24,heartbeat=lambda:setattr(m,'lease',time.monotonic()))
                self.assertTrue(m.run['running']);self.assertFalse(m.run['limitReached'])
                self.assertEqual(m.run['targetA'],24)
                self.assertFalse(any('field=current_a' in route and float(parse_qs(urlsplit(route).query)['value'][0])>0 for route in m.writes))
                self.assertEqual(m.current_limit_ma,30000)
                self.assertTrue(any('enable=1&mode=7&seconds=0' in w for w in m.writes))
                self.assertTrue(any('field=max_id_min_a&value=0' in w for w in m.writes))
                self.assertFalse(any('/api/save' in w for w in m.writes))
            finally:m.stop()
        self.assertFalse(m.enabled);self.assertTrue(m.run['stopConfirmed'])
    def test_old_firmware_refused_before_start_or_parameter_write(self):
        m=MaximumMotor();m.version=0x0A3B
        with self.assertRaisesRegex(CurrentError,'0x0A3C'):m.prepare({'mode':'maximum_output'})
        self.assertEqual(m.writes,[])
    def test_invalid_maximum_options_fail_without_output(self):
        m=MaximumMotor()
        for options in ({'busLimitA':0},{'negativeIdMinA':1},{'maxPhaseA':31},{'busVoltageV':float('nan')}):
            with self.assertRaises(CurrentError):m.prepare({'mode':'maximum_output',**options})
        self.assertEqual(m.writes,[])
    def test_joint_mode7_waits_for_manual_load_and_automatically_records(self):
        fixture=torque_fixture.SweepTests();fixture.setUp()
        class Recorder:
            def __init__(self):self.open=False;self.stopped=[]
            def active(self):return self.open
            def start(self,data):self.open=True;return {'id':'max-record','name':data['name']}
            def stop(self,identifier):self.open=False;self.stopped.append(identifier)
        try:
            m=MaximumMotor();fixture.motor=m;fixture.runner.motor=m;fixture.plant.motor=m
            fixture.runner.recorder=rec=Recorder()
            data=fixture.params();data['motorMode']='maximum_output'
            fixture.runner.start(data)
            self.wait_for(lambda:fixture.runner.run['phase']=='awaiting_load',heartbeat=lambda:fixture.runner.status(heartbeat=True))
            self.assertFalse(fixture.supply.on)
            self.assertTrue(rec.active());self.assertEqual(fixture.runner.run['recordingId'],'max-record')
            self.assertFalse(fixture.runner.run.get('loadRequested',False))
            fixture.runner.begin_loading({'id':fixture.runner.run['id']})
            self.wait_for(lambda:fixture.supply.on,heartbeat=lambda:fixture.runner.status(heartbeat=True))
            fixture.runner.stop(wait=True)
            self.assertEqual(rec.stopped,['max-record'])
            self.assertTrue(fixture.runner.run['stopConfirmed'])
        finally:fixture.tearDown()

if __name__=='__main__':unittest.main()
