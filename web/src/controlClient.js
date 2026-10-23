// One live document owns control; read-only viewers never renew its lease.
export function installControlClient() {
  const nativeFetch=window.fetch.bind(window);
  // LAN HTTP is not a secure context; randomUUID may be unavailable there.
  const client=typeof crypto.randomUUID==='function'?crypto.randomUUID()
    :Array.from(crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join('');
  let epoch=0,token='',heartbeat=false;
  const startRoutes=new Set(['/api/coupled-current/start','/api/coupled-current/load','/api/motor-current/start','/api/brake-current/start','/api/automatic/start']);
  const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const receive=response=>{
    const value=response.headers.get('X-Control-Epoch');
    if(value!==null)epoch=Math.max(epoch,Number(value)||0);
    return response;
  };
  async function emergencyStop() {
    if(!token)return;
    try {receive(await nativeFetch('/api/control/stop',{method:'POST',headers:{'X-MotorBench-Token':token},
      body:'{}',keepalive:true,signal:AbortSignal.timeout(3000)}));}catch{}
  }
  window.fetch=async (url,options={})=>{
    const path=typeof url==='string'?url:'';
    if(!path.startsWith('/api/'))return nativeFetch(url,options);
    const method=(options.method||'GET').toUpperCase();
    const headers=new Headers(options.headers||{});
    if(method==='POST'){
      headers.set('X-Workbench-Client',client);headers.set('X-Control-Epoch',String(epoch));
      if(headers.get('X-MotorBench-Token'))token=headers.get('X-MotorBench-Token');
    }
    let data={};try{data=JSON.parse(options.body||'{}');}catch{}
    const stop=method==='POST' && (path.endsWith('/stop')&&!path.startsWith('/api/recordings/')&&!path.startsWith('/api/specialized/')
      ||path.startsWith('/api/supplies/')&&path.endsWith('/output')&&data.on===false);
    const energizing=method==='POST' && (startRoutes.has(path)
      ||path.startsWith('/api/supplies/')&&path.endsWith('/output')&&data.on===true);
    try {
      let response=receive(await nativeFetch(url,{...options,headers}));
      if(stop&&response.status===403){
        // Refresh a bridge-restart token only for idempotent STOP, never ON.
        const health=receive(await nativeFetch('/api/health',{cache:'no-store',signal:AbortSignal.timeout(2000)}));
        if(health.ok){
          token=(await health.json()).token||token;
          epoch=Number(health.headers.get('X-Control-Epoch'))||0;
          headers.set('X-MotorBench-Token',token);
          response=receive(await nativeFetch(url,{...options,headers}));
        }
      }
      if(path==='/api/health'&&response.ok){
        const next=(await response.clone().json()).token||token;
        if(next!==token)epoch=Number(response.headers.get('X-Control-Epoch'))||0;
        token=next;
      }
      if(stop&&response.status===202){
        const deadline=performance.now()+12000;
        let safety;
        do {
          await sleep(100);
          const state=receive(await nativeFetch('/api/control/status',{cache:'no-store',signal:AbortSignal.timeout(2000)}));
          if(!state.ok)throw new Error('停机状态读取失败，结果未确认');
          safety=await state.json();
        }while(safety.stopping&&performance.now()<deadline);
        if(safety.stopping||safety.blocked)throw new Error(`关闭未全部确认：${safety.errors?.join('；')||safety.reason}`);
        const target=path.startsWith('/api/supplies/')?path.replace('/output','/read')
          :path.replace('/stop','/status');
        const readOptions=target.endsWith('/read')?{method:'POST',headers:{'X-MotorBench-Token':token},body:'{}'}:{};
        const state=receive(await nativeFetch(target,{...readOptions,cache:'no-store',signal:AbortSignal.timeout(2500)}));
        const result=await state.json();
        response=new Response(JSON.stringify({...result,safety}),{status:state.status,headers:{'Content-Type':'application/json'}});
      }
      return response;
    }catch(error){
      // An aborted request may still have reached the server. Cancel it; never
      // retry ON/start automatically after an unknown network outcome.
      if(energizing)void emergencyStop();
      throw error;
    }
  };
  window.setInterval(async()=>{
    if(!token||heartbeat)return;
    heartbeat=true;
    try{receive(await nativeFetch('/api/control/heartbeat',{method:'POST',headers:{
      'X-MotorBench-Token':token,'X-Workbench-Client':client},body:'{}',signal:AbortSignal.timeout(1500)}));}catch{}
    finally{heartbeat=false;}
  },750);
  window.addEventListener('pagehide',()=>{
    // Owner-only lease revocation; closing a spectator cannot stop another user.
    if(token)void nativeFetch('/api/control/release',{method:'POST',headers:{'X-MotorBench-Token':token,
      'X-Workbench-Client':client},body:'{}',keepalive:true}).catch(()=>{});
  });
}
