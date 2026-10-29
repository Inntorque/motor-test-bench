import json
from pathlib import Path
import struct
import time
import unittest
from types import SimpleNamespace
from protocol import generated as p
from protocol.codec import IsoTp,SampleAssembler,packet,unpack,objects,values,tlv,decode_tlvs,ProtocolError
from protocol.generate import outputs
from mbp_fixture import SdkBus,lib
from workbench.devices.motor import MotorDevice

class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.bus=SdkBus(realtime=False);self.received=[]
        self.iso=IsoTp(lambda d:self.bus.send(SimpleNamespace(arbitration_id=0x401,data=d)),self.received.append)
        self.rid=10
    def tearDown(self):self.bus.shutdown()
    def exchange(self,op,body=b'',rid=None,major=1):
        self.rid+=1;self.received.clear();self.iso.start(packet(op,rid or self.rid,body,major=major),self.bus.now/1e6)
        for _ in range(6000):
            now=self.bus.now/1e6;self.iso.poll(now);msg=self.bus.recv(0)
            if msg and msg.arbitration_id==0x441:self.iso.receive(msg.data,now)
            if self.received:
                _,_,_,_,_,body=unpack(self.received[0]);return struct.unpack_from('<H',body)[0],values(body[4:])
        self.fail('transaction timeout')
    def envelope(self,seq=1,gen=1,session=123):return struct.pack('<QQII',0x1122334455667788,session,gen,seq)
    def test_generated_contract_is_current(self):
        root=Path(__file__).resolve().parents[1]
        for name,data in outputs().items():self.assertEqual((root/name).read_text(),data)
    def test_frozen_golden_vectors_match_generated_codec(self):
        vectors=json.loads((Path(__file__).resolve().parents[1]/'protocol/golden.json').read_text())
        self.assertEqual(packet(p.OP_IDENTIFY,1).hex(),vectors['identify_request'])
        self.assertEqual(objects({p.OBJ_IQ_TARGET:-1234}).hex(),vectors['iq_negative_1234ma'])
        self.assertEqual(values(bytes.fromhex(vectors['position_negative_large']))[p.OBJ_POSITION_TARGET],-1234567890123)

    def test_identity_and_signed_golden_vector(self):
        rc,ids=self.exchange(p.OP_IDENTIFY);self.assertEqual(rc,0);self.assertEqual(ids[p.OBJ_IQ_MIN],-30000)
        self.assertEqual(ids[p.OBJ_BOOT_ID],0x1122334455667788)
        self.assertEqual(objects({p.OBJ_IQ_TARGET:-1234}).hex(),'02100504002efbffff')
        self.assertLessEqual(self.bus.metric(0),8192)
    def test_prepare_does_not_enable_start_deduplicates_stop_revokes_late_start(self):
        self.assertEqual(self.exchange(p.OP_CLAIM_CONTROL,self.envelope())[0],0)
        rc,obj=self.exchange(p.OP_PREPARE_RUN,self.envelope(2)+objects({p.OBJ_RUN_MODE:1,p.OBJ_DIRECTION:1,p.OBJ_IQ_TARGET:1000}))
        self.assertEqual(rc,0);self.assertEqual(self.bus.metric(5),0)
        prepared=obj[p.OBJ_PREPARED_ID];request=self.envelope(3)+objects({p.OBJ_PREPARED_ID:prepared})
        self.assertEqual(self.exchange(p.OP_START,request,rid=100)[0],1)
        self.assertEqual(self.exchange(p.OP_START,request,rid=100)[0],1);self.assertEqual(self.bus.metric(1),1)
        self.bus.send(SimpleNamespace(arbitration_id=0x081,data=b'STOP'+struct.pack('<I',99)));self.bus.step()
        self.assertEqual(self.bus.metric(5),0)
        self.assertEqual(self.exchange(p.OP_START,request,rid=100)[0],7);self.assertEqual(self.bus.metric(1),1)
    def test_same_request_id_different_body_and_stale_sequence_rejected(self):
        self.exchange(p.OP_CLAIM_CONTROL,self.envelope())
        body=self.envelope(2)+objects({p.OBJ_RUN_MODE:1,p.OBJ_DIRECTION:1})
        self.assertEqual(self.exchange(p.OP_PREPARE_RUN,body,rid=101)[0],0)
        self.assertEqual(self.exchange(p.OP_PREPARE_RUN,body+b'\0',rid=101)[0],3)
        self.assertEqual(self.exchange(p.OP_PREPARE_RUN,body,rid=102)[0],8)
    def test_major_mismatch_identifies_but_cannot_claim(self):
        self.assertEqual(self.exchange(p.OP_IDENTIFY,major=2)[0],0)
        self.assertEqual(self.exchange(p.OP_CLAIM_CONTROL,self.envelope(),major=2)[0],2)
        self.assertEqual(self.bus.metric(1),0)
    def test_malformed_length_unknown_required_config_and_bad_type(self):
        self.exchange(p.OP_CLAIM_CONTROL,self.envelope())
        self.assertEqual(self.exchange(p.OP_PREPARE_RUN,self.envelope(2)+tlv(0x1100,p.TYPE_U32,1))[0],3)
        self.assertEqual(self.exchange(p.OP_PREPARE_RUN,self.envelope(3)+tlv(p.OBJ_RUN_MODE,p.TYPE_U32,1))[0],3)
    def test_wrong_session_heartbeat_does_not_renew_watchdog(self):
        self.exchange(p.OP_CLAIM_CONTROL,self.envelope())
        self.bus.send(SimpleNamespace(arbitration_id=0x101,data=struct.pack('<Q',999)))
        self.bus.step(1100000)
        self.assertEqual(self.exchange(p.OP_PREPARE_RUN,self.envelope(2))[0],7)
    def test_stop_slot_survives_receive_queue_overflow(self):
        for _ in range(100):self.bus.send(SimpleNamespace(arbitration_id=0x401,data=b'\x10\xff'+b'\0'*6))
        self.bus.send(SimpleNamespace(arbitration_id=0x081,data=b'STOP'+struct.pack('<I',77)));self.bus.step()
        self.assertGreater(self.bus.metric(6),0);self.assertEqual(self.bus.metric(2),1)
    def test_invalid_tlv_integer_and_bool(self):
        for body in (b'\x01\x00\x03\x01\x00\x00',b'\x01\x00\x07\x01\x00\x02'):
            with self.assertRaises(ProtocolError):decode_tlvs(body)
    def test_missing_half_position_and_cross_group_never_published(self):
        a=SampleAssembler()
        for field in (0,1,6):a.receive(struct.pack('<HBBI',1,field,1,123))
        self.assertIsNone(a.receive(struct.pack('<HBBI',1,254,1,67)))
        a.receive(struct.pack('<HBBI',2,7,1,456))
        self.assertIsNone(a.receive(struct.pack('<HBBI',2,254,1,128)))
        self.assertEqual(a.dropped,2)

class NativeClientTests(unittest.TestCase):
    def setUp(self):self.bus=SdkBus();self.motor=MotorDevice(bus_factory=lambda:self.bus)
    def tearDown(self):self.motor.close()
    def test_python_host_roundtrip_real_c_sdk(self):
        self.motor.connect();time.sleep(.08)
        snapshot=self.motor.latest_snapshot()
        self.assertEqual(snapshot['feedback']['positionMicroturns'],'1234567890123')
        self.assertEqual(snapshot['feedback']['id_a'],-.321)
        prepared=self.motor.prepare_run({p.OBJ_RUN_MODE:1,p.OBJ_DIRECTION:1,p.OBJ_IQ_TARGET:0})
        self.assertFalse(self.bus.metric(5));self.motor.start(prepared);self.motor.set_target(4.2);time.sleep(.08)
        self.assertEqual(self.motor.latest_snapshot()['feedback']['iq_a'],4.2)
        result=self.motor.stop();self.assertTrue(result['deviceDisabled']);self.assertEqual(self.bus.metric(1),1)
    def test_unsupported_current_convention_cannot_claim_output(self):
        self.motor.connect();self.motor.identity[p.OBJ_DQ_CURRENT_CONVENTION]=2
        with self.assertRaisesRegex(ProtocolError,'电流定义'):self.motor.claim()
        self.assertEqual(self.bus.metric(1),0)

    def test_unknown_major_remains_identified_read_only(self):
        recv=self.bus.recv
        def receive(timeout=.02):
            msg=recv(timeout)
            if msg and msg.arbitration_id==0x441 and msg.data[0]>>4==1:
                data=bytearray(msg.data);data[6]=2;msg.data=bytes(data)
            return msg
        self.bus.recv=receive
        snapshot=self.motor.connect()
        self.assertEqual(snapshot['wireProtocol'],[2,0]);self.assertEqual(snapshot['capabilities']['modes'],0)
        with self.assertRaisesRegex(ProtocolError,'主版本'):self.motor.claim()
        self.assertEqual(self.bus.metric(1),0)

    def test_cache_reads_do_not_generate_can_commands_or_refresh_sample_timestamp(self):
        self.motor.connect();time.sleep(.05)
        with self.motor.lock:
            one=self.motor.latest_snapshot();sent=self.bus.metric(3)
            for _ in range(20):two=self.motor.latest_snapshot()
            self.assertEqual(one['receivedAt'],two['receivedAt']);self.assertEqual(sent,self.bus.metric(3))

if __name__=='__main__':unittest.main()
