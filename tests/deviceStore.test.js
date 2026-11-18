import test from 'node:test';
import assert from 'node:assert/strict';
import {installApiClient,apiFetch} from '../web/src/apiClient.ts';
import {startDeviceStore,cachedResponse} from '../web/src/deviceStore.ts';

test('STOP confirmation bypasses an earlier cached disabled state',async()=>{
  const savedWindow=globalThis.window,savedSource=globalThis.EventSource;
  let source;const network=[];
  const reply=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'X-Control-Epoch':'2'}});
  globalThis.EventSource=class {constructor(){this.listeners={};source=this;}addEventListener(name,fn){this.listeners[name]=fn;}};
  globalThis.window={fetch:async(path,options={})=>{
    network.push(path);
    if(path==='/api/v1/state')return reply({control:{epoch:2},routes:{}});
    if(path==='/api/v1/health')return reply({token:'test'});
    if(path==='/api/v1/motor-current/stop')return reply({},202);
    if(path==='/api/v1/control/status')return reply({stopping:false,blocked:true,errors:['stop unconfirmed']});
    return reply({});
  },setInterval:()=>{},addEventListener:()=>{}};
  try {
    installApiClient();startDeviceStore(apiFetch);await new Promise(resolve=>setTimeout(resolve,0));
    source.listeners.state({data:JSON.stringify({data:{control:{epoch:2},routes:{'/api/control/status':{stopping:false,blocked:false}}}})});
    assert.equal((await cachedResponse('/api/control/status').json()).blocked,false);
    await apiFetch('/api/health');
    await assert.rejects(apiFetch('/api/motor-current/stop',{method:'POST',body:'{}'}),/stop unconfirmed/);
    assert.ok(network.includes('/api/v1/control/status'));
  }finally{globalThis.window=savedWindow;globalThis.EventSource=savedSource;}
});

test('late refresh cannot overwrite a newer stream snapshot; silent expiry notifies subscribers',async()=>{
  const savedWindow=globalThis.window,savedSource=globalThis.EventSource,savedNow=Date.now;
  const {startDeviceStore,subscribeDevices,cachedResponse}=await import('../web/src/deviceStore.ts?ordering');
  let source,finish,watchdog,time=10000;
  globalThis.window={setInterval:fn=>{watchdog=fn;}};
  globalThis.EventSource=class{constructor(){this.listeners={};source=this;}addEventListener(name,fn){this.listeners[name]=fn;}};
  Date.now=()=>time;const seen=[];const unsubscribe=subscribeDevices(value=>seen.push(value));
  try {
    startDeviceStore(()=>new Promise(resolve=>{finish=resolve;}));
    const current={devices:{'current-motor':{connected:true}},routes:{'/api/health':{ready:true}}};
    source.listeners.state({data:JSON.stringify({data:current})});
    finish(new Response(JSON.stringify({devices:{'current-motor':{connected:false}}})));
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(seen.length,1);assert.equal(seen[0].devices['current-motor'].connected,true);
    time+=1501;watchdog();assert.equal(seen.at(-1).streamOnline,false);assert.equal(cachedResponse('/api/health'),null);
    source.listeners.state({data:JSON.stringify({data:current})});assert.notEqual(seen.at(-1).streamOnline,false);
    assert.equal((await cachedResponse('/api/health').json()).ready,true);
    source.listeners.error();assert.equal(seen.at(-1).streamOnline,false);
  }finally{unsubscribe();Date.now=savedNow;globalThis.window=savedWindow;globalThis.EventSource=savedSource;}
});
