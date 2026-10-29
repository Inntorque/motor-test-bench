// Ephemeral static UI with mocked APIs and a nominal 1 kHz frame source. No hardware access.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {createServer} from 'node:http';
import {readFile,writeFile} from 'node:fs/promises';
import {resolve,extname} from 'node:path';
const baseline=!!process.env.UI_BASELINE_DIR,root=resolve(process.env.UI_BASELINE_DIR||'dist');
const server=createServer(async(req,res)=>{try{const p=resolve(root,req.url==='/'?'index.html':req.url.slice(1));if(!p.startsWith(root+'/'))throw Error();res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css'})[extname(p)]||'text/plain');res.end(await readFile(p));}catch{res.statusCode=404;res.end();}});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
const page=await browser.newPage({viewport:{width:1366,height:900}}),errors=[],writes=[];
page.on('pageerror',error=>errors.push(error.message));let online=false;
const config={protocol:'hex6',torqueDecimals:2,speedDecimals:1};
const subjects={motors:[],driverBoards:[],testSetups:[],selected:{motorId:'',driverBoardId:'',testSetupId:''}};
const sensor=()=>({connected:online,config,snapshot:{receivedAt:Date.now()/1000,torque:2,speed:3,frameRateHz:1000,framesReceived:1000}});
const motor=()=>({connected:online,run:{running:false},snapshot:{receivedAt:Date.now()/1000,node:1,version:'mock',limitA:20,motor:{output_on:false,state:0},ntc:{valid:true,temp_c:25,age_s:0},feedback:{iq_a:0,id_a:0,speed_rpm:29.34,position_deg:0,stale:false,position_stale:false}}});
const health=()=>({app:'motor-test-bench-hardware',token:'mock',channels:{'torque-sensor':sensor(),'current-motor':motor()},safety:{}});
const coupled=()=>({run:{running:false},motor:motor(),brake:{device:{connected:false},run:{}},capabilities:{maximumOutput:true,fullIqRamp:true}});
await page.route('**/api/**',async route=>{
 const request=route.request(),path=new URL(request.url()).pathname.replace('/api/v1/','/api/');let data={};
 if(request.method()==='POST')writes.push(path);
 if(path==='/api/health')data=health();
 if(path==='/api/test-subjects')data=subjects;
 if(path==='/api/ports')data={ports:[]};
 if(path.includes('/recordings/status')||path==='/api/automatic/status'||path==='/api/specialized/status')data={current:null,records:[]};
 if(path==='/api/coupled-current/status')data=coupled();
 if(path==='/api/sensors/torque-sensor/frames')data={...sensor(),frames:[],reset:false,dropped:0};
 if(path==='/api/sensors/torque-sensor/read'||path==='/api/sensors/torque-sensor/status')data=sensor();
 if(path==='/api/state')data={control:{epoch:0},devices:{'torque-sensor':sensor(),'current-motor':motor()},routes:{'/api/health':health(),'/api/motor-current/status':motor(),'/api/brake-current/status':{device:{connected:false},run:{}},'/api/coupled-current/status':coupled(),'/api/recordings/status':{current:null,records:[]},'/api/test-subjects':subjects}};
 await route.fulfill({json:data});
});
await page.addInitScript(()=>{
 window.__rafRequests=0;window.__emittedFrames=0;window.__feed=false;
 const raf=window.requestAnimationFrame.bind(window);window.requestAnimationFrame=callback=>{window.__rafRequests++;return raf(callback);};
 window.EventSource=class{
  constructor(){this.listeners={};this.sequence=0;this.stateTimer=setInterval(async()=>{const d=await(await fetch('/api/v1/state')).json();for(const cb of this.listeners.state||[])cb({data:JSON.stringify({data:d})});},200);
   this.frameTimer=setInterval(()=>{if(!window.__feed)return;const now=Date.now()/1000;const frames=Array.from({length:20},(_,i)=>[++this.sequence,now-(19-i)/1000,200,30]);window.__emittedFrames+=frames.length;for(const cb of this.listeners.sensor_frames||[])cb({data:JSON.stringify({data:{config:{protocol:'hex6',torqueDecimals:2,speedDecimals:1},frames}})});},20);}
  addEventListener(name,cb){(this.listeners[name]??=[]).push(cb);}
  close(){clearInterval(this.stateTimer);clearInterval(this.frameTimer);}
 };
});
const delay=ms=>new Promise(done=>setTimeout(done,ms));
try{
 await page.goto(`http://127.0.0.1:${server.address().port}/`);
 await page.getByRole('heading',{name:'扭矩工作台',exact:true}).waitFor();await delay(600);
 const buttons=await page.locator('button').allTextContents(),headings=await page.locator('h2').allTextContents();
 const idleStart=await page.evaluate(()=>window.__rafRequests);await delay(1200);const idleRaf=await page.evaluate(start=>window.__rafRequests-start,idleStart);
 if(!baseline)assert.ok(idleRaf<=3,`idle RAF requests: ${idleRaf}`);
 const positions=await page.evaluate(()=>{const a=document.querySelector('.recording-panel').getBoundingClientRect(),b=document.querySelector('.encoder-compare').getBoundingClientRect();return {recordBottom:a.bottom,encoderTop:b.top,gap:b.top-a.bottom,encoderInMeasurement:!!document.querySelector('.measurement-area > .encoder-compare')};});
 if(!baseline){assert.equal(positions.encoderInMeasurement,true);assert.ok(positions.gap>=0&&positions.gap<=24);}
 online=true;await page.evaluate(()=>window.__feed=true);await delay(1800);
 await page.locator('.plot-wrap').evaluate(node=>node.scrollIntoView({block:'start'}));await delay(400);
 const cdp=await page.context().newCDPSession(page);await cdp.send('Performance.enable');
 const get=async()=>Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(m=>[m.name,m.value]));
 const a=await get();const framesStart=await page.evaluate(()=>window.__emittedFrames);await delay(3000);const b=await get();
 const frameCount=await page.evaluate(start=>window.__emittedFrames-start,framesStart);
 assert.ok(frameCount>1800,`replay frame rate too low: ${frameCount/3}`);
 const path=page.locator('.plot-wrap svg > path');assert.ok((await path.getAttribute('d')).includes('M'));
 if(!baseline){
  await page.evaluate(()=>{const spacer=document.createElement('div');spacer.id='replay-spacer';spacer.style.height='1000px';document.body.append(spacer);});
  await page.locator('.encoder-chart').evaluate(node=>node.scrollIntoView({block:'start'}));await delay(300);
  assert.ok(await page.locator('.plot-wrap').evaluate(node=>node.getBoundingClientRect().bottom < -100),'main plot is outside visibility margin');
  const oldPath=await path.getAttribute('d'),n=await page.evaluate(()=>window.__emittedFrames);await delay(1100);
  assert.ok(await path.getAttribute('d')===oldPath,'offscreen main plot stays cached');
  assert.ok(await page.evaluate(start=>window.__emittedFrames-start,n)>500,'frame reception continues offscreen');
  await page.locator('.plot-wrap').evaluate(node=>node.scrollIntoView({block:'start'}));await delay(200);
  assert.ok(await path.getAttribute('d')!==oldPath,'visible plot catches up');
  await page.evaluate(()=>document.querySelector('#replay-spacer').remove());
 }
 await page.locator('.encoder-chart').evaluate(node=>node.scrollIntoView({block:'start'}));await delay(300);
 const encoderPaths=await page.locator('.encoder-chart g[clip-path] path').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('d')));
 assert.ok(encoderPaths.every(path=>path?.includes('M')));
 const rate=await page.getByRole('region',{name:'动态扭矩传感器连接',exact:true}).textContent();
 const match=rate.match(/网页与曲线同步显示\s*(\d+)\s*次/);assert.ok(match&&Number(match[1])>=15&&Number(match[1])<=35,`display rate: ${match?.[1]}`);
 for(const width of [1920,1366,1024,768,390]){await page.setViewportSize({width,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow ${width}`);}
 const result={baseline,idleRafRequestsIn1_2Seconds:idleRaf,positions,buttons:buttons.map(s=>s.trim()).sort(),headings:headings.map(s=>s.trim()).sort(),encoderPaths,replayFramesIn3Seconds:frameCount,displayHz:Number(match[1]),rendererTaskSecondsIn3Seconds:b.TaskDuration-a.TaskDuration,rendererScriptSecondsIn3Seconds:b.ScriptDuration-a.ScriptDuration,heapUsedBytes:b.JSHeapUsedSize};
 if(!baseline){const before=JSON.parse(await readFile('.runtime/layout-performance-ui-before.json','utf8'));const normalize=values=>values.map(s=>s.replace(/\d{2}:\d{2}:\d{2}/g,'TIME'));assert.deepEqual(normalize(result.buttons),normalize(before.buttons));assert.deepEqual(result.headings,before.headings);assert.deepEqual(encoderPaths,before.encoderPaths);}
 assert.deepEqual(errors,[]);assert.equal(writes.filter(p=>p.includes('/supplies/')||/\/(motor|motor-current|coupled-current|brake-current)\//.test(p)).length,0);
 await page.setViewportSize({width:1366,height:900});await page.locator('.recording-panel').evaluate(node=>node.scrollIntoView({block:'start'}));await delay(200);
 await page.screenshot({path:`.runtime/layout-performance-${baseline?'before':'after'}.png`});
 await writeFile(`.runtime/layout-performance-ui-${baseline?'before':'after'}.json`,JSON.stringify(result,null,2)+'\n');
 console.log(JSON.stringify(result));console.log(`PASS ${baseline?'baseline':'optimized'} layout/performance UI: controls preserved, curves retained, 1 kHz replay, responsive layout; all APIs mocked`);
}finally{await browser.close();await new Promise(done=>server.close(done));}
