import ctypes
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
_build=tempfile.TemporaryDirectory(prefix='mbp-simulator-')
_library=Path(_build.name)/'simulator.so'
subprocess.run(['cc','-shared','-fPIC','-std=c11','-Wall','-Wextra','-Werror','-O2','-I',str(ROOT/'motor_sdk/include'),str(ROOT/'motor_sdk/src/mb_sdk.c'),str(ROOT/'motor_sdk/tests/simulator.c'),'-o',str(_library)],check=True)
lib=ctypes.CDLL(str(_library));lib.sim_create.restype=ctypes.c_void_p
for name,args in {'sim_destroy':[ctypes.c_void_p],'sim_step':[ctypes.c_void_p,ctypes.c_uint64],'sim_receive':[ctypes.c_void_p,ctypes.c_uint16,ctypes.c_void_p],'sim_pop':[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p],'sim_metric':[ctypes.c_void_p,ctypes.c_uint],'sim_maximum':[ctypes.c_void_p,ctypes.c_uint],'sim_motion':[ctypes.c_void_p,ctypes.c_int32]}.items():getattr(lib,name).argtypes=args

class SdkBus:
    def __init__(self,realtime=True):
        self.context=lib.sim_create();self.lock=threading.RLock();self.realtime=realtime;self.now=1000000
        if not self.context:raise RuntimeError('SDK simulator init failed')
    def send(self,msg):
        with self.lock:lib.sim_receive(self.context,msg.arbitration_id,ctypes.create_string_buffer(bytes(msg.data),8))
    def step(self,us=1000):
        with self.lock:
            self.now=int(time.monotonic()*1e6) if self.realtime else self.now+us
            lib.sim_step(self.context,self.now)
    def recv(self,timeout=.02):
        deadline=time.monotonic()+timeout
        while True:
            self.step();identifier=ctypes.c_uint16();data=ctypes.create_string_buffer(8)
            with self.lock:available=lib.sim_pop(self.context,ctypes.byref(identifier),data)
            if available:return SimpleNamespace(arbitration_id=identifier.value,data=data.raw,is_extended_id=False)
            if not self.realtime or time.monotonic()>=deadline:return None
            time.sleep(.0005)
    def metric(self,key):
        with self.lock:return lib.sim_metric(self.context,key)
    def shutdown(self):
        with self.lock:
            if self.context:lib.sim_destroy(self.context);self.context=None
