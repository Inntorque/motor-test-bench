"""Homepage acquisition checks with generated data and no hardware/control objects."""
import csv
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from workbench_recordings import WorkbenchRecordings
from background_recording import BackgroundRecording


class WorkbenchRecordingTests(unittest.TestCase):
    def test_background_follows_homepage_start_stop_and_restarts_cleanly(self):
        diagnostics = BackgroundRecording(Path(self.temp.name)/'background',self.source,self.frames)
        self.manager.diagnostics = diagnostics
        self.assertFalse(diagnostics.root.exists())
        directories=[]
        for index in range(2):
            report=self.manager.start({'name':f'linked-{index}'})
            directory=Path(report['backgroundDirectory'])
            directories.append(directory)
            diagnostics.observe('operation_request',{'requestId':f'only-{index}'})
            time.sleep(.06)
            stopped=self.manager.stop(report['id'])
            self.assertFalse(stopped['running'])
            self.assertFalse(diagnostics.thread.is_alive())
            manifest=json.loads((directory/'manifest.json').read_text())
            self.assertEqual(manifest['workbenchRecording']['id'],report['id'])
            self.assertEqual(manifest['counts']['session_start'],1)
            self.assertEqual(manifest['counts']['operation_request'],1)
            self.assertFalse(manifest['running'])
            files=list(directory.glob('events-*.jsonl'))
            sizes=[f.stat().st_size for f in files]
            diagnostics.observe('must_not_record',{})
            time.sleep(.03)
            self.assertEqual(sizes,[f.stat().st_size for f in files])
        self.assertNotEqual(*directories)

    def test_recording_error_also_closes_linked_background(self):
        diagnostics=BackgroundRecording(Path(self.temp.name)/'background',self.source,self.frames)
        self.manager.diagnostics=diagnostics
        self.manager.start({})
        self.drop=True
        deadline=time.monotonic()+2
        while self.manager.active() and time.monotonic()<deadline:time.sleep(.02)
        self.assertFalse(self.manager.active())
        self.assertFalse(diagnostics.thread.is_alive())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.origin = time.time()
        self.connected = True
        self.sensor = True
        self.drop = False
        self.target = .2
        self.frame_config = {'protocol':'hex6','torqueDecimals':2,'speedDecimals':1}
        self.manager = WorkbenchRecordings(Path(self.temp.name), self.source, self.frames)

    def tearDown(self):
        if self.manager.active():self.manager.stop(self.manager.status()['id'])
        self.temp.cleanup()

    def source(self):
        now = time.time()
        supply = {'connected':self.connected, 'config':{'protocol':'ssj'},
                  'snapshot':{'receivedAt':now,'measured':[2400,100,24],'settings':[1,2500,200],
                              'status':128,'futureField':123,'config':{'voltageDecimals':2,'currentDecimals':2}}}
        return {'motorSupply':supply, 'brake':supply,
                'motor':{'connected':self.connected, 'run':{'targetA':self.target,'running':True},
                         'snapshot':{'receivedAt':now,'feedback':{'iq_a':.21,'id_a':0,'speed_rpm':20,'position_deg':10},
                                     'ntc':{'temp_c':26},'motor':{'output_on':True,'mode':5}}},
                'sensor':{'connected':self.connected and self.sensor,'config':self.frame_config if self.sensor else None,
                          'snapshot':{'receivedAt':now,'torque':1.2,'speed':10} if self.sensor else None}}

    def frames(self, cursor):
        if not self.sensor:return None
        count = int((time.time()-self.origin)*1000)
        return {'frames':[(seq,self.origin+seq/1000,120,100) for seq in range(cursor+1,count+1)],
                'config':self.frame_config,'reset':False,'dropped':1 if self.drop else 0}

    def test_full_record_exceeds_preview_limit_without_changing_devices(self):
        report = self.manager.start({'name':'首页记录','range':50})
        time.sleep(.1)
        self.target = .7
        self.manager.observe('operation_request',{'path':'/api/supplies/motor-supply/set','request':{'voltage':25,'current':2}})
        self.manager.observe('motor_command_ack',{'route':'/api/control/set?field=current_a&value=0.7','response':{'ok':True}})
        time.sleep(1.55)
        report = self.manager.stop(report['id'])
        self.assertEqual(self.target, .7)
        self.assertTrue(self.connected)
        self.assertEqual(report['state'],'completed')
        self.assertGreater(report['counts']['sensor_frame'],1500)
        self.assertEqual(report['statistics']['torqueNm']['mean'],1.2)
        with self.manager.file(report['id'],'timeline.csv').open(encoding='utf-8-sig') as stream:
            rows = list(csv.DictReader(stream))
        self.assertTrue({'motor','brake','motorSupply','sensor_frame','motor_command_ack'} <= {row['source'] for row in rows})
        self.assertTrue(any(row['commanded_iq_A']=='0.7' for row in rows))
        self.assertEqual(json.loads(next(row for row in rows if row['source']=='brake')['payload_json'])['snapshot']['futureField'],123)
        reloaded = WorkbenchRecordings(Path(self.temp.name),self.source,self.frames)
        self.assertEqual(reloaded.history()[0]['id'],report['id'])

    def test_missing_encoder_ratio_and_sensor_are_kept_empty(self):
        self.sensor = False
        report = self.manager.start({'range':50})
        time.sleep(.06)
        report = self.manager.stop(report['id'])
        self.assertEqual(report['state'],'completed')
        with self.manager.file(report['id'],'timeline.csv').open(encoding='utf-8-sig') as stream:
            motor = next(row for row in csv.DictReader(stream) if row['source']=='motor')
        self.assertEqual(motor['output_position_deg'],'')
        self.assertEqual(motor['actual_iq_A'],'0.21')

    def test_no_connection_or_duplicate_record_is_rejected(self):
        self.connected = False
        with self.assertRaises(ValueError):self.manager.start({})
        self.connected = True
        report = self.manager.start({})
        with self.assertRaises(ValueError):self.manager.start({})
        self.manager.stop(report['id'])
        for identifier,name in [('../escape','report.json'),(report['id'],'../bad')]:
            with self.assertRaises(ValueError):self.manager.file(identifier,name)

    def test_gap_ends_recording_and_retains_live_outputs(self):
        report = self.manager.start({})
        time.sleep(.06)
        self.drop = True
        until = time.monotonic()+2
        while self.manager.active() and time.monotonic()<until:time.sleep(.02)
        self.assertFalse(self.manager.active())
        self.assertEqual(self.manager.status()['state'],'interrupted')
        self.assertTrue(self.connected)
        self.assertEqual(self.target,.2)

    def test_http_start_stop_and_full_exports_are_independent_of_special_tests(self):
        import hardware_bridge as bridge
        server = bridge.ThreadingHTTPServer(('127.0.0.1',0),bridge.Handler)
        port=server.server_address[1]
        def post(action, payload):
            request=urllib.request.Request(f'http://127.0.0.1:{port}/api/recordings/{action}',
                data=json.dumps(payload).encode(), headers={'Content-Type':'application/json','X-MotorBench-Token':bridge.TOKEN})
            with urllib.request.urlopen(request,timeout=5) as response:return json.load(response)['current']
        with patch.object(bridge,'PORT',port),patch.object(bridge,'WORKBENCH_RECORDINGS',self.manager):
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                report=post('start',{'name':'首页独立记录','range':50})
                time.sleep(.06)
                report=post('stop',{'id':report['id']})
                self.assertEqual(report['state'],'completed')
                for name,content_type in [('timeline.csv','text/csv'),('timeline.jsonl','application/x-ndjson'),('report.json','application/json')]:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/recordings/files/{report["id"]}/{name}',timeout=5) as response:
                        self.assertIn(content_type,response.headers['Content-Type'])
                        self.assertTrue(response.read())
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/recordings/status',timeout=5) as response:
                    self.assertEqual(json.load(response)['records'][0]['id'],report['id'])
            finally:
                server.shutdown();server.server_close();thread.join(2)


if __name__ == '__main__':unittest.main()
