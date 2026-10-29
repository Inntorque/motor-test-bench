"""The authenticated application dispatches locally; no second HTTP service."""
import http.client
import json
import threading
import unittest
import tempfile
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from workbench.service import Handler,devices
from workbench.events import HUB

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.lan_access=None;self.server.closing=threading.Event()
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):self.server.closing.set();self.server.shutdown();self.server.server_close();self.thread.join(1)
    def request(self,method,path,headers=None,data=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=2)
        conn.request(method,path,json.dumps(data or {}) if method=='POST' else None,headers or {})
        response=conn.getresponse();body=response.read();conn.close();return response.status,body
    def test_same_origin_token_and_versioned_local_dispatch(self):
        code,body=self.request('GET','/api/v1/health');self.assertEqual(code,200);self.assertIn('token',json.loads(body))
        self.assertEqual(self.request('POST','/api/v1/connections/start')[0],403)
        with patch.object(devices.CONNECTIONS,'start',return_value={'running':False}) as start:
            self.assertEqual(self.request('POST','/api/v1/connections/start',{'X-MotorBench-Token':devices.TOKEN})[0],200)
            self.assertEqual(start.call_count,1)
            self.assertEqual(self.request('POST','/api/v1/connections/start',{'X-MotorBench-Token':devices.TOKEN,'Origin':'http://evil.invalid'})[0],403)
            self.assertEqual(start.call_count,1)
    def test_state_read_and_static_page_do_not_connect_hardware(self):
        HUB.publish('state',{'hostApi':1,'devices':{}})
        with patch.object(devices.CURRENT_MOTOR.device,'connect',side_effect=AssertionError('hardware forbidden')):
            self.assertEqual(json.loads(self.request('GET','/api/v1/state')[1])['hostApi'],1)
            self.assertEqual(self.request('GET','/')[0],200)
            self.assertEqual(json.loads(self.request('GET','/__workbench/status')[1])['hostApi'],1)

    def test_subject_metadata_is_authenticated_and_does_not_enter_device_control(self):
        from workbench.subjects import TestSubjects
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(devices, 'TEST_SUBJECTS', TestSubjects(Path(folder)/'subjects.json')), patch.object(devices.SAFETY, 'claim', side_effect=AssertionError('metadata must not control devices')):
                self.assertEqual(self.request('GET','/api/v1/test-subjects')[0],200)
                self.assertEqual(self.request('POST','/api/v1/test-subjects/create',data={'kind':'motor','name':'电机 A'})[0],403)
                headers={'X-MotorBench-Token':devices.TOKEN}
                code,body=self.request('POST','/api/v1/test-subjects/create',headers,{'kind':'motor','name':'电机 A'})
                self.assertEqual(code,200)
                item=json.loads(body)['item']
                code,body=self.request('POST','/api/v1/test-subjects/select',headers,{'motorId':item['id']})
                self.assertEqual(code,200)
                self.assertEqual(json.loads(body)['selected']['motorId'],item['id'])
