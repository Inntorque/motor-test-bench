"""Mode-7 integration with in-memory actuators; all network I/O is prohibited."""
import threading
import time
import unittest
from urllib.parse import urlsplit,parse_qs
from unittest.mock import patch
from workbench.test_engine import CurrentError
from test_motor_current import FakeMotor
import test_torque_sweep as torque_fixture

class MaximumMotor(FakeMotor):
    def __init__(self):
        super().__init__();self.mode=5

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
                self.assertEqual(m.positive_writes(),[])
                self.assertEqual(m.current_limit_ma,30000)
                self.assertIn(('start',5),m.writes)
                prepared=next(w[1] for w in m.writes if w[0]=='prepare')
                self.assertEqual(prepared[0x1006],0)
            finally:m.stop()
        self.assertFalse(m.enabled);self.assertTrue(m.run['stopConfirmed'])
    def test_old_firmware_refused_before_start_or_parameter_write(self):
        m=MaximumMotor();m.maximum_supported=False
        with self.assertRaisesRegex(CurrentError,'MAX_AVAILABLE'):m.prepare({'mode':'maximum_output'})
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
