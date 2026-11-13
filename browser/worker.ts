import type {MainToWorker, IoArguments, IoMethod, DeviceInput, DeviceIo} from './messages.ts';
import {BrowserStore} from './storage.ts';
import {Bench} from './bench.ts';
import {LegacyApp} from './legacy.ts';
import {FocApp} from './foc.ts';
import {isFocApp} from './messages.ts';
import {jsonValue} from './bytes.ts';
type Engine = Pick<Bench, 'initialize' | 'request'> & Partial<Pick<Bench, 'device' | 'channels' | 'sensor' | 'epoch'>> & {ioError?: (device: string, error: string) => void};
let engine: Engine,ready: Promise<void>;
const pending = new Map(), subscriptions = new Map(); let nextIo = 0;
const emit = (event,data) => postMessage({kind:'event',event,data:jsonValue(data)});
const io: DeviceIo = {
  call(method: IoMethod,args: IoArguments) {
    const id = ++nextIo;
    return new Promise((resolve,reject) => {pending.set(id,{resolve,reject});postMessage({kind:'io-call',id,method,args});});
  },
  subscribe(device: string,callback: (data: DeviceInput) => void) {
    const listeners = subscriptions.get(device) || new Set(); listeners.add(callback); subscriptions.set(device,listeners);
    return () => {listeners.delete(callback);if (!listeners.size) subscriptions.delete(device);};
  },
};
self.addEventListener('message',(event: MessageEvent<MainToWorker>) => {
  const message = event.data as MainToWorker;
  if (message.kind === 'init') {
    ready = (async () => {
      const store = await new BrowserStore(message.app).open();
      if (message.app === 'dynamometer') engine = new Bench(io,store,emit);
      else if (isFocApp(message.app)) engine = new FocApp(message.app,io,store,emit);
      else engine = new LegacyApp(message.app,io,store,emit);
      await engine.initialize(); postMessage({kind:'ready'});
    })();
    ready.catch(error => postMessage({kind:'fatal',error:error.message}));
  } else if (message.kind === 'io-result') {
    const item = pending.get(message.id); if (!item) return;
    pending.delete(message.id); if (message.error) item.reject(new Error(message.error)); else item.resolve(message.value);
  } else if (message.kind === 'input') {
    for (const callback of subscriptions.get(message.device) || []) callback(message.data);
  } else if (message.kind === 'io-error') {
    if (engine?.device?.bus?.device === message.device) engine.device.fail(new Error(message.error));
    for (const channel of Object.values(engine?.channels || {})) if (channel.config?.device === message.device) {channel.connected = false;channel.error = message.error;}
    if (engine?.sensor?.config?.device === message.device) {engine.sensor.connected = false;engine.sensor.error = message.error;}
    engine?.ioError?.(message.device,message.error);
    emit('device_error',{device:message.device,error:message.error});
  } else if (message.kind === 'request') {
    void (async () => {
      await ready;
      try {
        const result = await engine.request(message.path,message.method,message.body,message.headers);
        const status = result?.httpStatus || 200;
        postMessage({kind:'response',id:message.id,status,result:jsonValue(result),epoch:engine.epoch || 0});
      } catch (error) {postMessage({kind:'response',id:message.id,status:400,result:{error:error.message},epoch:engine.epoch || 0});}
    })().catch(error => postMessage({kind:'response',id:message.id,status:503,result:{error:error.message}}));
  }
});
