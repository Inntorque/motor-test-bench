"""Two-stage maximum-Iq test over the compiled portable C SDK."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from background_recording import BackgroundRecording
from workbench.acquisition import Acquisition
from workbench.events import EventHub
from workbench.recording import RecordManager
from workbench.test_engine import CurrentMotor
from workbench.devices.motor import MotorDevice
from coupled_current import CoupledCurrent
from brake_current import BrakeCurrent
from test_torque_sweep import MeteredSupply,MotorPower
from mbp_fixture import SdkBus,lib

class WorkflowTests(unittest.TestCase):
    def wait(self,predicate,timeout=5):
        end=time.monotonic()+timeout
        while not predicate():
            if time.monotonic()>end:self.fail(str(self.joint.status()))
            time.sleep(.01)
    def test_c_sdk_two_click_loading_joint_standstill_and_shared_manual_record(self):
        with tempfile.TemporaryDirectory() as directory:
            self.hub=EventHub();self.bus=SdkBus();lib.sim_motion(self.bus.context,50000)
            device=MotorDevice(bus_factory=lambda:self.bus,observe=self.hub.publish);motor=CurrentMotor(device);motor.connect()
            supply=MeteredSupply();power=MotorPower();power.on=True;stalled=[False]
            class Sensor:
                def status(self):return {'connected':True,'config':{'protocol':'modbus','speedDecimals':1},'snapshot':{'receivedAt':time.time(),'torque':.05+8*supply.current if supply.on else .05,'speed':0 if stalled[0] else 5}}
            sensor=Sensor();brake=BrakeCurrent(supply,self.hub.publish)
            source=lambda:{'motor':motor.status(),'motorSupply':power.cached_status(),'brake':supply.cached_status(),'sensor':sensor.status()}
            recorder=RecordManager(Path(directory)/'records',source,lambda _:None,event_hub=self.hub)
            recorder.diagnostics=BackgroundRecording(Path(directory)/'raw',source,lambda _:None,event_hub=self.hub)
            acquisition=Acquisition(source,lambda _:None,self.hub);acquisition.start()
            manual=recorder.start({'name':'manual owner'})
            self.joint=CoupledCurrent(motor,brake,sensor,power,lambda:False,self.hub.publish,recorder=recorder)
            try:
                self.joint.start({'mode':'torque_sweep','motorMode':'maximum_output','brake':{'startNm':.3,'stepNm':.1,'maxNm':.4,'intervalS':5}})
                self.wait(lambda:self.joint.run.get('phase')=='awaiting_load')
                self.assertEqual(self.bus.metric(1),1);self.assertFalse(supply.on);self.assertEqual(motor.run['targetA'],24)
                self.joint.begin_loading({'id':self.joint.run['id']});self.wait(lambda:supply.on)
                stalled[0]=True;lib.sim_motion(self.bus.context,0)
                self.wait(lambda:not self.joint.active())
                self.assertTrue(self.joint.run['stopConfirmed']);self.assertEqual(self.bus.metric(5),0);self.assertFalse(supply.on)
                self.assertTrue(self.joint.run['stallEndpoint']['encoderConfirmation']['confirmed'])
                self.assertTrue(recorder.active());self.assertEqual(recorder.status()['id'],manual['id'])
                recorder.stop(manual['id'])
                rows=[json.loads(row) for row in recorder.file(manual['id'],'timeline.jsonl').read_text().splitlines()]
                self.assertTrue(any(r['source']=='motor' and r['payload'].get('snapshot',{}).get('bootId') for r in rows))
                manifest=json.loads((recorder.diagnostics.directory/'manifest.json').read_text())
                self.assertFalse(manifest['error']);self.assertEqual(manifest['droppedEvents'],0)
            finally:
                if self.joint.active():self.joint.stop(wait=True)
                if recorder.active():recorder.stop(manual['id'])
                acquisition.close();motor.device.close()

if __name__=='__main__':unittest.main()
