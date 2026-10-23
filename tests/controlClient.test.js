import test from 'node:test';
import assert from 'node:assert/strict';
import {installControlClient} from '../web/src/controlClient.js';

test('LAN HTTP without randomUUID supports control headers, stop confirmation and bridge restart',async()=>{
  const previousWindow=globalThis.window;
  const cryptoDescriptor=Object.getOwnPropertyDescriptor(globalThis,'crypto');
  const requests=[];
  let serverEpoch=4,serverToken='first',heartbeat,rejectStop=false;
  const response=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'X-Control-Epoch':String(serverEpoch)}});
  globalThis.window={
    fetch:async(path,options={})=>{
      requests.push([path,options]);
      if(path==='/api/health')return response({token:serverToken});
      if(path==='/api/control/status')return response({stopping:false,blocked:false,epoch:serverEpoch});
      if(path.endsWith('/output')&&JSON.parse(options.body).on===false){
        if(rejectStop){rejectStop=false;return response({},403);}
        return response({},202);
      }
      if(path.endsWith('/read'))return response({connected:true,snapshot:{status:0}});
      return response({});
    },
    setInterval:fn=>{heartbeat=fn;},addEventListener:()=>{},
  };
  Object.defineProperty(globalThis,'crypto',{configurable:true,value:{getRandomValues:array=>array.fill(11)}});
  try{
    installControlClient();
    await window.fetch('/api/health');
    await window.fetch('/api/supplies/motor-supply/output',{method:'POST',headers:{'X-MotorBench-Token':serverToken},body:'{"on":true}'});
    const first=requests.at(-1)[1].headers;
    assert.equal(first.get('X-Control-Epoch'),'4');
    assert.equal(first.get('X-Workbench-Client'),'0b'.repeat(16));
    const stopped=await window.fetch('/api/supplies/motor-supply/output',{method:'POST',headers:{'X-MotorBench-Token':serverToken},body:'{"on":false}'});
    assert.equal((await stopped.json()).snapshot.status,0);
    await heartbeat();
    assert.equal(requests.at(-1)[0],'/api/control/heartbeat');
    serverEpoch=0;serverToken='second';
    await window.fetch('/api/health');
    await window.fetch('/api/coupled-current/start',{method:'POST',body:'{}'});
    assert.equal(requests.at(-1)[1].headers.get('X-Control-Epoch'),'0');
    serverToken='third';rejectStop=true;
    const refreshed=await window.fetch('/api/supplies/motor-supply/output',{method:'POST',headers:{'X-MotorBench-Token':'second'},body:'{"on":false}'});
    assert.equal((await refreshed.json()).snapshot.status,0);
    const retried=requests.filter(([path])=>path.endsWith('/output')).at(-1)[1];
    assert.equal(retried.headers.get('X-MotorBench-Token'),'third');
  }finally{
    globalThis.window=previousWindow;
    Object.defineProperty(globalThis,'crypto',cryptoDescriptor);
  }
});
