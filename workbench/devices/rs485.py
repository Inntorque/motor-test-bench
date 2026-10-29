"""Passive sensor polling independent of browser pages and recording sessions."""
import threading
from sensor_hex import HexSensorChannel

class SensorMonitor:
    def __init__(self,sensor,observe):
        self.sensor,self.observe=sensor,observe;self.closed=threading.Event();self.thread=None
    def start(self):
        def run():
            while not self.closed.is_set():
                if self.sensor.connected and not isinstance(self.sensor.channel,HexSensorChannel):
                    try:self.sensor.read()
                    except (OSError,ValueError) as error:self.observe('sensor_read_error',{'error':str(error)})
                self.closed.wait(.3)
        self.thread=threading.Thread(target=run,name='sensor-rs485',daemon=True);self.thread.start()
    def close(self):
        self.closed.set()
        if self.thread:self.thread.join(2)
