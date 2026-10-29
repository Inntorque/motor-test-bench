"""Bounded device event stream and immutable state publication."""
from collections import deque
import copy
import threading
import time

class EventHub:
    def __init__(self,capacity=2048):
        self.condition=threading.Condition();self.events=deque(maxlen=capacity);self.sequence=0;self.state={};self.listeners=[];self.listener_errors=deque(maxlen=32)
    def subscribe(self,callback):
        with self.condition:self.listeners.append(callback)
        def unsubscribe():
            with self.condition:self.listeners.remove(callback)
        return unsubscribe
    def publish(self,kind,payload):
        with self.condition:
            self.sequence+=1;event={'id':self.sequence,'type':kind,'at':time.time(),'monotonic':time.monotonic(),'data':copy.deepcopy(payload)}
            self.events.append(event)
            if kind=='state':self.state=event['data']
            listeners=list(self.listeners);self.condition.notify_all()
        for callback in listeners:
            try:callback(event)
            except Exception as error:
                with self.condition:self.listener_errors.append({'eventId':event['id'],'error':str(error)})
    def after(self,cursor,timeout=10,include=None):
        with self.condition:
            if self.sequence<=cursor:self.condition.wait(timeout)
            gap=bool(self.events and cursor and cursor<self.events[0]['id']-1)
            pending=[]
            for event in reversed(self.events):
                if event['id']<=cursor:break
                pending.append(copy.deepcopy(event) if include is None or event['type'] in include
                               else {'id':event['id'],'type':event['type']})
            pending.reverse()
            return gap,pending
    def snapshot(self):
        with self.condition:return copy.deepcopy(self.state)

HUB=EventHub()
