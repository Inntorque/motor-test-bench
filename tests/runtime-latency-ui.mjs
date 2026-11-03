import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,extname} from 'node:path';
import {chromium} from 'playwright';
import {build} from 'vite';

const directory=resolve(process.env.LATENCY_SITE_DIR || 'site'),baseline=!!process.env.LATENCY_SITE_DIR;
const tag=baseline?'before':'after',output=resolve('.runtime/latency-20261103');await mkdir(output,{recursive:true});
await build({configFile:false,publicDir:false,build:{outDir:resolve(output,'fixture'),emptyOutDir:true,minify:true,
  lib:{entry:resolve('tests/fixtures/browser-serial.js'),name:'BrowserSerialFixture',formats:['iife'],fileName:()=> 'fixture.js'}}});
const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml','.png':'image/png'};
const server=createServer(async(req,res)=>{
  const path=new URL(req.url,'http://localhost').pathname,file=resolve(directory,'.'+(path.endsWith('/')?path+'index.html':path));
  try {assert.ok(file.startsWith(directory+'/'));res.setHeader('Content-Type',types[extname(file)]||'application/octet-stream');res.end(await readFile(file));}
  catch {res.writeHead(404).end();}
});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
const base=`http://127.0.0.1:${server.address().port}/`,browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
const errors=[],result={baseline,visuals:[],animation:[],worker:{}};
try {
  for(const id of ['dynamometer','sg-6010hb','rm-7010','bootloader']) {
    const context=await browser.newContext({viewport:{width:1366,height:900}});
    await context.addInitScript(()=>{
      const NativeDate=Date,fixed=Date.parse('2026-11-03T06:00:00Z');
      window.Date=class extends NativeDate {constructor(...args){super(...(args.length?args:[fixed]));}static now(){return fixed;}};
      window.__rafCount=0;const raf=requestAnimationFrame.bind(window);window.requestAnimationFrame=fn=>{window.__rafCount++;return raf(fn);};
      Object.defineProperty(navigator,'serial',{value:{getPorts:async()=>[],addEventListener(){}}});
      Object.defineProperty(navigator,'usb',{value:{getDevices:async()=>[],addEventListener(){}}});
    });
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+`apps/${id}/`);await page.getByRole('status').filter({hasText:'网页已就绪'}).first().waitFor();
    if(id==='dynamometer')await page.getByRole('heading',{name:'扭矩工作台',exact:true}).waitFor();
    await page.waitForTimeout(800);
    const png=await page.screenshot({path:resolve(output,`${id}-${tag}.png`),animations:'disabled'});
    const text=await page.locator('body').innerText();
    await writeFile(resolve(output,`${id}-${tag}.txt`),text);
    if(process.env.LATENCY_COMPARE_VISUALS) {
      assert.equal(text,await readFile(resolve(output,`${id}-before.txt`),'utf8'),`${id} visible text changed`);
      const before=await readFile(resolve(output,`${id}-before.png`));
      if(!png.equals(before)) {
        const difference=await page.evaluate(async images=>{
          const canvas=document.createElement('canvas'),context=canvas.getContext('2d'),pixels=[];
          for(const image of images) {
            const bitmap=await createImageBitmap(new Blob([Uint8Array.from(atob(image),c=>c.charCodeAt(0))]));
            canvas.width=bitmap.width;canvas.height=bitmap.height;context.drawImage(bitmap,0,0);
            pixels.push(context.getImageData(0,0,canvas.width,canvas.height).data);bitmap.close();
          }
          let changed=0,maximum=0;
          for(let i=0;i<pixels[0].length;i+=4) {
            let delta=0;for(let c=0;c<4;c++)delta=Math.max(delta,Math.abs(pixels[0][i+c]-pixels[1][i+c]));
            if(delta)changed++;maximum=Math.max(maximum,delta);
          }
          return {changedPixels:changed,maxChannelDelta:maximum};
        },[before.toString('base64'),png.toString('base64')]);
        assert.ok(difference.changedPixels<=Math.ceil(1366*900*.0002)&&difference.maxChannelDelta<=5,`${id} screenshot changed: ${JSON.stringify(difference)}`);
        result.visuals.push({id,unchanged:true,rasterDifference:difference});
      } else result.visuals.push({id,unchanged:true,rasterDifference:{changedPixels:0,maxChannelDelta:0}});
    } else {
      result.visuals.push({id,unchanged:false});
    }
    if(['sg-6010hb','rm-7010'].includes(id)) {
      const canvas=page.locator('#curves');await canvas.scrollIntoViewIfNeeded();await page.waitForTimeout(250);
      const original=await canvas.evaluate(el=>el.toDataURL());
      const count=()=>page.evaluate(()=>window.__rafCount);
      let start=await count();await page.waitForTimeout(400);const visible=await count()-start;assert.ok(visible>5);
      await page.evaluate(()=>{const spacer=document.createElement('div');spacer.id='latency-spacer';spacer.style.height='2500px';document.body.append(spacer);window.scrollTo({top:document.body.scrollHeight,behavior:'instant'});});
      await page.waitForFunction(()=>document.getElementById('curves').getBoundingClientRect().bottom<0);
      await page.waitForTimeout(250);start=await count();await page.waitForTimeout(400);const offscreen=await count()-start;
      if(!baseline)assert.equal(offscreen,0,'offscreen waveform must stop requesting animation frames');
      await canvas.scrollIntoViewIfNeeded();await page.waitForTimeout(250);
      assert.equal(await canvas.evaluate(el=>el.toDataURL()),original,'the fixed-clock waveform must resume without changing pixels');
      await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
      await page.waitForTimeout(100);start=await count();await page.waitForTimeout(400);const hidden=await count()-start;
      if(!baseline)assert.equal(hidden,0,'hidden waveform must stop requesting animation frames');
      await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});
      start=await count();await page.waitForTimeout(300);assert.ok(await count()-start>5);
      result.animation.push({id,visibleRequestsIn400ms:visible,offscreenRequestsIn400ms:offscreen,hiddenRequestsIn400ms:hidden,resumedIdentically:true});
    }
    await context.close();
  }
  const context=await browser.newContext({viewport:{width:1366,height:900}});
  await context.addInitScript({content:await readFile(resolve(output,'fixture/fixture.js'),'utf8')});
  await context.addInitScript(()=>{
    window.__json={stringifies:0,parses:0};
    const stringify=JSON.stringify,parse=JSON.parse;
    JSON.stringify=function(...args){window.__json.stringifies++;return stringify(...args);};
    JSON.parse=function(...args){window.__json.parses++;return parse(...args);};
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'apps/dynamometer/');await page.getByRole('status').filter({hasText:'网页已就绪'}).first().waitFor();
  await page.getByRole('button',{name:'授权串口',exact:true}).click();
  await page.evaluate(async()=>{
    const h=await(await fetch('/api/v1/health')).json();
    const r=await fetch('/api/v1/motor-current/connect',{method:'POST',headers:{'X-MotorBench-Token':h.token,'X-Workbench-Client':'latency-read-only-0001','X-Control-Epoch':String(h.safety.epoch)},body:'{}'});
    const value=await r.json();if(!r.ok)throw Error(value.error);
  });
  await page.waitForTimeout(1200);
  const cdp=await context.newCDPSession(page);await cdp.send('Performance.enable');
  const metrics=async()=>Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(m=>[m.name,m.value]));
  const first=await metrics(),json=await page.evaluate(()=>({...window.__json}));
  await page.waitForTimeout(3000);
  const last=await metrics();
  const deltas=await page.evaluate(start=>({stringifies:window.__json.stringifies-start.stringifies,parses:window.__json.parses-start.parses}),json);
  const latencies=await page.evaluate(async()=>{
    const values=[];
    for(let i=0;i<120;i++){const t=performance.now();const r=await fetch('/api/v1/health');await r.json();values.push(performance.now()-t);}
    return values.sort((a,b)=>a-b);
  });
  assert.equal(await page.evaluate(()=>window.__serialFixture.board.commands.some(c=>c.opcode===6)),false,'profiling must not start even the virtual motor');
  result.worker={virtualSerialOnly:true,...deltas,taskSecondsIn3s:last.TaskDuration-first.TaskDuration,scriptSecondsIn3s:last.ScriptDuration-first.ScriptDuration,heapBytes:last.JSHeapUsedSize,healthRoundTripMedianMs:latencies[60],healthRoundTripP95Ms:latencies[114]};
  assert.deepEqual(errors,[]);await context.close();
  await writeFile(resolve(output,`${tag}-browser.json`),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
}finally{await browser.close();await new Promise(done=>server.close(done));}
