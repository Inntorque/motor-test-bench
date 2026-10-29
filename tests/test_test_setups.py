"""Mechanical configuration tests use fake devices and temporary records only."""
import csv
import json
from pathlib import Path
import tempfile
import time
import unittest
from automatic_tests import AutomaticTests
from specialized_tests import SpecializedTests
from workbench.subjects import TestSubjects, setup_columns
from workbench.recording import RecordManager
from protocol import generated as p
from test_motor_current import FakeMotor


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'subjects.json'
        self.catalog=TestSubjects(self.path)
        self.motor=self.catalog.create('motor','电机 A')['item']
        self.other=self.catalog.create('motor','电机 B')['item']

    def tearDown(self):self.temp.cleanup()

    def setup(self,**values):
        return self.catalog.save_setup({'motorId':self.motor['id'],'name':'裸机','hasGearbox':False,'ratio':1,**values})['item']

    def test_one_motor_has_independent_bare_and_geared_setups_and_revisions(self):
        bare=self.setup(massKg=.5)
        geared=self.setup(name='减速箱版本',hasGearbox=True,ratio=9.67,gearboxName='箱 A',massKg=1.2)
        self.catalog.select({'motorId':self.motor['id'],'testSetupId':bare['id']})
        original=self.catalog.selection()
        self.catalog.select({'motorId':self.motor['id'],'testSetupId':geared['id']})
        edited=self.setup(**{**geared,'ratio':100})
        current=TestSubjects(self.path).selection()
        self.assertEqual(current['testSetup']['ratio'],100)
        self.assertEqual(edited['revision'],2)
        self.assertEqual(original['testSetup']['ratio'],1)
        self.assertEqual(original['testSetup']['massKg'],.5)
        with self.assertRaises(ValueError):self.catalog.select({'motorId':self.other['id'],'testSetupId':bare['id']})
        self.catalog.select({'motorId':self.other['id']})
        self.assertNotIn('testSetup',self.catalog.selection())

    def test_invalid_setup_does_not_overwrite_catalog_or_accept_wrong_motor(self):
        bare=self.setup();before=self.path.read_bytes()
        for ratio in (0,-1,True,float('nan'),float('inf')):
            with self.subTest(ratio=ratio), self.assertRaises(ValueError):self.setup(name='bad',ratio=ratio,hasGearbox=True)
        for data in ({'massKg':0},{'volumeL':True},{'ratio':2},{'name':'裸机'},
                     {'id':bare['id'],'motorId':self.other['id']},{'motorId':'missing'}):
            with self.subTest(data=data),self.assertRaises(ValueError):self.setup(**data)
        self.assertEqual(self.path.read_bytes(),before)

    def test_active_task_locks_setup_edits_and_selection_without_changing_disk(self):
        bare=self.setup();self.catalog.select({'motorId':self.motor['id'],'testSetupId':bare['id']})
        before=self.path.read_bytes();self.catalog.busy=lambda:True
        self.assertTrue(self.catalog.status()['configurationLocked'])
        with self.assertRaises(ValueError):self.setup(**{**bare,'name':'changed'})
        with self.assertRaises(ValueError):self.catalog.select({'motorId':self.other['id']})
        self.assertEqual(self.path.read_bytes(),before)

    def test_old_ratio_is_imported_once_only_for_original_motor_not_selected_other_motor(self):
        old={'motors':[self.other], 'driverBoards':[], 'selected':{'motorId':self.other['id'],'driverBoardId':''}}
        self.path.write_text(json.dumps(old))
        legacy={'motor':{'label':'SG-6010HB','nameplate':{'reduction_ratio':9.67}}}
        migrated=TestSubjects(self.path,legacy)
        data=migrated.status();self.assertEqual(data['selected']['motorId'],self.other['id'])
        setup=data['testSetups'][0]
        self.assertFalse(setup['ratioConfirmed']);self.assertEqual(setup['ratio'],9.67)
        self.assertNotEqual(setup['motorId'],self.other['id'])
        self.assertNotIn('testSetup',migrated.selection())
        self.assertEqual(len(TestSubjects(self.path,legacy).status()['testSetups']),1)
        self.assertTrue(all(v is None for v in setup_columns(migrated.selection()).values()))


class SetupExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.catalog=TestSubjects(self.root/'subjects.json');self.motor_entry=self.catalog.create('motor','同一电机')['item']
        self.motor=FakeMotor();self.begin=time.time()
        self.passive=SpecializedTests(self.root/'passive',self.source,self.frames,subjects=self.catalog.selection)
        self.auto=AutomaticTests(self.root/'active',self.motor,self.passive,self.source,subjects=self.catalog.selection)
        self.recorder=RecordManager(self.root/'records',self.source,self.frames,subjects=self.catalog.selection)
        self.auto.recorder=self.recorder
        self.motor.subjects=self.catalog.selection;self.motor._refresh()

    def tearDown(self):
        if self.auto.active():self.auto.stop(self.auto.status()['id'])
        if self.passive.thread and self.passive.thread.is_alive():self.passive.stop(self.passive.status()['id'])
        if self.recorder.active():self.recorder.stop(self.recorder.status()['id'])
        self.temp.cleanup()

    def source(self):
        snapshot=self.motor.device.latest_snapshot();snapshot['receivedAt']=time.time()
        return {'motor':{'connected':True,'snapshot':snapshot,'testSubject':self.catalog.selection()},
                'sensor':{'connected':True,'config':{'torqueDecimals':2,'speedDecimals':1},'snapshot':{'receivedAt':time.time(),'torque':2,'speed':3}}}

    def frames(self,cursor):
        end=int((time.time()-self.begin)*1000)
        return {'frames':[(seq,self.begin+seq/1000,200,30) for seq in range(cursor+1,end+1)],
                'config':{'torqueDecimals':2,'speedDecimals':1},'reset':False,'dropped':0}

    def choose(self,ratio):
        item=self.catalog.save_setup({'motorId':self.motor_entry['id'],'name':str(ratio),'hasGearbox':ratio!=1,'ratio':ratio,'massKg':ratio})['item']
        self.catalog.select({'motorId':self.motor_entry['id'],'testSetupId':item['id']})
        return item

    def finish(self,manager):
        manager.thread.join(6);self.assertFalse(manager.thread.is_alive())
        report=json.loads(manager.file(manager.status()['id'],'report.json').read_text())
        self.assertEqual(report['state'],'completed',report.get('reason'))
        return report

    def test_selected_ratio_controls_position_velocity_and_all_linked_record_values(self):
        for ratio in (1,9.67,100):
            with self.subTest(ratio=ratio):
                item=self.choose(ratio);self.motor.position=0;self.motor.writes.clear()
                self.auto.start({'suite':'position','ratio':999,'testSetupId':item['id'],
                                 'testSetupRevision':item['revision'],'positionsDeg':[0,1],'cycles':1,
                                 'approachDeg':.1,'settleS':.01,'dwellS':.2,'motionConfirmed':True})
                report=self.finish(self.auto)
                self.assertEqual(report['config']['ratio'],ratio)
                self.assertEqual(report['testSubject']['testSetup'],item)
                self.assertIn(('target',p.OBJ_POSITION_TARGET,round(ratio/360*1000000)),self.motor.writes)
                linked=json.loads(self.recorder.file(report['recordingId'],'report.json').read_text())
                self.assertEqual(linked['ratio'],ratio)
                self.assertEqual(linked['testSubject'],report['testSubject'])
                with self.auto.file(report['id'],'data.csv').open(encoding='utf-8-sig') as stream:rows=list(csv.DictReader(stream))
                motors=[row for row in rows if row['source']=='motor']
                self.assertTrue(motors)
                for row in motors:
                    self.assertEqual(float(row['reduction_ratio']),ratio)
                    self.assertEqual(row['test_setup_id'],item['id'])
                    self.assertAlmostEqual(float(row['position_deg']),float(row['rotor_position_deg'])/ratio)
                sensor=next(row for row in rows if row['source']=='sensor')
                self.assertEqual(float(sensor['speed_rpm']),3)
                self.assertEqual(float(sensor['torque_Nm']),2)
                self.motor.writes.clear()
                self.auto.start({'suite':'performance','test':'point','controlMode':'VELOCITY','target':.1,
                                 'testSetupId':item['id'],'durationS':.2,'motionConfirmed':True})
                self.finish(self.auto)
                self.assertIn(('target',p.OBJ_VELOCITY_TARGET,round(.1*ratio*1000)),self.motor.writes)

    def test_record_freezes_ratio_and_names_while_catalog_is_edited_after_start(self):
        item=self.choose(9.67);self.motor.position=96.7;self.motor.speed=193.4
        report=self.recorder.start({})
        self.catalog.save_setup({**item,'name':'later','ratio':50})
        time.sleep(.16);report=self.recorder.stop(report['id'])
        self.assertEqual(report['ratio'],9.67)
        self.assertEqual(report['testSubject']['testSetup']['name'],'9.67')
        with self.recorder.file(report['id'],'timeline.csv').open(encoding='utf-8-sig') as stream:rows=list(csv.DictReader(stream))
        motor=next(row for row in rows if row['source']=='motor')
        self.assertEqual(float(motor['output_position_deg']),10)
        self.assertEqual(float(motor['output_speed_rpm']),20)
        self.assertEqual(float(motor['reduction_ratio']),9.67)
        self.assertTrue(all(row['test_setup_name']=='9.67' for row in rows))

    def test_missing_or_stale_setup_never_uses_device_profile_or_starts_motor(self):
        with self.assertRaises(ValueError):self.auto.start({'suite':'thermal','motionConfirmed':True})
        item=self.choose(9.67)
        for data in ({'testSetupId':'other'},{'testSetupRevision':0}):
            with self.assertRaises(ValueError):self.auto.start({'suite':'thermal','motionConfirmed':True,**data})
        self.assertEqual(self.motor.writes,[])
        status=self.motor.status();self.assertEqual(status['testSubject']['testSetup']['ratio'],9.67)
        self.assertEqual(status['snapshot']['motorProfile']['nameplate']['reduction_ratio'],10)
        self.assertEqual(item['id'],status['testSubject']['testSetup']['id'])

    def test_passive_capture_uses_selected_setup_and_shared_record_without_actuator_writes(self):
        item=self.choose(1)
        self.passive.start({'suite':'thermal','ratio':999,'axis':'output','durationS':.3})
        report=self.finish(self.passive)
        self.assertEqual(report['config']['ratio'],1);self.assertEqual(report['config']['axis'],'motor')
        self.assertEqual(self.motor.writes,[])
        linked=json.loads(self.recorder.file(report['recordingId'],'report.json').read_text())
        self.assertEqual(linked['testSubject']['testSetup'],item)

    def test_unknown_ratio_keeps_record_raw_values_and_does_not_assume_one(self):
        self.motor.position=10;report=self.recorder.start({});time.sleep(.1);self.recorder.stop(report['id'])
        with self.recorder.file(report['id'],'timeline.csv').open(encoding='utf-8-sig') as stream:rows=list(csv.DictReader(stream))
        motor=next(row for row in rows if row['source']=='motor')
        self.assertEqual(float(motor['rotor_position_deg']),10)
        self.assertEqual(motor['output_position_deg'],'');self.assertEqual(motor['reduction_ratio'],'')

    def test_setup_api_requires_token_and_locks_configuration_without_motor_commands(self):
        import threading
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer
        from unittest.mock import patch
        from workbench.service import Handler, devices
        item=self.choose(9.67)
        locked=False
        self.catalog.busy=lambda:locked
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server.lan_access=None;server.closing=threading.Event()
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def post(action,data,token=devices.TOKEN):
            request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/v1/test-subjects/{action}',
                data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-MotorBench-Token':token})
            with urllib.request.urlopen(request,timeout=3) as response:return json.load(response)
        try:
            with patch.object(devices,'TEST_SUBJECTS',self.catalog):
                before=self.catalog.path.read_bytes()
                with self.assertRaises(urllib.error.HTTPError) as error:post('save-setup',{**item,'ratio':100},'wrong')
                self.assertEqual(error.exception.code,403);self.assertEqual(self.catalog.path.read_bytes(),before)
                changed=post('save-setup',{**item,'ratio':100})['item']
                self.assertEqual(changed['ratio'],100)
                locked=True
                for action,data in [('save-setup',{**changed,'ratio':2}),('select',{'motorId':self.motor_entry['id']})]:
                    with self.assertRaises(urllib.error.HTTPError) as error:post(action,data)
                    self.assertEqual(error.exception.code,400)
                self.assertEqual(self.catalog.selection()['testSetup']['ratio'],100)
                self.assertEqual(self.motor.writes,[])
        finally:server.shutdown();server.server_close();worker.join(2)
