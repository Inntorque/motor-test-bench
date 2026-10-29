"""Test strategies with a native device fake; no serial/CAN or HTTP access."""
import threading
import time
import unittest
from protocol import generated as p
from workbench.test_engine import CurrentMotor,CurrentError

class FakeDevice:
    def __init__(self,motor):self.owner=motor;self.prepared=None
    def connect(self):return self.latest_snapshot()
    def latest_snapshot(self):
        m=self.owner;now=time.time();fault=m.enabled and (m.fail=='fault' and m.last_positive>0 or m.fault_at is not None and m.current>=m.fault_at or m.mode==3 and m.speed_fault_at is not None and m.speed>=m.speed_fault_at)
        state=4 if fault else 1 if m.enabled and time.monotonic()<m.enabled_at+m.precharge_delay else 2 if m.enabled else 0
        m.speed_reads+=1
        speed=m.speed+(5 if int(time.monotonic()*10)%2 else -5) if m.speed_wobble and m.enabled and m.mode==3 else m.speed
        iq=24 if m.mode==5 and m.enabled else m.current
        ready=39 if m.calibrated else 2
        return {'wireProtocol':[1,0],'version':str(m.version),'capabilities':{'maximumOutput':m.maximum_supported,'modes':(1<<1)|(1<<3)|(1<<4)|(1<<5)},'limits':{str(p.OBJ_ROTOR_SPEED_ABS_MAX):m.speed_limit*1000},'readiness':{'flags':ready,'detail':'ready' if m.calibrated else '电角校准无效','faultCategory':2 if fault else 0,'vendorCode':4 if fault else 0},
                'motorProfile':{'nameplate':{'reduction_ratio':10}},'limitA':m.current_limit_ma/1000,'supportedMaxA':30,'fullTargetA':10,
                'motor':{'available':True,'age_s':0,'mode':m.mode,'state':state,'fault':bool(fault),'output_on':m.enabled and not fault,'updated_at':now},
                'encoder':{'valid':True,'stale':False},'ntc':{'valid':True,'age_s':0,'temp_c':25,'limit_c':m.ntc_limit},
                'feedback':{'iq_a':iq,'id_a':0.,'iq_reference_a':iq,'stale':False,'updated_at':now,'position_at':now,'position_stale':m.stale,'position_deg':m.position,'speed_rpm':speed},
                'maximumOutput':{'iqReferenceA':24,'idReferenceA':0,'operatingCurrentA':28,'receivedAt':now,'quality':['sampling_window_unverified'],'limits':['adc_range']}}
    def prepare_run(self,config):
        m=self.owner
        if m.mode==5 and not m.maximum_supported:raise CurrentError('UNSUPPORTED')
        if config.get(p.OBJ_NEGATIVE_ID_MIN,0):raise CurrentError('UNSUPPORTED')
        self.prepared=config;m.writes.append(('prepare',dict(config)))
        if config.get(p.OBJ_PHASE_RUN_LIMIT):m.current_limit_ma=config[p.OBJ_PHASE_RUN_LIMIT]
        elif config[p.OBJ_RUN_MODE]==5:m.current_limit_ma=30000
        return 123
    def start(self,prepared):
        m=self.owner
        if m.cancel.is_set():raise CurrentError('cancelled')
        if prepared!=123:raise CurrentError('NOT_READY')
        m.enabled=True;m.mode=self.prepared[p.OBJ_RUN_MODE];m.enabled_at=time.monotonic();m.writes.append(('start',m.mode))
        if p.OBJ_POSITION_TARGET in self.prepared:self.set_object_target(p.OBJ_POSITION_TARGET,self.prepared[p.OBJ_POSITION_TARGET])
        if p.OBJ_VELOCITY_TARGET in self.prepared:self.set_object_target(p.OBJ_VELOCITY_TARGET,self.prepared[p.OBJ_VELOCITY_TARGET])
    def set_target(self,value):return self.set_object_target(p.OBJ_IQ_TARGET,round(value*1000))
    def set_object_target(self,oid,value):
        m=self.owner
        if m.cancel.is_set():raise CurrentError('cancelled')
        m.writes.append(('target',oid,value))
        if oid==p.OBJ_IQ_TARGET:
            m.current=value/1000
            if value>0:
                m.last_positive=value/1000
                if m.fail=='write':raise CurrentError('write ACK missing')
        if oid==p.OBJ_POSITION_TARGET:m.position=value/1000000*360+m.position_error_deg*10
        if oid==p.OBJ_VELOCITY_TARGET:m.speed=min(value/1000,m.speed_response_cap) if m.speed_response_cap is not None else value/1000
        return {'confirmed':True}
    def stop(self):
        m=self.owner;m.writes.append(('stop',))
        if m.stop_fail:raise CurrentError('stop ACK missing')
        m.enabled=False;m.speed=0;m.current=0;return {'deviceDisabled':True}
    def close(self):pass

class FakeMotor(CurrentMotor):
    def __init__(self,fail=None):
        self.enabled=False;self.writes=[];self.fail=fail;self.calibrated=True;self.last_positive=0;self.version='arbitrary firmware metadata'
        self.current_limit_ma=1000;self.mode=1;self.current=0.;self.position=0.;self.speed=0.;self.fault_at=None
        self.stale=False;self.stop_fail=False;self.speed_limit=20;self.speed_response_cap=None;self.speed_fault_at=None
        self.speed_wobble=False;self.speed_reads=0;self.position_error_deg=0.;self.ntc_limit=60;self.precharge_delay=0;self.enabled_at=0;self.maximum_supported=True
        super().__init__(FakeDevice(self))
    def positive_writes(self):return [w[2]/1000 for w in self.writes if len(w)==3 and w[0]=='target' and w[1]==p.OBJ_IQ_TARGET and w[2]>0]

class MotorCurrentTests(unittest.TestCase):
    def finish(self,m):
        m.thread.join(3);self.assertFalse(m.thread.is_alive());self.assertFalse(m.enabled);self.assertTrue(m.run['stopConfirmed'])
    def test_steps_hold_interval_and_stop_at_duration(self):
        m=FakeMotor();m.start(dict(stepA=.4,intervalS=.2,durationS=1));self.finish(m)
        self.assertEqual(m.positive_writes(),[.4,.8,1]);self.assertEqual(m.run['reason'],'持续时间到期')
    def test_command_failure_stops_no_automatic_retry(self):
        m=FakeMotor('write');m.start(dict(stepA=.4,intervalS=.2,durationS=1));self.finish(m)
        self.assertEqual(m.positive_writes(),[.4]);self.assertIn('ACK missing',m.run['reason'])
    def test_reported_fault_preserved_and_stops(self):
        m=FakeMotor('fault');m.start(dict(stepA=.4,intervalS=.2,durationS=1));self.finish(m)
        self.assertIn('保护',m.run['reason']);self.assertTrue(m.run['endProtection']['motor']['fault'])
    def test_operator_target_ceiling_does_not_raise_device_protection(self):
        m=FakeMotor();m.start(dict(stepA=.2,intervalS=.2,durationS=1,maxTargetA=.3));self.finish(m)
        self.assertEqual(m.positive_writes(),[.2,.3]);self.assertEqual(m.current_limit_ma,1000)
    def test_unready_calibration_cannot_prepare(self):
        m=FakeMotor();m.calibrated=False
        with self.assertRaisesRegex(CurrentError,'校准'):m.prepare(dict(stepA=.2,intervalS=.2,durationS=1))
        self.assertEqual(m.writes,[])
    def test_maximum_uses_capability_not_firmware_version(self):
        m=FakeMotor();m.version='vendor-2020';self.assertEqual(m.prepare({'mode':'maximum_output'}),(0,1,0,30))
        m.maximum_supported=False
        with self.assertRaisesRegex(CurrentError,'MAX_AVAILABLE'):m.prepare({'mode':'maximum_output'})
    def test_stop_failure_never_claims_confirmed(self):
        m=FakeMotor();m.stop_fail=True
        with self.assertRaises(CurrentError):m.stop()
        self.assertFalse(m.run['stopConfirmed'])
    def test_invalid_inputs_do_not_prepare_or_start(self):
        for data in ({'stepA':0,'intervalS':1,'durationS':1},{'stepA':.1,'intervalS':.1,'durationS':1},{'mode':'maximum_output','negativeIdMinA':1}):
            m=FakeMotor()
            with self.assertRaises(CurrentError):m.start(data)
            self.assertEqual(m.writes,[])
    def test_explicit_stop_cancels_further_targets(self):
        m=FakeMotor();m.start(dict(stepA=.2,intervalS=.2,durationS=10));time.sleep(.3);m.stop();self.finish(m)
        count=len(m.positive_writes());time.sleep(.25);self.assertEqual(len(m.positive_writes()),count)
    def test_full_current_is_one_target_and_native_prepare(self):
        m=FakeMotor();m.start({'mode':'full_current'});time.sleep(.15);m.stop();self.finish(m)
        self.assertEqual(m.positive_writes(),[10]);self.assertEqual(m.current_limit_ma,30000)
    def test_read_is_cached_and_never_claims_control(self):
        m=FakeMotor();m.connect()
        for _ in range(5):m.read()
        self.assertEqual(m.writes,[])

if __name__=='__main__':unittest.main()
