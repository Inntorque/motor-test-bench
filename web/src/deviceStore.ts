import {FrameBuffer} from './frameBuffer.ts';
import {eventData} from '../../browser/events.ts';

let state=null,received=0,stream=null;
const listeners=new Set<(value: typeof state) => void>();
const frameRows=new FrameBuffer();
const frameListeners=new Set<() => void>();
let frameConfig={},frameReset=false;
export function subscribeSensorFrames(callback: () => void) {
  frameListeners.add(callback);
  return()=>{frameListeners.delete(callback);};
}
export function subscribeDevices(callback) {
  listeners.add(callback);
  if(state)callback(state);
  return()=>{listeners.delete(callback);};
}
export function cachedResponse(url) {
  if(!state||Date.now()-received>1500||typeof url!=='string')return null;
  const [path,query='']=url.replace('/api/v1/','/api/').split('?');
  if(new URLSearchParams(query).get('history')==='1')return null;
  if(path==='/api/sensors/torque-sensor/frames') {
    const since=Number(new URLSearchParams(query).get('since'))||0;
    const data=sensorFrameBatch(since);
    if(!data)return null;
    return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
  }
  const data=state.routes?.[path];
  if(!data)return null;
  return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json','X-Control-Epoch':String(state.control?.epoch||0)}});
}
export function sensorFrameBatch(since) {
  if(!state||Date.now()-received>1500)return null;
  const data={...state.devices?.['torque-sensor'],config:frameConfig,frames:frameRows.after(since),
    reset:frameReset||!!(frameRows.length&&since>frameRows.at(frameRows.length-1)[0]),
    dropped:since&&frameRows.length?Math.max(0,frameRows.at(0)[0]-since-1):0};
  frameReset=false;
  return data;
}
function apply(next) {state=next;received=Date.now();for(const listener of listeners)listener(state);}
export function startDeviceStore(request) {
  if(stream)return;
  const refresh=async()=>{
    try {const response=await request('/api/state',{signal:AbortSignal.timeout(3000)});if(response.ok)apply(await response.json());}catch{}
  };
  void refresh();
  stream=new EventSource('/api/v1/events');
  stream.addEventListener('open',()=>{void refresh();});
  stream.addEventListener('state',event=>{apply(eventData(event));});
  stream.addEventListener('sensor_frames',event=>{
    const packet=eventData<{config: object; frames: [number, ...number[]][]}>(event);frameConfig=packet.config;
    frameRows.append(packet.frames);
    for(const listener of frameListeners)listener();
  });
  stream.addEventListener('sensor_stream',event=>{
    const packet=eventData<{config: object; reset?: boolean}>(event);frameConfig=packet.config;
    if(packet.reset){frameRows.clear();frameReset=true;}
  });
  stream.addEventListener('error',()=>{
    received=0;
    if(state)for(const listener of listeners)listener({...state,streamOnline:false});
  });
}
