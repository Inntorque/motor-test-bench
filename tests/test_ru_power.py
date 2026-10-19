import json
import struct
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import hardware_bridge as bridge


class RuTransport:
    def __init__(self, output=False, bad_crc=False):
        self.is_open=True;self.bad_crc=bad_crc;self.bad_echo=False
        self.inputs={100:1234,101:1250,103:6000,104:60000,106:2,107:3}
        self.holdings={149:2400,150:5000,157:6500,158:63000}
        self.bits={116:False,117:True,120:False,121:False,122:False}
        self.coils={133:output,136:True,137:True}
        self.frames=[];self.reply=bytearray()

    def reset_input_buffer(self):self.reply.clear()
    def close(self):self.is_open=False
    def read(self,count):
        data=bytes(self.reply[:count]);del self.reply[:count];return data

    def write(self,frame):
        self.frames.append(frame)
        assert bridge.crc16(frame[:-2])==frame[-2:]
        address,function,register,count=struct.unpack('>BBHH',frame[:6])
        if function in (1,2):
            registers=self.coils if function==1 else self.bits
            values=[registers[register+i] for i in range(count)]
            data=bytearray((count+7)//8)
            for i,value in enumerate(values):data[i//8]|=int(value)<<(i%8)
            response=bytes([address,function,len(data)])+data
        elif function in (3,4):
            registers=self.holdings if function==3 else self.inputs
            values=[registers[register+i] for i in range(count)]
            response=bytes([address,function,count*2])+struct.pack('>'+'H'*count,*values)
        elif function==16:
            assert register==149 and count==2, 'must only write the documented V/A setpoints'
            assert frame[6]==4
            values=struct.unpack('>HH',frame[7:-2])
            self.holdings.update({149:values[0],150:values[1]})
            response=frame[:6]
        elif function==5:
            assert register==133 and count in (0,0xff00), 'only the output coil is writable'
            self.coils[133]=count==0xff00
            response=frame[:6]
            if self.bad_echo:response=response[:-1]+bytes([response[-1]^1])
        else:raise AssertionError('Unexpected function')
        checksum=bridge.crc16(response)
        if self.bad_crc:checksum=bytes([checksum[0]^1,checksum[1]])
        self.reply=bytearray(response+checksum)

    def writes(self):return [frame for frame in self.frames if frame[1] in (5,6,16)]


class RuPowerTests(unittest.TestCase):
    def setUp(self):
        self.transport=RuTransport()
        self.channel=bridge.PowerSupplyChannel('motor-supply')
        self.channel._open_transport=lambda config:self.transport
        self.config=bridge.normalized_config({'protocol':'ru','transport':'serial','device':'TEST_RU'})
        self.pause=patch.object(bridge.time,'sleep');self.pause.start()

    def tearDown(self):
        self.channel.disconnect();self.pause.stop()

    def test_new_motor_defaults_and_serial_requirements(self):
        config=bridge.normalized_config({'protocol':'ru','transport':'serial','device':'TEST'},'motor-supply')
        self.assertEqual((config['protocol'],config['baud'],config['parity'],config['stopbits']),('ru',9600,'N',1))
        self.assertEqual((config['maxVoltage'],config['maxCurrent']),(24,10))
        for change in ({'parity':'E'},{'stopbits':2}):
            with self.assertRaises(bridge.BridgeError):bridge.normalized_config(self.config|change,'motor-supply')
        with self.assertRaises(bridge.BridgeError):bridge.normalized_config(self.config,'brake-supply')

    def test_connect_reads_only_documented_registers_and_scales_from_device(self):
        state=self.channel.connect(self.config)
        self.assertTrue(state['connected']);self.assertEqual(self.transport.writes(),[])
        self.assertEqual((state['config']['voltageDecimals'],state['config']['currentDecimals']),(2,3))
        self.assertEqual((state['config']['ratedVoltage'],state['config']['ratedCurrent']),(60,60))
        snapshot=state['snapshot']
        self.assertAlmostEqual(snapshot['measured'][2],15.425)
        self.assertTrue(snapshot['powerCalculated']);self.assertEqual(snapshot['settings'][1:3],[2400,5000])
        self.assertFalse(snapshot['status']&128);self.assertTrue(snapshot['ruStatus']['cv'])
        self.assertEqual(snapshot['protection']['current'],63)

    def test_set_and_output_use_new_map_and_echo_readback(self):
        self.channel.connect(self.config)
        self.channel.set_output(True,{'voltage':36.5,'current':7.25})
        self.assertEqual(self.transport.holdings[149],3650);self.assertEqual(self.transport.holdings[150],7250)
        self.assertTrue(self.channel.snapshot['status']&128)
        self.channel.set_output(False)
        self.assertFalse(self.channel.snapshot['status']&128)
        self.assertEqual(struct.unpack('>BBHH',self.transport.writes()[-1][:6]),(1,5,133,0))

    def test_limits_enforce_set_and_on_on_server(self):
        self.channel.connect(self.config)
        self.channel.set_limits({'maxVoltage':20,'maxCurrent':2})
        self.assertEqual(self.transport.writes(),[])
        for point in ({'voltage':21,'current':1},{'voltage':18,'current':3}):
            with self.assertRaises(bridge.BridgeError):self.channel.set_values(point)
            with self.assertRaises(bridge.BridgeError):self.channel.set_output(True,point)
        self.assertEqual(self.transport.writes(),[])
        self.channel.set_values({'voltage':18,'current':1.234})
        self.assertEqual(self.transport.holdings[150],1234)
        self.channel.set_output(True,{'voltage':18,'current':1.234})
        self.assertTrue(self.transport.coils[133])

    def test_limits_cannot_exceed_ratings_or_claim_tighter_live_output(self):
        self.channel.connect(self.config)
        with self.assertRaises(bridge.BridgeError):self.channel.set_limits({'maxVoltage':61,'maxCurrent':2})
        self.channel.set_output(True,{'voltage':24,'current':5})
        count=len(self.transport.writes())
        with self.assertRaises(bridge.BridgeError):self.channel.set_limits({'maxVoltage':20,'maxCurrent':2})
        self.assertIsNone(self.channel.config['maxVoltage'])
        self.assertEqual(len(self.transport.writes()),count)

    def test_rounding_cannot_cross_maximum_and_zero_cap_is_enforced(self):
        self.channel.connect(self.config)
        self.channel.set_limits({'maxVoltage':12.346,'maxCurrent':2})
        with self.assertRaises(bridge.BridgeError):self.channel.set_values({'voltage':12.346,'current':1})
        self.assertEqual(self.transport.writes(),[])
        self.channel.set_limits({'maxVoltage':0,'maxCurrent':0})
        with self.assertRaises(bridge.BridgeError):self.channel.set_values({'voltage':.01,'current':0})

    def test_active_protection_blocks_on_without_resetting_protection(self):
        self.channel.connect(self.config)
        for register in (120,121,122):
            self.transport.bits[register]=True
            with self.assertRaises(bridge.BridgeError):self.channel.set_output(True,{'voltage':12,'current':1})
            self.transport.bits[register]=False
        self.assertEqual(self.transport.writes(),[])

    def test_crc_error_and_wrong_write_echo_never_claim_success(self):
        self.transport.bad_crc=True
        with self.assertRaises(bridge.DeviceLinkError):self.channel.connect(self.config)
        self.assertFalse(self.channel.status()['connected'])
        self.transport=RuTransport();self.channel.connect(self.config);self.transport.bad_echo=True
        with self.assertRaises(bridge.DeviceLinkError):self.channel.set_output(False)
        self.assertFalse(self.channel.status()['connected'])

    def test_invalid_device_metadata_and_saved_caps_fail_readonly_handshake(self):
        self.transport.inputs[107]=99
        with self.assertRaises(bridge.DeviceLinkError):self.channel.connect(self.config)
        self.assertFalse(self.channel.status()['connected']);self.assertEqual(self.transport.writes(),[])
        self.transport=RuTransport()
        with self.assertRaises(bridge.BridgeError):self.channel.connect(self.config|{'maxVoltage':61})
        self.assertEqual(self.transport.writes(),[])

    def test_http_limits_require_token_and_block_bypass(self):
        class Quiet(bridge.Handler):
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Quiet);port=server.server_address[1]
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        def post(action,data,token=bridge.TOKEN):
            req=urllib.request.Request(f'http://127.0.0.1:{port}/api/supplies/motor-supply/{action}',data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-MotorBench-Token':token})
            with urllib.request.urlopen(req) as response:return json.load(response)
        with patch.object(bridge,'PORT',port),patch.object(bridge,'CHANNELS',{'motor-supply':self.channel}):
            worker.start()
            try:
                with self.assertRaises(urllib.error.HTTPError) as failure:post('limits',{'maxVoltage':20,'maxCurrent':2},'bad')
                self.assertEqual(failure.exception.code,403)
                post('connect',{'protocol':'ru','device':'TEST_RU','transport':'serial'})
                with self.assertRaises(urllib.error.HTTPError) as failure:post('limits',{'maxVoltage':20,'maxCurrent':2})
                self.assertEqual(failure.exception.code,400)
                limits=post('limits',{'maxVoltage':24,'maxCurrent':10})
                self.assertEqual(limits['config']['maxCurrent'],10)
                with self.assertRaises(urllib.error.HTTPError) as failure:post('output',{'on':True,'voltage':24,'current':1})
                self.assertEqual(failure.exception.code,400);self.assertEqual(self.transport.writes(),[])
                with self.assertRaises(urllib.error.HTTPError):post('set',{'voltage':25,'current':10})
                self.assertEqual(self.transport.writes(),[])
                result=post('output',{'on':True,'voltage':24,'current':10})
                self.assertEqual(result['settings'][1:3],[2400,10000])
                self.assertTrue(result['status'] & 128)
                self.assertFalse(post('output',{'on':False})['status'] & 128)
            finally:server.shutdown();server.server_close();worker.join(2)


if __name__=='__main__':unittest.main()
