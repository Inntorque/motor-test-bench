import json
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from background_recording import BackgroundRecording
from workbench.test_engine import CurrentMotor, CurrentError


class BackgroundRecordingTests(unittest.TestCase):
    def test_frames_commands_disconnect_and_reset_continue_in_rotated_files(self):
        with tempfile.TemporaryDirectory() as root:
            state = {'connected':True, 'snapshot':{'receivedAt':time.time(), 'settings':[1,2400,1000]}}
            rows = []
            reset = False
            def frames(cursor):
                return {'config':{'torqueDecimals':2,'speedDecimals':1}, 'reset':reset,
                        'dropped':2 if reset else 0, 'frames':[r for r in rows if reset or r[0]>cursor]}
            recorder = BackgroundRecording(root, lambda:{'brake':state}, frames, rotate_seconds=.06)
            recorder.start()
            original = {'requestId':'operation-1', 'request':{'current':1}}
            recorder.observe('operation_request', original)
            original['request']['current'] = 99
            recorder.observe('operation_response', {'requestId':'operation-1','httpStatus':200,'durationS':.1})
            rows.extend([[i,time.time(),120,100] for i in range(1,1001)])
            time.sleep(.08)
            state['connected'] = False
            rows.clear(); reset = True
            time.sleep(.04)
            reset = False
            state['connected'] = True
            rows.extend([[1001,time.time(),130,200]])
            time.sleep(.08)
            recorder.close()
            files = sorted(recorder.directory.glob('events-*.jsonl'))
            self.assertGreaterEqual(len(files),2)
            events = [json.loads(line) for file in files for line in file.read_text().splitlines()]
            request = next(e for e in events if e['kind']=='operation_request')
            self.assertEqual(request['payload']['request']['current'],1)
            samples = [r for e in events if e['kind']=='sensor_frames' for r in e['payload']['frames']]
            self.assertEqual(len(samples),1001)
            self.assertEqual(samples[0][0],1)
            self.assertEqual(samples[-1][0],1001)
            self.assertTrue(any(e['kind']=='sensor_stream' and e['payload']['droppedFrames']==2 for e in events))
            self.assertTrue(any(e['kind']=='snapshot' and not e['payload']['state']['connected'] for e in events))
            manifest=json.loads((Path(root)/'latest.json').read_text())
            self.assertEqual(manifest['sensorFrames'],1001)
            self.assertEqual(manifest['droppedEvents'],0)
            self.assertFalse(manifest['running'])
            self.assertFalse(recorder.thread.is_alive())

    def test_snapshot_capture_does_not_call_device_commands_or_modify_inputs(self):
        with tempfile.TemporaryDirectory() as root:
            sources={'motor':{'connected':False,'run':{'targetA':.4}},'sensor':{'connected':False}}
            recorder=BackgroundRecording(root,lambda:sources,lambda cursor:None)
            recorder.start()
            time.sleep(.04)
            recorder.close()
            self.assertEqual(sources['motor']['run']['targetA'],.4)
            self.assertEqual(recorder.error,'')


if __name__=='__main__':unittest.main()
