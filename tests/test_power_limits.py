"""Offline readback injection. No serial/CAN/network access."""
import copy
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from hardware_bridge import PowerSupplyChannel, DeviceLinkError, PowerOperationCancelled
from power_limits import power_problem


class PowerReadbackTests(unittest.TestCase):
    def setUp(self):
        self.channel=PowerSupplyChannel('motor-supply')
        self.channel.config=dict(protocol='ssj',voltageDecimals=2,currentDecimals=2)
        self.channel.connected=True
        self.channel.transport=SimpleNamespace(is_open=True,close=lambda:None)
        self.good=dict(status=208,settings=[1,2400,2000,4800,5003,0],measured=[2400,123,29],
                       receivedAt=time.time(),logs=[dict(tx='read only',rx='valid CRC')])
    def read_sequence(self,*snapshots):
        with patch.object(self.channel,'_read_snapshot_unlocked',side_effect=snapshots) as read:
            result=self.channel.read()
            self.assertEqual(read.call_count,len(snapshots))
            return result
    def test_healthy_and_one_display_count_rounding_need_one_read_only(self):
        for voltage in (2399,2400,2401):
            sample=copy.deepcopy(self.good);sample['settings'][1]=voltage
            self.assertIsNone(power_problem(self.channel.key,self.channel.config,sample))
            self.read_sequence(sample)
        sample=copy.deepcopy(self.good);sample['settings'][2]=2001
        self.assertIsNone(power_problem(self.channel.key,self.channel.config,sample))
    def test_single_abnormal_read_is_confirmed_independently_and_never_published(self):
        for field,values in (('measured',[2700,123,29]),('measured',[2400,2001,29]),('settings',[1,2500,1000])):
            bad=copy.deepcopy(self.good);bad[field]=values
            self.channel.snapshot=copy.deepcopy(self.good)
            def verify():
                self.assertTrue(self.channel.verifying_power)
                self.assertEqual(self.channel.snapshot['measured'],self.good['measured'])
                return copy.deepcopy(self.good)
            with patch.object(self.channel,'_read_snapshot_unlocked') as read:
                read.side_effect=lambda: bad if read.call_count==1 else verify()
                result=self.channel.read()
            self.assertEqual(result['snapshot']['settings'],self.good['settings'])
            self.assertEqual(result['powerCheck']['state'],'recovered')
            self.assertEqual(result['powerCheck']['first'][field],values)
            self.assertFalse(self.channel.verifying_power)
    def test_repeated_violation_remains_a_fault_with_both_readings(self):
        bad=copy.deepcopy(self.good);bad['measured'][1]=2100
        result=self.read_sequence(bad,copy.deepcopy(bad))
        self.assertEqual(result['powerCheck']['state'],'confirmed')
        self.assertEqual(result['powerCheck']['issue']['kind'],'measured_current')
        self.assertIsNotNone(power_problem(self.channel.key,self.channel.config,result['snapshot']))
    def test_motor_bus_allows_20a_but_never_adds_an_extra_ten_percent(self):
        for current in (1101,1166,1999,2000):
            sample={**self.good,'measured':[2400,current,0]}
            self.assertIsNone(power_problem('motor-supply',self.channel.config,sample))
            self.read_sequence(sample)
        sample={**self.good,'measured':[2400,2001,0]}
        issue=power_problem('motor-supply',self.channel.config,sample)
        self.assertEqual((issue['kind'],issue['limit']),('measured_current',20))
        sample={**self.good,'settings':[1,2400,2002]}
        self.assertEqual(power_problem('motor-supply',self.channel.config,sample)['kind'],'setting_current')

    def test_actual_current_precision_is_independent_of_setpoint_precision(self):
        config={**self.channel.config,'measuredCurrentDecimals':3}
        for raw in (600,2071,20000):
            self.assertIsNone(power_problem('motor-supply',config,{**self.good,'measured':[2400,raw,0]}))
        issue=power_problem('motor-supply',config,{**self.good,'measured':[2400,20001,0]})
        self.assertEqual(issue['value'],20.001)
        self.assertEqual(issue['kind'],'measured_current')
        self.assertEqual(power_problem('motor-supply',config,{**self.good,'settings':[1,2400,2002]})['kind'],'setting_current')

    def test_device_ocp_ovp_and_ru_protection_are_not_debounced(self):
        for flags in (4,8,128|4,128|8):
            bad=copy.deepcopy(self.good);bad['status']=flags
            result=self.read_sequence(bad)
            self.assertEqual(result['powerCheck']['state'],'device_protection')
        config={**self.channel.config,'protocol':'ru'}
        self.assertEqual(power_problem('motor-supply',config,{**self.good,'ruStatus':{'otp':True}})['kind'],'hardware')
    def test_failed_confirmation_never_reconnects_or_enables_and_preserves_first_read(self):
        self.channel.output_uncertain=True;self.channel.snapshot=self.good
        bad=copy.deepcopy(self.good);bad['measured'][0]=2700
        with patch.object(self.channel,'_read_snapshot_unlocked',side_effect=[bad,DeviceLinkError('offline timeout')]),patch.object(self.channel,'_open_transport') as opened:
            with self.assertRaises(DeviceLinkError):self.channel.read()
            opened.assert_not_called()
        self.assertFalse(self.channel.connected)
        self.assertTrue(self.channel.output_uncertain)
        self.assertFalse(self.channel.verifying_power)
        self.assertEqual(self.channel.power_check['first']['measured'][0],2700)
    def test_stop_cancels_confirmation_without_closing_original_off_transport_or_publishing_bad_read(self):
        self.channel.snapshot=copy.deepcopy(self.good)
        bad=copy.deepcopy(self.good);bad['measured'][0]=2700
        with patch.object(self.channel,'_read_snapshot_unlocked') as read:
            def snapshot():
                if read.call_count==1:return bad
                self.channel.cancel_pending()
                return self.channel._exchange(4,1000,3)
            read.side_effect=snapshot
            with self.assertRaises(PowerOperationCancelled):self.channel.read()
        self.assertIsNotNone(self.channel.transport)
        self.assertTrue(self.channel.connected)
        self.assertFalse(self.channel.verifying_power)
        self.assertEqual(self.channel.snapshot['measured'],self.good['measured'])
    def test_brake_voltage_thresholds_and_malformed_read_is_not_accepted_as_zero(self):
        for measured,kind in (([2641,0],'measured_voltage'),([2400,2001],'measured_current'),([],'invalid')):
            sample={**self.good,'measured':measured}
            self.assertEqual(power_problem('motor-supply',self.channel.config,sample)['kind'],kind)
        config={**self.channel.config,'currentDecimals':3}
        sample={**self.good,'settings':[1,2400,2300],'measured':[2400,2531]}
        self.assertEqual(power_problem('brake-supply',config,sample)['kind'],'measured_current')


if __name__=='__main__':unittest.main()
