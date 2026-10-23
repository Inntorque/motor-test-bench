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

    def test_fixed_current_has_one_setpoint_and_turns_off_on_expiry(self):
        self.runner.start({'mode':'hold','currentA':.375,'durationS':1})
        self.runner.thread.join(3)
        self.assertEqual(self.supply.values,[{'voltage':24,'current':.375}])
        self.assertEqual(self.runner.run['targetA'],.375)
        self.assertEqual(self.runner.run['steps'],0)
        self.assertTrue(self.runner.run['stopConfirmed'])
        self.assertFalse(self.supply.on)

    def test_fixed_current_validation_and_changed_setpoint_stop(self):
        for current in (-.001,2.301,.0001,float('nan'),''):
            with self.subTest(current=current), self.assertRaises(ValueError):
                self.runner.validate({'mode':'hold','currentA':current,'durationS':1})
        self.assertEqual(self.runner.validate({'mode':'hold','currentA':0,'durationS':1})[0],0)
        self.runner.run.update(mode='hold',targetA=.4,limitA=2.3)
        with self.assertRaisesRegex(ValueError,'发生变化'):
            self.runner._check_snapshot({'status':128,'settings':[1,2400,500]})
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

    def test_one_second_steps_prioritize_writes_over_slow_telemetry(self):
        # SSJ uses 150 ms gaps: a full read costs ~0.47 s and a
        # verified write ~0.7 s. Reading before every write cannot keep 1 Hz.
        reads, writes = [], []
        def read():
            reads.append(time.monotonic())
            time.sleep(.47)
            return self.supply.cached_status()
        def write(data):
            writes.append((time.monotonic(),data['current']))
            time.sleep(.7)
            return self.supply.cached_status()['snapshot']
        self.supply.read, self.supply.set_values = read, write
        self.runner.start({'stepA':.01,'intervalS':1,'durationS':4.9})
        deadline = time.monotonic()+8
        while self.runner.active() and time.monotonic()<deadline:
            self.runner.status(heartbeat=True)
            time.sleep(.03)
        self.runner.thread.join(2)
        self.assertEqual(self.runner.run['reason'],'持续时间到期')
        self.assertTrue(self.runner.run['stopConfirmed'])
        self.assertEqual([v for _,v in writes],[0,.01,.02,.03,.04])
        starts = [t for t,v in writes if v>0]
        self.assertTrue(all(.85 < b-a < 1.15 for a,b in zip(starts,starts[1:])))
        self.assertLess(self.runner.run['stepLatenessS'],.15)
        self.assertGreaterEqual(self.runner.run['commandDurationS'],.69)
        self.assertFalse([t for t in reads if starts[0] <= t <= starts[-1]],
                         'step responses already include fresh telemetry')

    def test_output_loss_in_write_readback_stops_without_next_step(self):
        original = self.supply.set_values
        def write(data):
            if data['current']>0:self.supply.on=False
            return original(data)
        self.supply.set_values=write
        self.runner.start({'stepA':.1,'intervalS':.2,'durationS':1})
        self.runner.thread.join(3)
        self.assertIn('输出已关闭',self.runner.run['reason'])
        self.assertEqual(self.runner.run['steps'],0)
        self.assertTrue(self.runner.run['stopConfirmed'])


if __name__=='__main__':unittest.main()
