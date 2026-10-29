"""Cache and event-delivery equivalence tests; no devices or services."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from workbench.events import EventHub
from workbench.subjects import TestSubjects


class RuntimePerformanceTests(unittest.TestCase):
    def test_stream_health_and_direct_health_have_the_same_service_identity(self):
        from workbench.service import state, devices
        class Reader:
            path='/api/health'
            def _valid_host(self):return True
            def _send(self,value):return value
        direct=devices.Handler.do_GET(Reader())
        streamed=state()['routes']['/api/health']
        self.assertEqual(streamed['app'],direct['app'])
        self.assertEqual(streamed['token'],direct['token'])

    def test_filtered_event_read_keeps_cursor_and_does_not_copy_internal_payload(self):
        class Probe:
            copies=0
            def __deepcopy__(self,memo):
                Probe.copies+=1
                return Probe()
        hub=EventHub();seen=[];hub.subscribe(seen.append)
        hub.publish('acquisition',{'probe':Probe()});hub.publish('state',{'value':1})
        before=Probe.copies
        gap,events=hub.after(0,0,include=('state','sensor_frames','sensor_stream'))
        self.assertFalse(gap);self.assertEqual(Probe.copies,before)
        self.assertEqual(events[0],{'id':1,'type':'acquisition'})
        events[1]['data']['value']=2
        self.assertEqual(hub.snapshot(),{'value':1})
        self.assertIn('probe',seen[0]['data'])
        self.assertEqual(hub.after(events[-1]['id'],0)[1],[])
        self.assertIn('probe',hub.after(0,0)[1][0]['data'])
        self.assertGreater(Probe.copies,before)

    def test_filtered_events_retain_gap_detection_and_order(self):
        hub=EventHub(capacity=3)
        for i in range(7):hub.publish('sensor_frames' if i%2 else 'acquisition',{'n':i})
        gap,events=hub.after(1,0,include=('sensor_frames',))
        self.assertTrue(gap);self.assertEqual([e['id'] for e in events],[5,6,7])
        self.assertNotIn('data',events[0]);self.assertEqual(events[1]['data'],{'n':5})

    def test_cached_catalog_reads_refresh_external_changes_and_isolate_returned_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'subjects.json';catalog=TestSubjects(path)
            motor=catalog.create('motor','A')['item'];catalog.select({'motorId':motor['id']})
            with patch('workbench.subjects.json.loads',wraps=json.loads) as loads:
                for _ in range(100):self.assertEqual(catalog.selection()['motor']['name'],'A')
                self.assertEqual(loads.call_count,0)
                other=TestSubjects(path);other.create('motor','B')
                self.assertEqual(len(catalog.status()['motors']),2)
                self.assertEqual(loads.call_count,2)
            status=catalog.status();status['motors'][0]['name']='mutated';status['selected']['motorId']=''
            selection=catalog.selection();selection['motor']['name']='mutated'
            self.assertEqual(catalog.selection()['motor']['name'],'A')

    def test_failed_catalog_save_does_not_publish_unwritten_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'subjects.json';catalog=TestSubjects(path)
            catalog.create('motor','A');original=path.read_bytes()
            with patch.object(Path,'replace',side_effect=OSError('disk error')):
                with self.assertRaises(OSError):catalog.create('motor','B')
            self.assertEqual(path.read_bytes(),original)
            self.assertEqual([r['name'] for r in catalog.status()['motors']],['A'])
