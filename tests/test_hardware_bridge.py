import importlib.util
import pathlib
import struct
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("hardware_bridge", ROOT / "hardware_bridge.py")
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class FakeTransport:
    def __init__(self, output=False):
        self.is_open = True
        self.registers = {
            1000: 2400,
            1001: 0,
            1002: 0,
            1007: 16 | (128 if output else 0),
            2000: 1,
            2001: 2400,
            2002: 500,
            2003: 4800,
            2004: 5003,
            2005: 0,
            2016: 65535 if output else 0,
        }
        self.frames = []
        self.reply = bytearray()

    def reset_input_buffer(self):
        self.reply.clear()

    def write(self, frame):
        self.frames.append(frame)
        if bridge.crc16(frame[:-2]) != frame[-2:]:
            raise AssertionError("invalid request CRC")
        address, function, register = struct.unpack(">BBH", frame[:4])
        if function == 16:
            count = struct.unpack(">H", frame[4:6])[0]
            values = struct.unpack(">" + "H" * count, frame[7:-2])
            for offset, value in enumerate(values):
                self.registers[register + offset] = value
            if register == 2016:
                self.registers[1007] = 16 | (128 if values[0] else 0)
            response = frame[:6]
        else:
            count = struct.unpack(">H", frame[4:6])[0]
            values = [self.registers.get(register + offset, 0) for offset in range(count)]
            response = bytes((address, function, count * 2)) + struct.pack(">" + "H" * count, *values)
        self.reply = bytearray(response + bridge.crc16(response))

    def read(self, count):
        result = bytes(self.reply[:count])
        del self.reply[:count]
        return result

    def close(self):
        self.is_open = False


class FakeSensorTransport:
    def __init__(self, *, bad_crc=False):
        self.is_open = True
        self.bad_crc = bad_crc
        self.registers = {}
        values = {
            0x00: -1234,
            0x02: 2500,
            0x08: 2,
            0x18: 0,
            0x1C: 1,
            0x20: 1,
        }
        for address, value in values.items():
            words = struct.unpack(">HH", struct.pack(">i", value))
            self.registers[address] = words[0]
            self.registers[address + 1] = words[1]
        self.frames = []
        self.reply = bytearray()

    def reset_input_buffer(self):
        self.reply.clear()

    def write(self, frame):
        self.frames.append(frame)
        if bridge.crc16(frame[:-2]) != frame[-2:]:
            raise AssertionError("invalid sensor request CRC")
        address, function, register, count = struct.unpack(">BBHH", frame[:6])
        if function != 3:
            raise AssertionError("sensor bridge must remain read-only")
        values = [self.registers.get(register + offset, 0) for offset in range(count)]
        response = bytes((address, function, count * 2)) + struct.pack(">" + "H" * count, *values)
        checksum = bridge.crc16(response)
        if self.bad_crc:
            checksum = bytes((checksum[0] ^ 0xFF, checksum[1]))
        self.reply = bytearray(response + checksum)

    def read(self, count):
        result = bytes(self.reply[:count])
        del self.reply[:count]
        return result

    def close(self):
        self.is_open = False


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.motor_transport = FakeTransport()
        self.brake_transport = FakeTransport()
        self.channels = {
            "motor-supply": bridge.PowerSupplyChannel("motor-supply"),
            "brake-supply": bridge.PowerSupplyChannel("brake-supply"),
        }
        transports = [self.motor_transport, self.brake_transport]

        def open_transport(_channel, _config):
            return transports.pop(0)

        for channel in self.channels.values():
            channel._open_transport = lambda config, current=channel: open_transport(current, config)
        self.config = {
            "transport": "serial",
            "device": "COM_TEST",
            "baud": 9600,
            "address": 1,
            "parity": "N",
            "stopbits": 1,
            "voltageDecimals": 2,
            "currentDecimals": 2,
            "ratedVoltage": None,
            "ratedCurrent": None,
        }

    def test_http_brake_requires_current_setting_before_on_and_keeps_off_available(self):
        import json
        import threading
        import urllib.request
        import urllib.error
        from http.server import ThreadingHTTPServer
        channel = self.channels['brake-supply']
        # Use the second fake independently of the motor channel.
        channel._open_transport = lambda config: self.brake_transport
        server = ThreadingHTTPServer(('127.0.0.1', 0), bridge.Handler)
        port = server.server_address[1]
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        def post(action, data):
            request = urllib.request.Request(f'http://127.0.0.1:{port}/api/supplies/brake-supply/{action}', data=json.dumps(data).encode(), headers={'Content-Type':'application/json', 'X-MotorBench-Token':bridge.TOKEN})
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        with patch.object(bridge, 'PORT', port), patch.object(bridge, 'CHANNELS', self.channels), patch.object(bridge.time, 'sleep'):
            worker.start()
            try:
                result = post('connect', self.config)
                self.assertTrue(result['connected'])
                self.assertEqual(result['config']['maxCurrent'], 2.3)
                for action, values in [('set', {'voltage':24, 'current':2.301}), ('output', {'on':True, 'voltage':24, 'current':2.301})]:
                    with self.assertRaises(urllib.error.HTTPError):
                        post(action, values)
                with self.assertRaises(urllib.error.HTTPError):
                    post('output', {'on':True, 'voltage':24, 'current':1.25})
                with self.assertRaises(urllib.error.HTTPError):
                    post('set', {'voltage':25, 'current':1.25})
                self.assertFalse(any(frame[1] == 16 for frame in self.brake_transport.frames))
                self.assertFalse(post('set', {'voltage':24, 'current':1.25})['status'] & 128)
                self.assertTrue(post('output', {'on':True, 'voltage':24, 'current':1.25})['status'] & 128)
                self.assertFalse(post('output', {'on':False})['status'] & 128)
                self.assertEqual(self.brake_transport.registers[2001], 2400)
                self.assertEqual(post('set', {'voltage':24, 'current':2.3})['settings'][2], 230)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(2)
                channel.disconnect()

    def test_each_supply_connects_independently_and_tracks_real_protocol_state(self):
        motor = self.channels["motor-supply"]
        brake = self.channels["brake-supply"]
        motor.connect({**self.config, "device": "COM_MOTOR"})
        brake.connect({**self.config, "device": "COM_BRAKE"})

        self.assertTrue(motor.status()["connected"])
        self.assertTrue(brake.status()["connected"])
        motor.set_values({"voltage": 12, "current": 1.25})
        self.assertEqual(
            [self.motor_transport.registers[2001], self.motor_transport.registers[2002]],
            [1200, 125],
        )
        self.assertEqual(self.brake_transport.registers[2001], 2400)

        motor.set_output(True, {"voltage": 12, "current": 1.25})
        self.assertTrue(motor.snapshot["status"] & 0x80)
        self.assertFalse(brake.snapshot["status"] & 0x80)
        motor.disconnect()
        self.assertFalse(motor.status()["connected"])
        self.assertTrue(brake.status()["connected"])

    def test_output_is_read_back_and_off_does_not_touch_other_supply(self):
        motor = self.channels["motor-supply"]
        brake = self.channels["brake-supply"]
        motor.connect({**self.config, "device": "COM_MOTOR"})
        brake.connect({**self.config, "device": "COM_BRAKE"})
        motor.set_output(True, {"voltage": 24, "current": 5})
        self.assertEqual(motor.snapshot["settings"][1:3], [2400, 500])
        self.assertTrue(motor.snapshot["status"] & 0x80)
        motor.set_output(False)
        self.assertFalse(motor.snapshot["status"] & 0x80)
        self.assertFalse(brake.snapshot["status"] & 0x80)

    def test_failed_handshake_never_marks_channel_connected(self):
        channel = bridge.PowerSupplyChannel("motor-supply")

        class SilentTransport(FakeTransport):
            def write(self, _frame):
                self.reply.clear()

            def read(self, _count):
                return b""

        transport = SilentTransport()
        channel._open_transport = lambda _config: transport
        with patch.object(bridge.time, "sleep"):
            with self.assertRaises(bridge.BridgeError):
                channel.connect(self.config)
        self.assertFalse(channel.status()["connected"])
        self.assertIsNone(channel.status()["snapshot"])
        self.assertFalse(transport.is_open)

    def test_config_rejects_missing_tcp_port_and_duplicate_config_shape(self):
        with self.assertRaises(bridge.BridgeError):
            bridge.normalized_config({
                "transport": "tcp",
                "address": 1,
                "voltageDecimals": 2,
                "currentDecimals": 2,
                "host": "192.168.1.20",
            })

    def test_torque_sensor_connects_read_only_decodes_signed_values_and_scales(self):
        channel = bridge.TorqueSensorChannel()
        transport = FakeSensorTransport()
        config = bridge.normalized_sensor_config({
            "device": "COM_SENSOR",
            "baud": 19200,
            "address": 1,
            "stopbits": 2,
        })
        with patch.object(bridge.serial, "Serial", return_value=transport):
            connected = channel.connect(config)
            self.assertTrue(connected["connected"])
            self.assertEqual(connected["snapshot"]["torque"], -12.34)
            self.assertEqual(connected["snapshot"]["speed"], 250.0)
            self.assertEqual(connected["snapshot"]["rawTorque"], -1234)
            self.assertEqual(connected["snapshot"]["torqueDecimals"], 2)
            self.assertTrue(all(frame[1] == 3 for frame in transport.frames))

            updated = channel.read()
            self.assertTrue(updated["connected"])
            self.assertEqual(updated["snapshot"]["torque"], -12.34)
            self.assertTrue(all(frame[1] == 3 for frame in transport.frames))
            self.assertEqual(struct.unpack(">BBHH", transport.frames[-1][:6]), (1, 3, 0, 4))
            self.assertEqual(updated["snapshot"]["speed"], 250.0)
        channel.disconnect()

    def test_sensor_crc_failure_never_marks_device_connected(self):
        channel = bridge.TorqueSensorChannel()
        transport = FakeSensorTransport(bad_crc=True)
        config = bridge.normalized_sensor_config({
            "device": "COM_SENSOR",
            "baud": 19200,
            "address": 1,
            "stopbits": 2,
        })
        with patch.object(bridge.serial, "Serial", return_value=transport):
            with self.assertRaises(bridge.DeviceLinkError):
                channel.connect(config)
        self.assertFalse(channel.status()["connected"])
        self.assertIsNone(channel.status()["snapshot"])
        self.assertFalse(transport.is_open)

    def test_sensor_mode_and_serial_config_are_validated_without_parameter_writes(self):
        config = bridge.normalized_sensor_config({
            "device": "COM_SENSOR",
            "baud": 19200,
            "address": 1,
            "stopbits": 1,
        })
        self.assertEqual(config["parity"], "N")
        self.assertEqual(config["bytesize"], 8)
        with self.assertRaises(bridge.BridgeError):
            bridge.normalized_sensor_config({"device": "COM_SENSOR", "baud": 38400, "address": 1, "stopbits": 1})

    def test_sensor_must_confirm_modbus_mode_and_selected_stopbit(self):
        for register, low_word, stopbits in ((0x1C, 0, 2), (0x18, 1, 2)):
            channel = bridge.TorqueSensorChannel()
            transport = FakeSensorTransport()
            transport.registers[register] = 0
            transport.registers[register + 1] = low_word
            config = bridge.normalized_sensor_config({
                "device": "COM_SENSOR",
                "baud": 19200,
                "address": 1,
                "stopbits": stopbits,
            })
            with patch.object(bridge.serial, "Serial", return_value=transport):
                with self.assertRaises(bridge.DeviceLinkError):
                    channel.connect(config)
            self.assertFalse(channel.status()["connected"])
            self.assertFalse(transport.is_open)


if __name__ == "__main__":
    unittest.main()
