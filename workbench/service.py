"""Single application server: UI, authenticated API and MBP/RS485 ownership."""
import argparse
import json
import signal
import threading
from http.server import ThreadingHTTPServer
import hardware_bridge as devices
from scripts.web_server import Handler as WebHandler,ROOT
from scripts.lan_access import Access,credentials
from scripts.instance_lock import InstanceLock
from workbench.events import HUB
from workbench.acquisition import Acquisition
from workbench.devices.rs485 import SensorMonitor

class Handler(WebHandler,devices.Handler):
    def _api(self):
        if not self.path.startswith('/api/v1/'):
            return self._send({'error':'请使用 /api/v1/ 接口'},410)
        self.path='/api/'+self.path[len('/api/v1/'):]
        aliases={'/api/tests/start':'/api/coupled-current/start','/api/tests/load':'/api/coupled-current/load','/api/tests/stop':'/api/coupled-current/stop','/api/tests/status':'/api/coupled-current/status','/api/motor/stop':'/api/motor-current/stop'}
        self.path=aliases.get(self.path,self.path)
        if self.command in ('GET','HEAD'):
            if self.path=='/api/state':return self._send(HUB.snapshot())
            if self.path=='/api/events':return self._events()
            return devices.Handler.do_GET(self)
        return devices.Handler.do_POST(self)
    def _valid_post(self):
        return self.headers.get('X-MotorBench-Token')==devices.TOKEN and (not self.headers.get('Origin') or self.headers['Origin']=='http://'+self.headers.get('Host',''))
    def _events(self):
        try:cursor=max(0,int(self.headers.get('Last-Event-ID','0')))
        except ValueError:return self._send({'error':'invalid event cursor'},400)
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-store');self.send_header('Connection','close');self.end_headers();self.close_connection=True
        try:
            initial_sequence=HUB.sequence
            initial=json.dumps({'id':initial_sequence,'type':'state','data':HUB.snapshot()},ensure_ascii=False,separators=(',',':'))
            self.wfile.write(f'event: state\ndata: {initial}\n\n'.encode());self.wfile.flush();cursor=cursor or initial_sequence
            while not self.server.closing.is_set():
                gap,events=HUB.after(cursor,10,include=('state','sensor_frames','sensor_stream'))
                if gap:events=[{'id':HUB.sequence,'type':'sensor_stream','data':{'reset':True,'eventGap':True}},{'id':HUB.sequence,'type':'state','data':HUB.snapshot()}]
                if not events:self.wfile.write(b': keepalive\n\n')
                for event in events:
                    cursor=event['id']
                    if event['type'] not in ('state','sensor_frames','sensor_stream'):continue
                    if event['type']=='state' and event['id']<=initial_sequence:continue
                    body=json.dumps(event,ensure_ascii=False,separators=(',',':'))
                    self.wfile.write(f'id: {cursor}\nevent: {event["type"]}\ndata: {body}\n\n'.encode())
                self.wfile.flush()
        except (OSError,TimeoutError):pass


def state():
    safety=devices.SAFETY.status();motor=devices.CURRENT_MOTOR.status();coupled={**devices.COUPLED_CURRENT.status(),'safety':safety}
    health={'app':'motor-test-bench-hardware','hostApi':1,'recordSchema':2,'wireProtocol':[1,0],'token':devices.TOKEN,'safety':safety,'channels':{**{key:channel.cached_status() for key,channel in devices.CHANNELS.items()},'torque-sensor':devices.TORQUE_SENSOR.status(),'current-motor':motor}}
    routes={'/api/health':health,'/api/control/status':safety,'/api/connections/status':devices.CONNECTIONS.status(),'/api/motor-current/status':{**motor,'safety':safety},'/api/coupled-current/status':coupled,'/api/brake-current/status':{**devices.BRAKE_CURRENT.status(),'safety':safety},'/api/recordings/status':{'current':devices.WORKBENCH_RECORDINGS.status()},'/api/automatic/status':{'current':devices.AUTOMATIC.status(),'motor':motor},'/api/specialized/status':{'current':devices.SPECIALIZED.status(),'availability':devices.SPECIALIZED.availability()}}
    routes['/api/test-subjects']=devices.TEST_SUBJECTS.status()
    return {'hostApi':1,'recordSchema':2,'routes':routes,'devices':health['channels'],'control':safety,'test':coupled,'recording':devices.WORKBENCH_RECORDINGS.status()}

def serve(port=5173,lan=False,connect=True):
    with InstanceLock(ROOT/'.runtime/application.lock'):
        _serve(port,lan,connect)

def _serve(port,lan,connect):
    if not (ROOT/'dist/index.html').is_file():raise RuntimeError('请先 npm run build')
    with ThreadingHTTPServer(('0.0.0.0' if lan else '127.0.0.1',port),Handler) as server:
        server.daemon_threads=True;server.lan_access=Access(credentials(ROOT)) if lan else None;server.closing=threading.Event()
        for manager in (devices.SPECIALIZED,devices.AUTOMATIC,devices.WORKBENCH_RECORDINGS):manager.recover_interrupted()
        sensor_monitor=SensorMonitor(devices.TORQUE_SENSOR,HUB.publish);sensor_monitor.start()
        acquisition=Acquisition(devices.AUTOMATIC.source,devices.SPECIALIZED.frames,HUB);acquisition.start()
        devices.SAFETY.start()
        if connect:devices.CONNECTIONS.start({})
        def publish():
            while not server.closing.is_set():
                HUB.publish('state',state());server.closing.wait(.2)
        thread=threading.Thread(target=publish,name='device-store',daemon=True);thread.start()
        def terminate(*_):threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGTERM,terminate)
        if hasattr(signal,'SIGBREAK'):signal.signal(signal.SIGBREAK,terminate)
        print(f'MotorBench MBP/1 http://127.0.0.1:{port}',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:
            server.closing.set();thread.join(1)
            devices.close_devices()
            acquisition.close()
            sensor_monitor.close()
            devices.CURRENT_MOTOR.device.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=5173);parser.add_argument('--lan',action='store_true');parser.add_argument('--no-connect',action='store_true')
    args=parser.parse_args();serve(args.port,args.lan,not args.no_connect)
