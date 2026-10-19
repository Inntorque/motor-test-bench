import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hardware_bridge import crc16, normalized_sensor_config
from sensor_hex import HexParser, HexSensorChannel


def frame6(torque, speed=0):
    data=abs(torque).to_bytes(2,'big')+((speed | (0x8000 if torque<0 else 0))).to_bytes(2,'big')
    return data+crc16(data)


class HexTests(unittest.TestCase):
    def test_signed_hex6_fragmentation_noise_and_resync(self):
        p=HexParser('hex6',crc16)
        raw=b'noise'+frame6(-1234,2500)*3
        out=[]
        for b in raw: out.extend(p.feed(bytes([b])))
        self.assertEqual([(a,b) for a,b,_ in out],[(-1234,2500)]*3)
        self.assertEqual(p.crc_order,'low-first')
        self.assertGreaterEqual(p.skipped,5)
        bad=bytearray(frame6(5));bad[-1]^=1
        out=p.feed(bytes(bad)+frame6(200,40)*3)
        self.assertEqual([(a,b) for a,b,_ in out],[(200,40)]*3)

    def test_hex8_signed24_and_high_first_crc(self):
        data=bytes.fromhex('ff ff fe 12 34 56')
        frame=data+crc16(data)[::-1]
        p=HexParser('hex8',crc16)
        self.assertEqual([(a,b) for a,b,_ in p.feed(frame*3)],[(-2,0x123456)]*3)
        self.assertEqual(p.crc_order,'high-first')

    def test_zero_seed_crc_needs_nonzero_frames_before_locking(self):
        from sensor_hex import crc_ibm0
        parser = HexParser('hex6', crc16)
        self.assertEqual(parser.feed(bytes(60)), [])
        self.assertIsNone(parser.crc_profile)
        data = bytes.fromhex('00 7b 00 14')
        frame = data + crc_ibm0(data)
        self.assertEqual(len(parser.feed(frame * 3)), 3)
        self.assertEqual(parser.crc_profile, 'ibm-0000')
        self.assertEqual(len(parser.feed(bytes(6))), 1)

    def test_bulk_stream_is_not_decimated(self):
        p=HexParser('hex6',crc16)
        out=p.feed(frame6(123)*10000)
        self.assertEqual(len(out),10000)
        self.assertEqual(len(p.buffer),0)

    def test_receiver_never_transmits_and_export_keeps_each_frame(self):
        class PassivePort:
            is_open=True
            def __init__(self): self.data=frame6(-1234,2500)*3
            @property
            def in_waiting(self): return len(self.data)
            def read(self,count):
                if self.data:
                    result,self.data=self.data[:count],self.data[count:]
                    return result
                time.sleep(.005)
                return b''
            def write(self,*_): raise AssertionError('HEX must never transmit')
            def close(self): self.is_open=False
        channel=HexSensorChannel(crc16)
        config=normalized_sensor_config({'device':'FAKE','protocol':'hex6','baud':115200,
                                        'stopbits':1,'torqueDecimals':2,'speedDecimals':1})
        with patch('sensor_hex.serial.Serial',return_value=PassivePort()):
            result=channel.connect(config)
            self.assertTrue(result['connected'])
            self.assertEqual(result['snapshot']['torque'],-12.34)
            self.assertEqual(result['snapshot']['speed'],250)
            self.assertEqual(channel.read()['snapshot']['framesReceived'],3)
            rows=channel.capture_csv().splitlines()
            self.assertEqual(len(rows),4)
            self.assertEqual([row.split(',')[0] for row in rows[1:]],['1','2','3'])
            channel.disconnect()
        self.assertFalse(channel.status()['connected'])

    def test_batch_cursor_and_overflow(self):
        channel = HexSensorChannel(crc16)
        channel.frames.extend([(11, 1.0, 2300, 0), (12, 1.001, -3500, 3)])
        self.assertEqual([f[0] for f in channel.batch(11)['frames']], [12])
        self.assertEqual(channel.batch(5)['dropped'], 5)
        self.assertTrue(channel.batch(100)['reset'])
        self.assertEqual(len(channel.batch(100)['frames']), 2)
        self.assertEqual(channel.batch(12)['frames'], [])

    def test_batch_only_visits_new_frames_in_full_buffer(self):
        from collections import deque
        class CountedDeque(deque):
            visits = 0
            def __reversed__(self):
                for frame in super().__reversed__():
                    self.visits += 1
                    yield frame
        channel = HexSensorChannel(crc16)
        channel.frames = CountedDeque((seq, 1., 0, 0) for seq in range(1, 60001))
        batch = channel.batch(59980)
        self.assertEqual(len(batch['frames']), 20)
        self.assertEqual(channel.frames.visits, 21)
        self.assertEqual([f[0] for f in batch['frames']], list(range(59981, 60001)))


if __name__=='__main__': unittest.main()
