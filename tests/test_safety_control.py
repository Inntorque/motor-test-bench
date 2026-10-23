"""Offline fault injection; never opens serial/CAN or uses the live safety journal."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import hardware_bridge as bridge
from safety_control import SafetyControl
from brake_current import BrakeCurrent
from coupled_current import CoupledCurrent
from test_coupled_current import TimedMotor, Sensor
from test_brake_current import Supply


class SafeSupply(Supply):
    def __init__(self,on=False):
        super().__init__()
        self.on=on
        self.config.update(voltageDecimals=2,device='FAKE',currentDecimals=3)
        self.output_uncertain=on
        self.snapshot=None
        self.switches=[]
        self.cancel_count=0
        self.fail_off=False
        self.remaining_voltage=0
        self.current_target=1.
    def cached_status(self):
        self.snapshot={'status':128 if self.on else 0,'measured':[2400 if self.on else self.remaining_voltage,0,0],
                       'settings':[1,2400,round(self.current_target*10**(self.config or {}).get('currentDecimals',3))], 'receivedAt':time.time()}
        return {'connected':True,'snapshot':self.snapshot,'config':self.config}
    def set_output(self,on,data=None,**kwargs):
        if not on and self.fail_off:raise ValueError('virtual OFF unavailable')
        if data and 'current' in data:self.current_target=data['current']
        self.switches.append(on);self.on=on;self.output_uncertain=on
        return self.cached_status()['snapshot']
    def set_values(self,data,**kwargs):
        self.current_target=data['current']
        return super().set_values(data)
    def cancel_pending(self):self.cancel_count+=1


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.motor=TimedMotor()
        self.brake_supply=SafeSupply()
        self.power=SafeSupply()
        self.brake=BrakeCurrent(self.brake_supply,lambda *_:None)
        self.joint=CoupledCurrent(self.motor,self.brake,Sensor(),self.power,lambda:False,lambda *_:None)
        self.safety=SafetyControl({'motor-supply':self.power,'brake-supply':self.brake_supply},
            self.motor,self.brake,self.joint,Path(self.temp.name)/'safety.json')
        self.safety.started=True

    def tearDown(self):
        self.safety.closed.set()
        self.power.fail_off=self.brake_supply.fail_off=False
        self.joint.cancel_requests()
        for obj in (self.joint,self.safety):
            thread=getattr(obj,'thread',None) or getattr(obj,'stop_thread',None)
            if thread:thread.join(5)
        if self.safety.journal_thread:self.safety.journal_thread.join(2)
        if self.safety.fault_thread:self.safety.fault_thread.join(2)
        self.temp.cleanup()

    def test_confirmed_boundary_names_supply_and_preserves_registers_without_full_recording(self):
        status=self.power.cached_status();status['snapshot'].update(status=128,measured=[2700,123,3])
        status['powerCheck']={'state':'confirmed','first':{'measured':[2800,123,3]}}
        def read():
            self.safety.closed.set();return status
        with patch.object(self.power,'read',side_effect=read),patch.object(self.safety,'stop') as stop:
            self.safety._poll('motor-supply',self.power)
            self.assertIn('电机电源',stop.call_args.args[0])
            self.assertIn('27 V',stop.call_args.args[0])
        self.safety.fault_thread.join(2)
        saved=json.loads(self.safety.path.with_name('power-fault.json').read_text())
        self.assertEqual(saved['channel'],'motor-supply')
        self.assertEqual(saved['trigger']['measured'],[2700,123,3])
        self.assertEqual(saved['verification']['first']['measured'],[2800,123,3])
        self.assertEqual(self.safety.status()['lastPowerFault'],saved)

    def test_device_protection_while_supply_off_still_stops_and_logging_does_not_delay_stop(self):
        status=self.power.cached_status();status['snapshot']['status']=4
        def read():self.safety.closed.set();return status
        with self.safety.fault_lock,patch.object(self.power,'read',side_effect=read),patch.object(self.safety,'stop') as stop:
            self.safety._poll('motor-supply',self.power)
            self.assertIn('过流保护',stop.call_args.args[0])
            self.assertTrue(self.safety.fault_thread.is_alive())
        self.safety.fault_thread.join(2)

    def test_latched_device_fault_with_output_off_does_not_repeatedly_invalidate_controls(self):
        states=iter((4,4,16,4));count=0
        def read():
            nonlocal count
            status=self.power.cached_status();status['snapshot']['status']=next(states)
            count+=1
            if count==4:self.safety.closed.set()
            return status
        with patch.object(self.power,'read',side_effect=read),patch.object(self.safety,'_power_trip') as stop,patch.object(self.safety.closed,'wait',return_value=False):
            self.safety._poll('motor-supply',self.power)
            self.assertEqual(stop.call_count,2)

    def test_intentional_off_cancellation_is_not_reported_as_link_failure(self):
        for exception, expected in ((bridge.PowerOperationCancelled('cancelled'),False),
                                    (bridge.BridgeError('serial reply timeout'),True)):
            self.safety.closed.clear()
            self.power.output_uncertain=True
            def read():
                self.safety.closed.set()
                raise exception
            with patch.object(self.power,'read',side_effect=read), patch.object(self.safety,'stop') as stop:
                self.safety._poll('motor-supply',self.power)
                self.assertEqual(stop.called,expected)

    def test_recovered_link_retries_off_verification_without_auto_enabling(self):
        self.safety.blocked = self.safety.reconciled = True
        reads = 0
        def read():
            nonlocal reads
            reads += 1
            if reads == 1:
                raise ValueError('lost reply')
            self.safety.closed.set()
            return self.power.cached_status()
        with patch.object(self.power, 'read', side_effect=read), patch.object(self.safety.closed, 'wait', return_value=False):
            self.safety._poll('motor-supply', self.power)
        self.assertFalse(self.safety.reconciled)
        self.assertTrue(self.safety.blocked)
        self.assertNotIn(True, self.power.switches)
        self.safety.stop(power_keys=self.safety.channels.keys())
        self.safety.stop_thread.join(3)
        self.assertFalse(self.safety.blocked)
        self.assertFalse(self.power.on or self.brake_supply.on)

    def test_readers_cannot_renew_owner_and_stale_commands_are_rejected(self):
        self.safety.claim('a'*32,0,energizing=True)
        before=self.safety.lease
        self.safety.heartbeat('b'*32)
        self.assertEqual(self.safety.lease,before)
        with self.assertRaisesRegex(ValueError,'另一浏览器'):self.safety.claim('b'*32,0)
        self.safety.invalidate('stop')
        with self.assertRaisesRegex(ValueError,'旧指令'):self.safety.claim('a'*32,0)
        self.assertTrue(json.loads(self.safety.path.read_text())['outputsMayBeOn'])

    def test_stop_bypasses_ordinary_gate_and_cuts_power_when_can_stop_fails(self):
        self.power.on=True
        self.motor.stop=lambda:(_ for _ in ()).throw(ValueError('virtual CAN lost'))
        with self.joint.control:
            self.safety.stop(power_keys=['motor-supply'])
            self.safety.stop_thread.join(2)
        self.assertFalse(self.safety.stop_thread.is_alive())
        self.assertFalse(self.power.on or self.brake_supply.on)
        self.assertTrue(self.safety.motor_isolated)
        self.assertFalse(self.safety.blocked)

    def test_failed_power_off_keeps_blocked(self):
        self.power.on=True;self.power.fail_off=True
        self.safety.stop(power_keys=['motor-supply'])
        self.safety.stop_thread.join(2)
        self.assertTrue(self.safety.blocked)
        self.assertTrue(self.power.on)

    def test_never_used_unconfigured_interfaces_do_not_prevent_normal_shutdown(self):
        self.power.config=self.brake_supply.config=None
        self.safety.stop(power_keys=list(self.safety.channels))
        self.safety.stop_thread.join(2)
        self.assertFalse(self.safety.blocked)
        self.assertEqual(set(self.safety.status()['unusedChannels']),set(self.safety.channels))
        self.assertFalse(self.power.switches or self.brake_supply.switches)

    def test_failed_stop_allows_original_reconnect_but_never_on(self):
        self.safety.blocked=True
        self.safety.claim('a'*32,0,restoring=True)
        with self.assertRaises(ValueError):self.safety.claim('a'*32,0,energizing=True)
        with self.assertRaises(ValueError):self.safety.check()

    def test_discharge_is_required_and_cancelled_recovery_cannot_reenable(self):
        self.power.remaining_voltage=2400
        with self.assertRaisesRegex(ValueError,'电压仍未降'):
            SafetyControl.wait_discharged(self.power,timeout=.1)
        cancel=threading.Event();cancel.set()
        with self.assertRaisesRegex(ValueError,'取消'):
            SafetyControl.wait_discharged(self.power,cancel)
        self.assertNotIn(True,self.power.switches)

    def test_owner_lease_expiry_cuts_both_manual_supplies(self):
        self.safety.claim('a'*32,0,energizing=True)
        self.power.on=self.brake_supply.on=True
        self.safety.lease=time.monotonic()-4
        watcher=threading.Thread(target=self.safety._watch,daemon=True);watcher.start()
        deadline=time.monotonic()+2
        while not self.safety.status()['stopping'] and time.monotonic()<deadline:time.sleep(.01)
        with self.safety.lock:stopper=self.safety.stop_thread
        stopper.join(2)
        self.safety.closed.set();watcher.join(1)
        self.assertFalse(self.power.on or self.brake_supply.on)
        self.assertIsNone(self.safety.owner)

    def test_http_stop_does_not_wait_for_command_gate_or_recording(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),bridge.Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def post(path,data=None,client='a'*32,epoch=0):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=1)
            conn.request('POST',path,json.dumps(data or {}),{'Host':'127.0.0.1:8766',
                'X-MotorBench-Token':bridge.TOKEN,'X-Workbench-Client':client,'X-Control-Epoch':str(epoch)})
            response=conn.getresponse();result=response.status,json.loads(response.read());conn.close();return result
        try:
            with patch.multiple(bridge,SAFETY=self.safety,COUPLED_CURRENT=self.joint,CURRENT_MOTOR=self.motor,
                    BRAKE_CURRENT=self.brake,CHANNELS=self.safety.channels),patch.object(bridge.WORKBENCH_RECORDINGS,'observe',side_effect=AssertionError('disk must not run on stop')):
                with patch.object(bridge.CONNECTIONS,'start',return_value={'running':False}):
                    self.assertEqual(post('/api/connections/start')[0],200)
                    self.assertIsNone(self.safety.owner)
                    self.assertFalse(self.safety.path.exists())
                with self.joint.control:
                    self.assertEqual(post('/api/supplies/motor-supply/output',{'on':False})[0],202)
                self.safety.stop_thread.join(2)
                self.assertEqual(post('/api/supplies/motor-supply/output',{'on':True},epoch=0)[0],409)
        finally:server.shutdown();server.server_close();worker.join(1)


class RecoveryMotor(TimedMotor):
    def __init__(self,failures=1):
        super().__init__();self.failures=failures;self.enables=0
    def _request(self,route,**kwargs):
        if route.startswith('/api/motor?enable=1'):
            self.enables+=1
            if self.failures:
                self.failures-=1
                raise ValueError('device refused motor command: 没有新鲜有效的编码器读数')
        return super()._request(route,**kwargs)


class RecoveryTests(unittest.TestCase):
    def run_case(self,failures=1,cancel=False,warmup=False):
        motor=RecoveryMotor(failures);power=SafeSupply(True);supply=SafeSupply()
        if warmup:
            original=motor.connect
            attempts=[]
            def connect(**options):
                attempts.append(1)
                if len(attempts)<3:raise ValueError('编码器反馈无效或超时')
                return original(**options)
            motor.connect=connect
        brake=BrakeCurrent(supply,lambda *_:None)
        runner=CoupledCurrent(motor,brake,Sensor(),power,lambda:False,lambda *_:None,recovery=True)
        runner.start({'motor':{'stepA':.1,'intervalS':.2,'durationS':1},
                      'brake':{'mode':'hold','currentA':.01,'durationS':1}})
        deadline=time.monotonic()+7
        while runner.active() and time.monotonic()<deadline:
            runner.status(heartbeat=True)
            if cancel and runner.run.get('phase')=='recovering':runner.stop()
            time.sleep(.01)
        runner.thread.join(1)
        self.assertFalse(runner.active())
        self.assertFalse(motor.enabled or supply.on)
        return runner,motor,power,supply

    def test_failed_encoder_start_power_cycles_once_and_restarts_both_at_zero(self):
        runner,motor,power,supply=self.run_case()
        self.assertEqual(motor.enables,2)
        self.assertEqual(power.switches[:2],[False,True])
        self.assertEqual(runner.run['reason'],'持续时间到期')
        self.assertTrue(runner.run['stopConfirmed'])
        self.assertEqual(runner.run['recoveryCount'],1)
        self.assertGreater(motor.run['steps'],0)
        self.assertEqual(runner.brake.run['steps'],0)
        self.assertEqual(runner.brake.run['targetA'],.01)
        self.assertIn(0,[v['current'] for v in supply.values])
        self.assertEqual(supply.values[-1]['current'],.01)

    def test_second_failure_never_enters_power_cycle_loop(self):
        runner,motor,power,_=self.run_case(failures=5)
        self.assertEqual(motor.enables,2)
        self.assertEqual(power.switches.count(True),1)
        self.assertNotEqual(runner.run['reason'],'持续时间到期')

    def test_stop_during_recovery_never_reenables_motor_power(self):
        runner,motor,power,_=self.run_case(cancel=True)
        self.assertEqual(motor.enables,1)
        self.assertNotIn(True,power.switches)

    def test_recovery_waits_for_fresh_encoder_without_another_power_cycle(self):
        runner,motor,power,_=self.run_case(warmup=True)
        self.assertEqual(runner.run['reason'],'持续时间到期')
        self.assertEqual(power.switches.count(True),1)
        self.assertEqual(motor.enables,2)

    def test_invalid_calibration_is_not_eligible_for_power_cycle(self):
        motor=RecoveryMotor();motor.calibrated=False
        power=SafeSupply(True);supply=SafeSupply();brake=BrakeCurrent(supply,lambda *_:None)
        runner=CoupledCurrent(motor,brake,Sensor(),power,lambda:False,lambda *_:None,recovery=True)
        runner.start({'motor':{'stepA':.1,'intervalS':.2,'durationS':1},'brake':{'mode':'hold','currentA':.01,'durationS':1}})
        runner.thread.join(2)
        self.assertNotIn(True,power.switches)
        self.assertNotIn('recoveryCount',runner.run)

    def test_temperature_protection_is_not_reset_by_power_cycle(self):
        motor=RecoveryMotor();original=motor._request
        def hot(route,**options):
            result=original(route,**options)
            if route=='/api/status':result['ntc'].update(over=True,temp_c=60)
            return result
        motor._request=hot
        power=SafeSupply(True);brake=BrakeCurrent(SafeSupply(),lambda *_:None)
        runner=CoupledCurrent(motor,brake,Sensor(),power,lambda:False,lambda *_:None,recovery=True)
        runner.start({'motor':{'stepA':.1,'intervalS':.2,'durationS':1},'brake':{'mode':'hold','currentA':.01,'durationS':1}})
        runner.thread.join(2)
        self.assertEqual(motor.enables,0)
        self.assertNotIn(True,power.switches)
        self.assertNotIn('recoveryCount',runner.run)


if __name__=='__main__':unittest.main()
