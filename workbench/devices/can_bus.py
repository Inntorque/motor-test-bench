"""One bounded, priority-scheduled owner for a CAN adapter."""
import heapq
import itertools
import threading
from types import SimpleNamespace

class CanBusManager:
    def __init__(self,bus,receive,on_error):
        self.bus,self.receive,self.on_error=bus,receive,on_error
        self.condition=threading.Condition();self.queue=[];self.serial=itertools.count();self.epoch=0
        self.closed=threading.Event();self.error='';self.sent=0;self.received=0
        self.threads=[threading.Thread(target=self._tx,name='can-tx',daemon=True),threading.Thread(target=self._rx,name='can-rx',daemon=True)]
        for t in self.threads:t.start()
    def send(self,identifier,data,priority=1):
        with self.condition:
            if self.closed.is_set() or self.error:raise OSError(self.error or 'CAN closed')
            if len(self.queue)>=256:raise OSError('CAN transmit queue full')
            heapq.heappush(self.queue,(priority,next(self.serial),self.epoch,identifier,bytes(data)));self.condition.notify()
    def stop(self,identifier,data):
        with self.condition:
            self.epoch+=1;self.queue.clear()
            heapq.heappush(self.queue,(0,next(self.serial),self.epoch,identifier,bytes(data)));self.condition.notify()
    def _tx(self):
        try:
            while not self.closed.is_set():
                with self.condition:
                    if not self.queue:self.condition.wait(.05);continue
                    _,_,epoch,identifier,data=heapq.heappop(self.queue)
                    if epoch!=self.epoch:continue
                # One already dequeued frame can precede STOP on the wire.
                self.bus.send(SimpleNamespace(arbitration_id=identifier,data=data,is_extended_id=False,is_remote_frame=False))
                self.sent+=1
        except Exception as error:self._fail(error)
    def _rx(self):
        try:
            while not self.closed.is_set():
                msg=self.bus.recv(.02)
                if msg is None:continue
                if getattr(msg,'is_extended_id',False) or getattr(msg,'is_remote_frame',False):continue
                self.received+=1;self.receive(msg.arbitration_id,bytes(msg.data))
        except Exception as error:self._fail(error)
    def _fail(self,error):
        self.error=str(error);self.closed.set();self.on_error(self.error)
    def close(self):
        self.closed.set()
        with self.condition:self.queue.clear();self.condition.notify_all()
        for t in self.threads:
            if t is not threading.current_thread():t.join(1.5)
        self.bus.shutdown()
