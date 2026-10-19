import csv
import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from specialized_tests import SpecializedTests, Stats


class FakeRig:
    def __init__(self):
        self.begin = time.time()
        self.brake_output = False
        self.sensor_on = True
        self.temp_on = False
        self.fixed_temp = False
        self.gap = False
        self.config = {'protocol':'hex6', 'torqueDecimals':2, 'speedDecimals':1}

    def source(self):
        now = time.time()
        stamp = self.begin if self.fixed_temp else math.floor(now * 4) / 4
        return {'sensor': {'connected':self.sensor_on,'config':self.config,
                          'snapshot':{'receivedAt':now,'torque':.2,'speed':60}},
                'motor': {'connected':self.temp_on, 'run':{'targetA':1},
                          'snapshot':{'receivedAt':stamp,'version':'FAKE','ntc':{'valid':True,'fault':False,'temp_c':25+(stamp-self.begin),'age_s':.01},
                                      'feedback':{'iq_a':.98,'id_a':0,'stale':False},'motor':{'fault':False}}},
                'brake': {'connected':True,'config':{},'snapshot':{'receivedAt':now,'status':128 if self.brake_output else 0,'settings':[0,0,0]}}}

    def frames(self, since):
        count = max(3, int((time.time()-self.begin)*1000))
        seqs = range(max(1,since+1),count+1)
        rows = [(seq, self.begin+seq/1000, 20+(seq%3), 600) for seq in seqs if not(self.gap and seq == 20)]
        return {'config':self.config,'frames':rows,'reset':False,'dropped':0}


class SpecializedTestsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.rig=FakeRig()
        self.tests=SpecializedTests(self.directory.name,self.rig.source,self.rig.frames)

    def tearDown(self):
        if self.tests.status():
            self.tests.stop(self.tests.status()['id'])
        self.directory.cleanup()

    def wait(self):
        self.tests.thread.join(3)
        self.assertFalse(self.tests.thread.is_alive())
        return self.tests.status()

    def test_statistics_keep_signed_torque_and_zero_mean_is_not_normalized(self):
        stats=Stats()
        for n in [-2,0,2,None,float('nan')]:stats.add(n)
        result=stats.result(.01)
        self.assertEqual(result['mean'],0)
        self.assertEqual(result['peakToPeak'],4)
        self.assertAlmostEqual(result['std'],math.sqrt(8/3))
        self.assertIsNone(result['cvPercent'])

    def test_configuration_rejects_invalid_parameters(self):
        for data in [None,{'kind':'position'},{'kind':'baseline','durationS':True},
                     {'kind':'baseline','durationS':0},{'kind':'temperature','ambientC':float('nan')}]:
            with self.assertRaises(ValueError):self.tests.start(data)

    def test_zero_load_requires_confirmed_off_and_cannot_override_on(self):
        self.rig.brake_output=True
        with self.assertRaisesRegex(ValueError,'仍在输出'):
            self.tests.start({'kind':'baseline','manualBrakeOff':True})
        source=self.rig.source();source['brake']['connected']=False
        self.tests.source=lambda:source
        with self.assertRaisesRegex(ValueError,'确认'):
            self.tests.start({'kind':'baseline'})

    def test_high_rate_csv_retains_each_frame_and_persists_results(self):
        self.tests.start({'kind':'baseline','durationS':1})
        report=self.wait()
        self.assertEqual(report['state'],'completed')
        self.assertTrue(report['valid'])
        self.assertGreater(report['sensorSamples'],800)
        path=self.tests.file(report['id'],'data.csv')
        with path.open(encoding='utf-8-sig',newline='') as file:rows=list(csv.DictReader(file))
        seq=[int(row['sensor_sequence']) for row in rows]
        self.assertTrue(all(b==a+1 for a,b in zip(seq,seq[1:])))
        self.assertEqual(len(rows),report['sensorSamples'])
        self.assertEqual(len(self.tests.history()),1)
        self.assertEqual(json.loads(self.tests.file(report['id'],'report.json').read_text())['state'],'completed')

    def test_sensor_gap_interrupts_without_fabricated_points(self):
        self.rig.gap=True
        self.tests.start({'kind':'baseline','durationS':1})
        report=self.wait()
        self.assertEqual(report['state'],'interrupted')
        self.assertFalse(report['valid'])
        self.assertIn('不连续',report['reason'])

    def test_temperature_only_uses_new_motor_snapshots(self):
        self.rig.sensor_on=False;self.rig.temp_on=True
        self.tests.start({'kind':'temperature','durationS':1,'ambientC':20})
        report=self.wait()
        self.assertEqual(report['state'],'completed')
        self.assertEqual(report['sensorSamples'],0)
        self.assertTrue(3 <= report['stats']['temperature']['count'] <= 6)
        self.assertAlmostEqual(report['endRiseAboveAmbientC'],report['stats']['temperature']['last']-20)
        self.assertTrue(report['valid'])

    def test_repeated_temperature_snapshot_counted_once(self):
        self.rig.temp_on=True;self.rig.fixed_temp=True
        self.tests.start({'kind':'temperature','durationS':1})
        report=self.wait()
        self.assertEqual(report['temperatureSamples'],1)
        self.assertFalse(report['valid'])

    def test_active_test_cannot_be_replaced_and_manual_stop_is_partial(self):
        report=self.tests.start({'kind':'ripple','durationS':10,'steadyConfirmed':True})
        with self.assertRaisesRegex(ValueError,'正在采集'):self.tests.start({'kind':'baseline'})
        with self.assertRaises(ValueError):self.tests.stop('wrong')
        stopped=self.tests.stop(report['id'])
        self.assertEqual(stopped['state'],'stopped')
        self.assertFalse(stopped['valid'])
        with self.assertRaises(ValueError):self.tests.file('../elsewhere','report.json')

    def test_zero_load_turned_on_mid_test_is_interrupted(self):
        self.tests.start({'kind':'baseline','durationS':1})
        self.rig.brake_output=True
        report=self.wait()
        self.assertEqual(report['state'],'interrupted')
        self.assertIn('仍在输出',report['reason'])

    def test_restart_marks_unfinished_record_interrupted(self):
        directory=Path(self.directory.name)/('a'*32);directory.mkdir()
        (directory/'report.json').write_text(json.dumps({'id':'a'*32,'state':'running','startedAt':time.time()}))
        recovered=SpecializedTests(self.directory.name,self.rig.source,self.rig.frames)
        recovered.recover_interrupted()
        self.assertEqual(recovered.history()[0]['state'],'interrupted')

    def test_http_start_and_download_routes_and_token_check(self):
        import urllib.request
        import urllib.error
        import threading
        from unittest.mock import patch
        from http.server import ThreadingHTTPServer
        import hardware_bridge as bridge
        class QuietHandler(bridge.Handler):
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
        port = server.server_address[1]
        worker = threading.Thread(target=server.serve_forever,daemon=True)
        with patch.object(bridge,'PORT',port), patch.object(bridge,'SPECIALIZED',self.tests):
            worker.start()
            base = f'http://127.0.0.1:{port}'
            try:
                request = urllib.request.Request(base+'/api/specialized/start',data=json.dumps({'kind':'baseline','durationS':1}).encode(),headers={'Content-Type':'application/json'})
                with self.assertRaises(urllib.error.HTTPError) as failure: urllib.request.urlopen(request)
                self.assertEqual(failure.exception.code,403)
                request.add_header('X-MotorBench-Token',bridge.TOKEN)
                with urllib.request.urlopen(request) as response: record=json.load(response)['current']
                report = self.wait()
                self.assertEqual(report['id'],record['id'])
                with urllib.request.urlopen(base+f"/api/specialized/files/{record['id']}/data.csv") as response:
                    self.assertIn(b'sensor_sequence',response.read())
                with urllib.request.urlopen(base+'/api/specialized/status') as response:
                    self.assertEqual(len(json.load(response)['records']),1)
            finally:
                server.shutdown();server.server_close();worker.join(2)


if __name__=='__main__':unittest.main()
