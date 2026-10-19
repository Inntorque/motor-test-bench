import csv
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from automatic_tests import AutomaticTests
from motor_current import CurrentMotor, CurrentError, XIGE_ROOT
from specialized_tests import SpecializedTests


class ScriptMotor(CurrentMotor):
    def __init__(self):
        super().__init__()
        self.enabled=False;self.mode=5;self.writes=[]
        self.current=0.;self.position=0.;self.speed=0.;self.fault_at=None
        self.calibrated=True;self.stale=False;self.stop_fail=False
        self.speed_limit=20;self.speed_response_cap=None;self.speed_fault_at=None
        self.speed_wobble=False;self.speed_reads=0
        self.position_error_deg=0.;self.ntc_limit=60

    def _request(self, route, *, write=False, allow_cancel=False):
        if write:
            if self.cancel.is_set() and not allow_cancel:raise CurrentError('cancelled')
            self.writes.append(route)
        parsed=urlsplit(route);q=parse_qs(parsed.query)
        fault=self.enabled and ((self.fault_at is not None and self.current>=self.fault_at) or
                               (self.mode==2 and self.speed_fault_at is not None and self.speed>=self.speed_fault_at))
        if parsed.path=='/api/status':
            return {'project_root':str(XIGE_ROOT),'connected':True,'version_raw':0x0A1C,
                    'version':'fake','motor_profile':{'data':{'nameplate':{'reduction_ratio':10}}},
                    'motor':{'available':True,'age_s':0,'mode':self.mode,
                    'state':3 if fault else 2 if self.enabled else 0,'fault':fault,
                    'output_on':self.enabled and not fault,'updated_at':time.time()},
                    'encoder':{'valid':True,'stale':False,'electrical_calibrated':self.calibrated,'electrical_angle_valid':self.calibrated},
                    'ntc':{'valid':True,'age_s':0,'temp_c':25,'limit_c':self.ntc_limit}}
        if parsed.path=='/api/control/status':
            self.speed_reads+=1
            speed=self.speed+(5 if self.speed_reads%2 else -5) if self.speed_wobble and self.enabled and self.mode==2 else self.speed
            return {'control':{'mode':self.mode},'feedback':{'id_a':0.,'iq_a':self.current,
                'stale':False,'updated_at':time.time(),'position_at':time.time(),
                'position_stale':self.stale,'position_deg':self.position,'speed_rpm':speed}}
        if parsed.path=='/api/session':return {'token':'fake','command_epoch':1}
        if parsed.path=='/api/get':return {'value':1000 if q['id']==['7'] else self.speed_limit}
        if parsed.path=='/api/motor_profile':raise CurrentError('POST-only profile selection must not be called')
        if parsed.path=='/api/motor':
            if q['enable']==['0'] and self.stop_fail:raise CurrentError('stop ACK missing')
            self.enabled=q['enable']==['1']
            if self.enabled:self.mode=int(q['mode'][0])
            else:self.speed=0
        if parsed.path=='/api/control/set':
            value=float(q['value'][0]);field=q['field'][0]
            if field=='current_a':self.current=value
            if field=='position_deg':self.position=(value+self.position_error_deg)*10
            if field=='speed_rpm':self.speed=min(value,self.speed_response_cap) if self.speed_response_cap is not None else value
        return {'ok':True}


class AutomaticTestsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.motor=ScriptMotor();self.brake=False
        self.begin=time.time();self.sensor=True;self.torque_factor=2
        self.sensor_config={'protocol':'hex6','torqueDecimals':2,'speedDecimals':1}
        self.passive=SpecializedTests(Path(self.temp.name)/'passive',self.source,self.frames)
        self.auto=AutomaticTests(Path(self.temp.name)/'auto',self.motor,self.passive,self.source)

    def tearDown(self):
        if self.auto.active():self.auto.stop(self.auto.status()['id'])
        if self.passive.status():self.passive.stop(self.passive.status()['id'])
        self.temp.cleanup()

    def source(self):
        now=time.time()
        return {'motor':self.motor.status(),'brake':{'connected':True,'snapshot':{'receivedAt':now,'status':128 if self.brake else 0,'settings':[12,1]}},
                'sensor':{'connected':self.sensor,'config':self.sensor_config,'snapshot':{'receivedAt':now,'torque':self.motor.current*self.torque_factor,'speed':0}}}

    def frames(self,since):
        count=max(1,int((time.time()-self.begin)*1000))
        return {'frames':[(seq,self.begin+seq/1000,round(self.motor.current*100*self.torque_factor),0) for seq in range(since+1,count+1)],
                'config':self.sensor_config,'reset':False,'dropped':0}

    def config(self,**values):
        return dict(group='no_load',position=False,speed=True,speedSearchDurationS=5,currentMaxA=1,
                    dwellS=1,temperatureMaxC=40,speedStepRpm=1,**values)

    def finish(self,timeout=10):
        deadline=time.monotonic()+timeout
        while self.auto.thread.is_alive() and time.monotonic()<deadline:
            self.motor.lease=time.monotonic();self.auto.thread.join(.1)
        self.assertFalse(self.auto.thread.is_alive())
        result=self.auto.status()
        self.assertFalse(self.motor.run['running']);self.assertFalse(self.motor.enabled)
        self.assertEqual(result['stopConfirmed'],not self.motor.stop_fail)
        return result

    def test_invalid_config_and_preflight_never_enable_motor(self):
        for payload in ({},self.config()|{'speedStepRpm':0},self.config()|{'speedSearchDurationS':0},self.config()|{'currentMaxA':0},self.config()|{'temperatureMaxC':20}):
            with self.assertRaises(ValueError):self.auto.start(payload)
            self.assertEqual(self.motor.writes,[])
        self.motor.calibrated=False
        with self.assertRaises(CurrentError):self.auto.start(self.config())
        self.assertEqual(self.motor.writes,[])

    def test_velocity_search_needs_no_preset_maximum_and_marks_controller_limit(self):
        self.motor.position=63719.956
        record=self.auto.start(self.config()|{'speedMaxRpm':.1,'angleMinDeg':-1,'angleMaxDeg':1,'continuousRotation':False});result=self.finish()
        self.assertEqual(result['state'],'stopped');self.assertEqual(result['outcome'],'controller_limit')
        self.assertNotIn('speedMaxRpm',result['config'])
        self.assertNotIn('angleMinDeg',result['config']);self.assertNotIn('angleMaxDeg',result['config'])
        self.assertEqual(result['config']['rotationMode'],'continuous')
        self.assertFalse(result['speedSearch']['limitFound'])
        self.assertEqual(result['speedSearch']['maxMeanRpm'],2)
        self.assertFalse(result['protectionSeen']);self.assertEqual(result['peakOutputSpeedRpm'],2)
        speed_routes=[r for r in self.motor.writes if 'field=speed_rpm' in r]
        self.assertEqual([float(parse_qs(urlsplit(r).query)['value'][0]) for r in speed_routes],[0,10,20])
        self.assertIn('mode=2',' '.join(self.motor.writes))
        self.assertEqual(json.loads(self.auto.file(record['id'],'report.json').read_text())['outcome'],'controller_limit')

    def test_speed_plateau_is_confirmed_without_further_increase(self):
        self.motor.speed_limit=30;self.motor.speed_response_cap=15
        self.auto.start(self.config());result=self.finish()
        self.assertEqual(result['outcome'],'speed_plateau')
        self.assertEqual(result['state'],'completed');self.assertFalse(result['protectionSeen'])
        search=result['speedSearch']
        self.assertTrue(search['limitFound']);self.assertEqual(search['maxMeanRpm'],1.5)
        self.assertEqual(search['platformTargetRpm'],2)
        self.assertEqual([row['attempt'] for row in result['steps']],[1,1,2])
        self.assertFalse(any('value=30' in route for route in self.motor.writes))

    def test_unstable_speed_is_not_reported_as_a_measured_limit(self):
        self.motor.speed_wobble=True;self.motor.speed_response_cap=15
        self.auto.start(self.config()|{'speedToleranceRpm':.000001});result=self.finish()
        self.assertEqual(result['outcome'],'controller_limit')
        self.assertNotIn('speedToleranceRpm',result['config'])
        self.assertFalse(result['speedSearch']['limitFound'])
        self.assertGreater(result['speedSearch']['maxMeanRpm'],0)
        self.assertTrue(any(row['speedStdRpm']>0 for row in result['steps']))

    def test_no_rotation_is_not_reported_as_a_zero_speed_limit(self):
        self.motor.speed_limit=30;self.motor.speed_response_cap=0
        self.auto.start(self.config());result=self.finish()
        self.assertEqual(result['outcome'],'controller_limit')
        self.assertFalse(result['speedSearch']['limitFound'])
        self.assertEqual(result['speedSearch']['maxMeanRpm'],0)

    def test_speed_search_time_budget_preserves_incomplete_measurement(self):
        self.auto.start(self.config()|{'dwellS':2,'speedSearchDurationS':1});result=self.finish()
        self.assertEqual(result['outcome'],'speed_search_timeout')
        self.assertFalse(result['speedSearch']['limitFound'])
        self.assertIsNone(result['speedSearch']['maxMeanRpm'])

    def test_speed_protection_keeps_last_stable_speed(self):
        self.motor.speed_limit=30;self.motor.speed_fault_at=20
        self.auto.start(self.config());result=self.finish()
        self.assertEqual(result['outcome'],'protection');self.assertTrue(result['protectionSeen'])
        self.assertEqual(result['speedSearch']['maxMeanRpm'],1)
        self.assertFalse(result['speedSearch']['limitFound'])

    def test_position_forward_reverse_uses_existing_output_coordinate(self):
        self.motor.position=63719.956
        self.auto.start(self.config()|{'position':True,'speed':False,'positionsDeg':[-720,720],'cycles':1,'positionToleranceDeg':.1})
        result=self.finish()
        self.assertEqual(result['state'],'completed');self.assertEqual(len(result['positionResults']),4)
        self.assertTrue(all(row['errorDeg']==0 for row in result['steps']))
        self.assertEqual([row['targetDeg'] for row in result['steps']],[-720,720,720,-720])
        self.assertAlmostEqual(result['originOutputDeg'],6371.9956)
        self.assertIn('mode=3',' '.join(self.motor.writes))
        self.assertFalse(any('/api/set?' in r or 'save' in r for r in self.motor.writes))

    def test_position_protocol_range_is_checked_before_any_motion(self):
        for points in ([-200001,1],[float('nan'),1],[-1,float('inf')]):
            with self.assertRaises(ValueError):
                self.auto.start(self.config()|{'position':True,'speed':False,'positionsDeg':points,'cycles':1,'positionToleranceDeg':.1})
        self.motor.position=30000000
        with self.assertRaisesRegex(ValueError,'协议'):
            self.auto.start(self.config()|{'position':True,'speed':False,'positionsDeg':[-1,1],'cycles':1,'positionToleranceDeg':.1})
        self.assertEqual(self.motor.writes,[])

    def test_position_error_is_measured_without_acceptance_abort(self):
        self.motor.position_error_deg=2
        self.auto.start(self.config()|{'position':True,'speed':False,'positionsDeg':[-1,1],
                                    'cycles':1,'positionToleranceDeg':.001})
        result=self.finish()
        self.assertEqual(result['state'],'completed');self.assertEqual(len(result['steps']),4)
        self.assertNotIn('positionToleranceDeg',result['config'])
        for row in result['steps']:
            self.assertEqual(row['errorDeg'],2);self.assertEqual(row['endErrorDeg'],2)
            self.assertEqual(row['errorRmsDeg'],2);self.assertEqual(row['maxAbsErrorDeg'],2)

    def test_device_protection_is_read_and_relative_points_use_start_coordinate(self):
        self.motor.position=63719.956
        payload=self.config()|{'position':True,'speed':False,'positionsDeg':[-10,10],'cycles':1,
                              'positionReference':'relative','currentMaxA':None,'temperatureMaxC':None}
        self.auto.start(payload);result=self.finish()
        self.assertEqual(result['config']['currentMaxA'],1)
        self.assertEqual(result['config']['temperatureMaxC'],60)
        self.assertEqual(result['protectionSources'],{'current':'device','temperature':'device'})
        self.assertAlmostEqual(result['steps'][0]['targetDeg'],6361.9956)
        self.assertTrue(all(abs(row['errorDeg'])<1e-9 for row in result['steps']))

    def test_step_defaults_and_missing_temperature_limit_never_start_motion(self):
        config=AutomaticTests.validate({'group':'no_load','position':True,'speed':True,'positionReference':'relative'})
        self.assertEqual(config['positionsDeg'],[-10,0,10]);self.assertEqual(config['dwellS'],2)
        self.assertEqual(config['speedStepRpm'],1);self.assertEqual(config['cycles'],2)
        self.motor.ntc_limit=None
        with self.assertRaisesRegex(ValueError,'NTC'):
            self.auto.start(self.config()|{'currentMaxA':None,'temperatureMaxC':None})
        self.assertEqual(self.motor.writes,[])

    def test_optional_protection_cannot_exceed_device_limit(self):
        for values in ({'currentMaxA':2},{'temperatureMaxC':61}):
            with self.assertRaises(ValueError):self.auto.start(self.config()|values)
        self.assertEqual(self.motor.writes,[])

    def test_zero_torque_is_recorded_without_a_minimum_acceptance_gate(self):
        self.brake=True;self.torque_factor=0
        self.auto.start(self.config()|{'group':'loaded','currentStepA':.5,'loadConfirmed':True,'minLoadTorqueNm':50})
        result=self.finish()
        self.assertEqual(result['state'],'completed');self.assertEqual(len(result['steps']),2)
        self.assertEqual(result['measuredPeakTorqueNm'],0)
        self.assertNotIn('minLoadTorqueNm',result['config'])

    def test_loaded_requires_brake_and_sensor_then_records_raw_peak(self):
        config=self.config()|{'group':'loaded','position':True,'speed':True,'currentStepA':.5,'minLoadTorqueNm':.1,'loadConfirmed':True}
        with self.assertRaisesRegex(ValueError,'制动'):self.auto.start(config)
        self.brake=True;self.sensor=False
        with self.assertRaisesRegex(ValueError,'传感器'):self.auto.start(config)
        self.assertEqual(self.motor.writes,[]);self.sensor=True
        self.auto.start(config);result=self.finish()
        self.assertFalse(result['config']['position']);self.assertFalse(result['config']['speed'])
        self.assertEqual(result['state'],'completed');self.assertEqual(result['measuredPeakTorqueNm'],2)
        self.assertEqual(result['postStopTorqueAbsMaxNm'],0)
        with self.passive.file(result['rawTorqueRecordId'],'data.csv').open(encoding='utf-8-sig') as stream:
            self.assertGreater(sum(1 for r in csv.DictReader(stream) if r['sample_type']=='sensor'),2000)
        self.assertIn('mode=5',' '.join(self.motor.writes))

    def test_real_protection_stops_without_claiming_normal_completion(self):
        self.brake=True;self.motor.fault_at=.5
        self.auto.start(self.config()|{'group':'loaded','currentStepA':.5,'minLoadTorqueNm':.1,'loadConfirmed':True})
        result=self.finish()
        self.assertEqual(result['outcome'],'protection');self.assertTrue(result['protectionSeen'])
        self.assertFalse(any('value=1.0' in r for r in self.motor.writes))

    def test_stale_position_and_page_timeout_both_stop(self):
        for fault in ('stale','lease'):
            self.motor.stale=False
            self.auto.start(self.config())
            if fault=='stale':self.motor.stale=True
            else:self.motor.lease=time.monotonic()-3
            self.auto.thread.join(4)
            self.assertFalse(self.auto.thread.is_alive())
            self.assertTrue(self.auto.status()['stopConfirmed'])
            self.assertEqual(self.auto.status()['outcome'],'feedback_lost' if fault=='stale' else 'page_timeout')

    def test_cancel_and_conflicting_tasks(self):
        record=self.auto.start(self.config())
        with self.assertRaises(ValueError):self.auto.start(self.config())
        with self.assertRaises(CurrentError):self.motor.start({'stepA':.1,'intervalS':1,'durationS':10})
        result=self.auto.stop(record['id'])
        self.assertEqual(result['outcome'],'operator_stop');self.assertTrue(result['stopConfirmed'])

    def test_stop_failure_is_not_reported_as_confirmed(self):
        record=self.auto.start(self.config());self.motor.stop_fail=True
        result=self.auto.stop(record['id'])
        self.assertFalse(result['stopConfirmed']);self.assertFalse(self.motor.status()['connected'])
        self.assertIn('stop ACK missing',result['reason'])

    def test_record_paths_and_interrupted_recovery(self):
        directory=Path(self.temp.name)/'auto'/('a'*32);directory.mkdir(parents=True)
        (directory/'report.json').write_text(json.dumps({'id':'a'*32,'running':True,'startedAt':1}))
        self.auto.recover_interrupted()
        self.assertEqual(self.auto.history()[0]['outcome'],'service_restart')
        with self.assertRaises(ValueError):self.auto.file('../escape','report.json')

    def test_http_routes_require_token_and_lock_conflicting_operations(self):
        from http.server import ThreadingHTTPServer
        from unittest.mock import patch
        import urllib.request
        import urllib.error
        import hardware_bridge as bridge
        class Quiet(bridge.Handler):
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Quiet);port=server.server_address[1]
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        def post(path,data,token=bridge.TOKEN):
            req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=json.dumps(data).encode(),
                headers={'Content-Type':'application/json','X-MotorBench-Token':token})
            with urllib.request.urlopen(req) as response:return json.load(response)
        with patch.object(bridge,'PORT',port),patch.object(bridge,'AUTOMATIC',self.auto),patch.object(bridge,'CURRENT_MOTOR',self.motor),patch.object(bridge,'SPECIALIZED',self.passive):
            worker.start()
            try:
                with self.assertRaises(urllib.error.HTTPError) as failure:post('/api/automatic/start',self.config(),'invalid')
                self.assertEqual(failure.exception.code,403);self.assertEqual(self.motor.writes,[])
                record=post('/api/automatic/start',self.config())['current']
                for path,data in [('/api/supplies/brake-supply/set',{'voltage':12,'current':1}),
                                  ('/api/sensors/torque-sensor/disconnect',{}),
                                  ('/api/specialized/start',{'kind':'baseline'})]:
                    with self.assertRaises(urllib.error.HTTPError) as failure:post(path,data)
                    self.assertEqual(failure.exception.code,400)
                stopped=post('/api/automatic/stop',{'id':record['id']})['current']
                self.assertTrue(stopped['stopConfirmed'])
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/automatic/files/{record["id"]}/data.csv') as response:
                    self.assertIn(b'output_position_deg',response.read())
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/automatic/status?history=0') as response:
                    self.assertNotIn('records',json.load(response))
            finally:server.shutdown();server.server_close();worker.join(2)


if __name__=='__main__':unittest.main()
