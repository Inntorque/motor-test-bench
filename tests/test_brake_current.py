import threading
import time
import unittest
from brake_current import BrakeCurrent


class Supply:
    def __init__(self):
        self.lock=threading.RLock()
        self.config={'protocol':'ssj','currentDecimals':3,'maxCurrent':2.3}
        self.on=False
        self.values=[]
        self.fail=False
    def cached_status(self):
        return {'connected':True,'config':self.config,'snapshot':{'status':128 if self.on else 0}}
    def read(self):return self.cached_status()
    def set_values(self,data):
        self.values.append(dict(data))
        if self.fail and data['current']>0:raise ValueError('readback mismatch')
        return self.read()['snapshot']
    def set_output(self,on,data):
        self.on=on
        return self.read()['snapshot']


class BrakeTests(unittest.TestCase):
    def setUp(self):
        self.supply=Supply()
        self.events=[]
        self.runner=BrakeCurrent(self.supply,lambda kind,payload:self.events.append((kind,payload)))
    def tearDown(self):
        if self.runner.active():self.runner.stop()
    def test_steps_hold_at_limit_and_expiry_turns_off(self):
        self.runner.start({'stepA':1,'intervalS':.2,'durationS':1})
        self.runner.thread.join(3)
        self.assertEqual([v['current']for v in self.supply.values],[0,1,2,2.3])
        self.assertTrue(all(v['voltage']==24 for v in self.supply.values))
        self.assertFalse(self.supply.on)
        self.assertTrue(self.runner.run['stopConfirmed'])
        self.assertEqual(self.runner.run['reason'],'持续时间到期')
        self.assertEqual([e[0]for e in self.events].count('brake_step'),3)
    def test_stop_cancels_future_steps_and_disables_output(self):
        self.runner.start({'stepA':.1,'intervalS':.2,'durationS':5})
        time.sleep(.25)
        self.runner.stop()
        count=len(self.supply.values)
        time.sleep(.25)
        self.assertEqual(len(self.supply.values),count)
        self.assertFalse(self.supply.on)
        self.assertFalse(self.runner.active())
    def test_readback_failure_stops(self):
        self.supply.fail=True
        self.runner.start({'stepA':.1,'intervalS':.2,'durationS':1})
        self.runner.thread.join(3)
        self.assertFalse(self.supply.on)
        self.assertIn('readback mismatch',self.runner.run['reason'])
    def test_lost_page_heartbeat_stops(self):
        self.runner.start({'stepA':.1,'intervalS':.2,'durationS':5})
        self.runner.lease=time.monotonic()-10
        self.runner.thread.join(3)
        self.assertFalse(self.supply.on)
        self.assertIn('心跳',self.runner.run['reason'])
    def test_invalid_parameters_and_live_output_never_start(self):
        for data in [{'stepA':3,'intervalS':.2,'durationS':1},
                     {'stepA':.1,'intervalS':1,'durationS':1},
                     {'stepA':float('nan'),'intervalS':.2,'durationS':1}]:
            with self.assertRaises(ValueError):self.runner.start(data)
        self.supply.on=True
        with self.assertRaisesRegex(ValueError,'关闭'):self.runner.start({'stepA':.1,'intervalS':.2,'durationS':1})
        self.assertTrue(self.supply.on)
        self.assertEqual(self.supply.values,[])


if __name__=='__main__':unittest.main()
