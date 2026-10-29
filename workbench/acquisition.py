"""Shared cached acquisition; consumers never poll CAN or RS485 themselves."""
import threading
import time

class Acquisition:
    def __init__(self,source,frames,hub):
        self.source,self.frames,self.hub=source,frames,hub;self.closed=threading.Event();self.thread=None
    def start(self):
        self.thread=threading.Thread(target=self._run,name='shared-acquisition',daemon=True);self.thread.start()
    def _run(self):
        cursor=0;previous={};sensor_config=None
        while not self.closed.is_set():
            began=time.monotonic();sources=self.source();packet=self.frames(cursor)
            self.hub.publish('acquisition',{'sources':sources,'packet':packet})
            for key,state in sources.items():
                stamp=(state.get('snapshot') or {}).get('receivedAt')
                signature=(state.get('connected'),stamp,state.get('error'),(state.get('run') or {}).get('targetA'))
                if previous.get(key)!=signature:
                    previous[key]=signature;self.hub.publish('snapshot',{'channel':key,'state':state})
            if packet is not None:
                config=packet.get('config') or {}
                if config!=sensor_config or packet.get('reset') or packet.get('dropped'):
                    self.hub.publish('sensor_stream',{'config':config,'reset':packet.get('reset',False),'droppedFrames':packet.get('dropped',0),'previousSequence':cursor});sensor_config=config
                if packet.get('frames'):
                    self.hub.publish('sensor_frames',{'config':config,'frames':packet['frames']});cursor=packet['frames'][-1][0]
            self.closed.wait(max(0,.02-(time.monotonic()-began)))
    def close(self):
        self.closed.set()
        if self.thread:self.thread.join(1)
