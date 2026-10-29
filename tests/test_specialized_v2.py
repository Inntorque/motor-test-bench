"""Specialized V2 uses fake devices and temp files; never connects hardware."""
import csv
import json
from pathlib import Path
import tempfile
import time
import unittest
from automatic_tests import AutomaticTests
from specialized_tests import SpecializedTests
from workbench.specialized_metrics import performance,positioning,thermal
from workbench.specialized_runner import config
from test_motor_current import FakeMotor


class MetricsTests(unittest.TestCase):
    def test_power_density_uses_one_point_and_never_multiplies_independent_extrema(self):
        windows=[{'valid':True,'steady':True,'phase':'scan','n':100,'torque':2,'powerW':20.94395,'iq':1,'id':0},
                 {'valid':True,'steady':True,'phase':'scan','n':10,'torque':10,'powerW':10.471975,'iq':3,'id':0}]
        result=performance(windows,None,{'massKg':2,'volumeL':1},[])
        self.assertEqual(result['maxObservedPowerPoint']['n'],100)
        self.assertAlmostEqual(result['densities']['shortTimePowerWPerKg'],10.471975)
        self.assertIsNone(result['outputEquivalentKt'])
        self.assertEqual(result['continuousRating'],'not_certified_by_short_time_sweep')

    def test_external_reference_is_separate_and_directional_repetition_is_not_feedback_accuracy(self):
        visits=[{'visitId':i+1,'direction':direction,'nominalDeg':10,'targetDeg':100,'meanDeg':100.01}
                for i,direction in enumerate(['forward','forward','reverse','reverse'])]
        feedback=positioning(visits)
        self.assertFalse(feedback['externalCoverageComplete']);self.assertIsNone(feedback['externalMaxAbsMeanErrorDeg'])
        measured=positioning(visits,{'1':30.1,'2':30.12,'3':29.9,'4':29.92},{'aligned':True,'name':'external encoder','zeroDeg':20})
        self.assertTrue(measured['externalCoverageComplete'])
        self.assertAlmostEqual(measured['externalWorstRepeatRangeDeg'],.02)
        self.assertAlmostEqual(measured['externalReversalDifferenceDeg'],.2)

    def test_thermal_requires_time_span_and_load_stability_and_does_not_invent_ambient(self):
        points=[dict(t=t,temperatureC=30,phase='heating',iq=1,speedRpm=5,torqueNm=2) for t in range(11)]
        result=thermal(points,{'thermalWindowS':10,'thermalSlopeCPerMin':.1})
        self.assertTrue(result['stable']);self.assertIsNone(result['riseK'])
        points[-1]['torqueNm']=4
        self.assertFalse(thermal(points,{'thermalWindowS':10})['stable'])
        self.assertFalse(thermal(points[:2],{'thermalWindowS':10})['stable'])

    def test_long_recipe_and_missing_mass_do_not_acquire_legacy_time_limits(self):
        value=config({'suite':'thermal','durationS':7200,'ratio':9.67})
        self.assertEqual(value['durationS'],7200)
        self.assertIsNone(value['massKg'])
        with self.assertRaises(ValueError):config({'suite':'thermal','axis':'motor','ratio':9.67})


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.motor=FakeMotor();self.begin=time.time()
        self.passive=SpecializedTests(Path(self.temp.name)/'passive',self.source,self.frames)
        self.auto=AutomaticTests(Path(self.temp.name)/'active',self.motor,self.passive,self.source)
        self.motor._refresh()

    def tearDown(self):
        if self.auto.active():self.auto.stop(self.auto.status()['id'])
        if self.passive.status() and self.passive.thread:self.passive.stop(self.passive.status()['id'])
        if self.auto.recorder and self.auto.recorder.active():self.auto.recorder.stop(self.auto.recorder.status()['id'])
        self.temp.cleanup()

    def source(self):
        snapshot=self.motor.device.latest_snapshot()
        snapshot['receivedAt']=time.time()
        return {'motor':{'connected':True,'snapshot':snapshot,'run':dict(self.motor.run)},
                'sensor':{'connected':True,'config':{'torqueDecimals':2,'speedDecimals':1},'snapshot':{'receivedAt':time.time(),'torque':self.motor.current*2,'speed':self.motor.speed/10}},
                'brake':{'connected':True,'snapshot':{'receivedAt':time.time(),'status':0}}}

    def frames(self,cursor):
        count=int((time.time()-self.begin)*1000)
        return {'frames':[(seq,self.begin+seq/1000,round(self.motor.current*200),0) for seq in range(cursor+1,count+1)],
                'config':{'torqueDecimals':2,'speedDecimals':1},'reset':False,'dropped':0}

    def wait(self,manager,timeout=6):
        manager.thread.join(timeout)
        self.assertFalse(manager.thread.is_alive())
        return json.loads(manager.file(manager.status()['id'],'report.json').read_text())

    def test_position_visits_have_both_directions_and_stop_confirmation(self):
        report=self.auto.start({'suite':'position','ratio':10,'positionsDeg':[0,20],'cycles':2,'approachDeg':1,
                                'settleS':.01,'dwellS':.2,'motionConfirmed':True})
        result=self.wait(self.auto)
        self.assertEqual(result['state'],'completed',result.get('reason'))
        self.assertEqual(len(result['positionVisits']),8)
        self.assertTrue(result['stopConfirmed']);self.assertFalse(self.motor.enabled)
        self.assertIsNone(result['positionMetrics']['externalMaxAbsMeanErrorDeg'])
        self.assertEqual({v['direction'] for v in result['positionVisits']},{'forward','reverse'})
        self.assertTrue(self.auto.file(report['id'],'timeline.jsonl').is_file())

    def test_kt_fit_uses_actual_current_plateaus_and_raw_frames_are_kept(self):
        report=self.auto.start({'suite':'performance','test':'kt','ratio':10,'iqLevelsA':[.2,.4,.8],
                               'settleS':.1,'dwellS':.7,'windowS':.2,'motionConfirmed':True})
        result=self.wait(self.auto)
        self.assertEqual(result['state'],'completed',result.get('reason'))
        self.assertAlmostEqual(result['performance']['outputEquivalentKt']['slope'],2,places=2)
        with self.auto.file(report['id'],'data.csv').open(encoding='utf-8-sig') as stream:rows=list(csv.DictReader(stream))
        self.assertGreater(sum(row['source']=='sensor' for row in rows),1500)
        self.assertTrue(all(row['iq_A']=='' for row in rows if row['source']=='sensor'))
        self.assertFalse(self.motor.enabled)

    def test_unconfirmed_command_is_not_retried_and_stops_device(self):
        self.motor.fail='write'
        self.auto.start({'suite':'thermal','target':.5,'durationS':.3,'motionConfirmed':True})
        result=self.wait(self.auto)
        self.assertEqual(result['state'],'interrupted')
        self.assertEqual(self.motor.positive_writes(),[.5])
        self.assertTrue(result['stopConfirmed']);self.assertFalse(self.motor.enabled)

    def test_temperature_observation_never_sends_an_actuator_command(self):
        report=self.passive.start({'suite':'thermal','ratio':10,'durationS':.35,'ambientC':20})
        result=self.wait(self.passive)
        self.assertEqual(self.motor.writes,[])
        self.assertEqual(result['state'],'completed',result.get('reason'))
        self.assertEqual(result['thermal']['riseK'],5)
        self.assertTrue(self.passive.file(report['id'],'timeline.jsonl').is_file())

    def test_stop_cancels_long_run_without_an_extra_enable(self):
        self.auto.start({'suite':'thermal','target':.5,'durationS':7200,'motionConfirmed':True})
        time.sleep(.15);self.auto.stop(self.auto.status()['id'])
        result=self.wait(self.auto)
        self.assertEqual(result['state'],'stopped')
        self.assertEqual(sum(write[0]=='start' for write in self.motor.writes),1)
        self.assertTrue(result['stopConfirmed'])

    def test_wire_range_failure_is_preflight_and_never_enables(self):
        with self.assertRaisesRegex(ValueError,'64'):
            self.auto.start({'suite':'position','ratio':10,'positionsDeg':[0,1e30],'motionConfirmed':True})
        self.assertEqual(self.motor.writes,[])

    def test_repeated_temperature_source_is_not_counted_as_new_measurements(self):
        def fixed_source():
            source=self.source();source['motor']['snapshot']['ntc']['sourceDeviceTimeUs']='1234';return source
        self.passive.source=fixed_source
        self.passive.start({'suite':'thermal','durationS':.35,'ambientC':20})
        result=self.wait(self.passive)
        self.assertEqual(result['thermal']['count'],1)
        self.assertFalse(result['thermal']['stable'])

    def test_existing_manual_recording_is_linked_but_not_closed(self):
        from workbench.recording import RecordManager
        self.auto.recorder=RecordManager(Path(self.temp.name)/'recordings',self.source,self.frames)
        original=self.auto.recorder.start({'name':'manual'})
        self.auto.start({'suite':'thermal','durationS':.3,'target':.5,'motionConfirmed':True})
        result=self.wait(self.auto)
        self.assertEqual(result['state'],'completed',result.get('reason'))
        self.assertEqual(result['recordingId'],original['id'])
        self.assertTrue(self.auto.recorder.active())

    def test_new_recipe_uses_existing_authenticated_automatic_endpoint(self):
        import threading,urllib.request
        from http.server import ThreadingHTTPServer
        from unittest.mock import patch
        from workbench.service import Handler,devices
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.lan_access=None;server.closing=threading.Event()
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            with patch.object(devices,'AUTOMATIC',self.auto):
                request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/v1/automatic/start',
                    data=json.dumps({'suite':'thermal','durationS':.2,'target':.5,'motionConfirmed':True}).encode(),
                    headers={'Content-Type':'application/json','X-MotorBench-Token':devices.TOKEN})
                with urllib.request.urlopen(request,timeout=3) as response:report=json.load(response)['current']
                self.assertEqual(report['category'],'thermal')
                self.assertEqual(self.wait(self.auto)['state'],'completed')
        finally:server.shutdown();server.server_close();worker.join(2)
