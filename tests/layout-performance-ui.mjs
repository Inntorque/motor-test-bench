// Ephemeral static UI with mocked APIs and a nominal 1 kHz frame source. No hardware access.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {createServer} from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,extname,sep} from 'node:path';
await mkdir('.runtime',{recursive:true});
const desktop=process.argv.includes('--desktop') || process.env.UI_DESKTOP==='1';
const baseline=!!process.env.UI_BASELINE_DIR,root=resolve(process.env.UI_BASELINE_DIR||'dist');
const server=createServer(async(req,res)=>{try{const p=resolve(root,req.url==='/'?'index.html':req.url.slice(1));if(!p.startsWith(root+sep))throw Error();res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css'})[extname(p)]||'text/plain');res.end(await readFile(p));}catch{res.statusCode=404;res.end();}});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
const page=await browser.newPage({viewport:{width:1366,height:900}}),errors=[],writes=[];
page.on('pageerror',error=>errors.push(error.message));let online=false,driverOnline=true,supplyOutputOn=true;
const config={protocol:'hex6',torqueDecimals:2,speedDecimals:1};
const subjects={motors:[],driverBoards:[],testSetups:[],selected:{motorId:'',driverBoardId:'',testSetupId:''}};
let loadRun=null;
const sensor=()=>({connected:online,config,snapshot:{receivedAt:Date.now()/1000,torque:2,speed:3,frameRateHz:1000,framesReceived:1000}});
const motor=()=>({connected:online&&driverOnline,error:driverOnline?'':'等待驱动板上电回传',run:{running:false},snapshot:{receivedAt:Date.now()/1000,node:1,version:'mock',limitA:20,capabilities:{maximumOutput:true,nativeControl:true,currentLoop:true,dyno:true,currentLoopMinA:-5.37,currentLoopMaxA:5.37},startBlockReason:loadRun?.running?'drive not ready':'',motor:{output_on:!!loadRun?.running,state:loadRun?.running?2:0},ntc:{valid:true,temp_c:25,age_s:0},feedback:{iq_a:0,id_a:0,speed_rpm:29.34,position_deg:0,stale:false,position_stale:false}}});
const health=()=>({app:'motor-test-bench-hardware',token:'mock',channels:{'torque-sensor':sensor(),'current-motor':motor(),...supplies()},safety:{}});
const supply=()=>({connected:online,config:{voltageDecimals:2,currentDecimals:3},snapshot:{receivedAt:Date.now()/1000,status:supplyOutputOn?128:0,settings:[1,2400,0],measured:[2400,0,0]}});
const supplies=()=>loadRun?{'motor-supply':supply(),'brake-supply':supply()}:{};
const coupled=()=>({run:loadRun||{running:false},motor:motor(),brake:{device:loadRun?supply():{connected:false},run:{}},capabilities:{maximumOutput:true,fullIqRamp:true}});
await page.route('**/api/**',async route=>{
 const request=route.request(),path=new URL(request.url()).pathname.replace('/api/v1/','/api/');let data={};
 if(request.method()==='POST')writes.push(path);
 if(path==='/api/desktop/autosave')data={running:true};
 if(path==='/api/health')data=health();
 if(path==='/api/test-subjects')data=subjects;
 if(path==='/api/ports')data={ports:[]};
 if(path.includes('/recordings/status')||path==='/api/automatic/status'||path==='/api/specialized/status')data={current:null,records:[]};
 if(path==='/api/coupled-current/status')data=coupled();
 if(path==='/api/coupled-current/load'){
  const {id,clickedAt}=request.postDataJSON();
  assert.equal(id,loadRun.id);
  loadRun={...loadRun,phase:'loading',loadRequested:true,loadStartedAt:clickedAt,characteristicSchema:2,characteristicPoints:[],targetNm:0};
  data=coupled();
 }
 if(/^\/api\/supplies\/[^/]+\/read$/.test(path))data=supply();
 if(path==='/api/sensors/torque-sensor/frames')data={...sensor(),frames:[],reset:false,dropped:0};
 if(path==='/api/sensors/torque-sensor/read'||path==='/api/sensors/torque-sensor/status')data=sensor();
 if(path==='/api/supplies/motor-supply/output'){await route.fulfill({status:400,json:{error:'电源输出状态读回与指令不一致'}});return;}
 if(path==='/api/state')data={control:{epoch:0},devices:{'torque-sensor':sensor(),'current-motor':motor(),...supplies()},routes:{'/api/connections/status':{running:false,report:['电机驱动板：等待驱动板上电回传']},'/api/health':health(),'/api/motor-current/status':motor(),'/api/brake-current/status':{device:{connected:false},run:{}},'/api/coupled-current/status':coupled(),'/api/recordings/status':{current:null,records:[]},'/api/test-subjects':subjects}};
 await route.fulfill({json:data});
});
await page.addInitScript(desktop=>{
 if(desktop){localStorage.setItem('motor-bench:characteristic:limits',JSON.stringify({torque:'5',power:'50'}));window.inntorqueDesktop={activity(){},dataDirectory:async()=>"/mock/测功数据",openDataFolder:async()=>{}};document.addEventListener('DOMContentLoaded',()=>document.documentElement.classList.add('desktop-app'));}
 window.__rafRequests=0;window.__emittedFrames=0;window.__feed=false;
 const raf=window.requestAnimationFrame.bind(window);window.requestAnimationFrame=callback=>{window.__rafRequests++;return raf(callback);};
 window.EventSource=class{
  constructor(){this.listeners={};this.sequence=0;this.stateTimer=setInterval(async()=>{const d=await(await fetch('/api/v1/state')).json();for(const cb of this.listeners.state||[])cb({data:JSON.stringify({data:d})});},200);
   this.frameTimer=setInterval(()=>{if(!window.__feed)return;const now=Date.now()/1000;const frames=Array.from({length:20},(_,i)=>[++this.sequence,now-(19-i)/1000,200,30]);window.__emittedFrames+=frames.length;for(const cb of this.listeners.sensor_frames||[])cb({data:JSON.stringify({data:{config:{protocol:'hex6',torqueDecimals:2,speedDecimals:1},frames}})});},20);}
  addEventListener(name,cb){(this.listeners[name]??=[]).push(cb);}
  close(){clearInterval(this.stateTimer);clearInterval(this.frameTimer);}
 };
},desktop);
const delay=ms=>new Promise(done=>setTimeout(done,ms));
try{
 await page.goto(`http://127.0.0.1:${server.address().port}/`);
 await page.getByRole('navigation',{name:'主导航',exact:true}).waitFor();await delay(600);
 const buttons=await page.locator('button').allTextContents(),headings=await page.locator('h2').allTextContents();
 const idleStart=await page.evaluate(()=>window.__rafRequests);await delay(1200);const idleRaf=await page.evaluate(start=>window.__rafRequests-start,idleStart);
 if(!baseline)assert.ok(idleRaf<=3,`idle RAF requests: ${idleRaf}`);
 const positions=await page.evaluate(()=>{const a=document.querySelector('.recording-panel, .auto-save-status').getBoundingClientRect(),b=document.querySelector('.encoder-compare').getBoundingClientRect();return {recordBottom:a.bottom,encoderTop:b.top,gap:b.top-a.bottom,encoderInMeasurement:!!document.querySelector('.measurement-area .encoder-compare')};});
 if(!baseline&&!desktop){assert.equal(positions.encoderInMeasurement,true);assert.ok(positions.gap>=0&&positions.gap<=24);}
 online=true;await page.evaluate(()=>window.__feed=true);await delay(1800);
 await page.locator('.plot-wrap').evaluate(node=>node.scrollIntoView({block:'start'}));await delay(400);
 const cdp=await page.context().newCDPSession(page);await cdp.send('Performance.enable');
 const get=async()=>Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(m=>[m.name,m.value]));
 const a=await get();const framesStart=await page.evaluate(()=>window.__emittedFrames);await delay(3000);const b=await get();
 const frameCount=await page.evaluate(start=>window.__emittedFrames-start,framesStart);
 assert.ok(frameCount>1800,`replay frame rate too low: ${frameCount/3}`);
 const path=page.locator('.plot-wrap svg > path');assert.ok((await path.getAttribute('d')).includes('M'));
 if(!baseline&&!desktop){
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
 if(desktop){
  await page.getByRole('combobox',{name:'工作区视图',exact:true}).selectOption('diagnostics');await delay(300);
  const oldPath=await path.getAttribute('d'),frames=await page.evaluate(()=>window.__emittedFrames);
  await delay(1100);
  assert.equal(await path.getAttribute('d'),oldPath,'hidden chart remains cached');
  assert.ok(await page.evaluate(start=>window.__emittedFrames-start,frames)>500,'data reception continues on diagnostics');
  await page.getByRole('combobox',{name:'工作区视图',exact:true}).selectOption('chart');await delay(300);
  assert.notEqual(await path.getAttribute('d'),oldPath,'chart catches up after switching back');
  await page.getByRole('combobox',{name:'工作区视图',exact:true}).selectOption('diagnostics');
 }
 await page.locator('.encoder-chart').evaluate(node=>node.scrollIntoView({block:'start'}));await delay(300);
 const encoderPaths=await page.locator('.encoder-chart g[clip-path] path').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('d')));
 assert.ok(encoderPaths.every(path=>path?.includes('M')));
 if(desktop)await page.getByRole('button',{name:/设备设置 ·/}).click();
 const rate=await page.getByRole('region',{name:'动态扭矩传感器连接',exact:true}).textContent();
 const match=rate.match(/网页与曲线同步显示\s*(\d+)\s*次/);assert.ok(match&&Number(match[1])>=15&&Number(match[1])<=35,`display rate: ${match?.[1]}`);
 if(desktop)await page.keyboard.press('Escape');
 for(const width of (desktop?[1920,1366,1024,800,720]:[1920,1366,1024,768,390])){await page.setViewportSize({width,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow ${width}`);}
 if(desktop){
  await page.setViewportSize({width:1366,height:900});
  await page.getByRole('combobox',{name:'工作区视图',exact:true}).selectOption('chart');
  await page.getByRole('button',{name:'查看扭矩特性曲线',exact:true}).click();
  const curves=page.locator('.motor-performance-chart [data-series] path');
  const dots=page.locator('.motor-performance-chart [data-series] circle[data-stage]');
  assert.equal(await dots.count(),0);
  assert.equal(await page.getByLabel('每级保持时间 s',{exact:true}).count(),0);
  assert.ok((await curves.evaluateAll(nodes=>nodes.map(n=>n.getAttribute('d')))).every(d=>!d),'idle real-time samples must not become characteristic points');
  const torqueColor=await page.locator('.torque-reading .value').evaluate(el=>getComputedStyle(el).color);
  assert.notEqual(torqueColor,'rgb(255, 255, 255)','real-time torque must be readable on the desktop background');
  const load=page.getByRole('button',{name:'② 开启力矩输出',exact:true});
  assert.equal(await load.isEnabled(),false);
  loadRun={id:'load-readiness',running:true,mode:'torque_sweep',motorMode:'dyno',phase:'ramping',startedAt:Date.now()/1000};
  await delay(400);
  assert.equal(await page.getByRole('button',{name:'转速 rpm'}).getAttribute('aria-pressed'),'true','spinup shows the speed trace');
  assert.equal(await page.locator('.motor-performance-chart').count(),0);
  assert.equal(await load.isEnabled(),false,'load waits for the no-load preparation phase');
  loadRun={...loadRun,phase:'awaiting_load'};
  await page.waitForFunction(el=>!el.disabled,await load.elementHandle(),{timeout:5000});
  assert.equal(await load.isEnabled(),true,'a running drive must not be tested for readiness to start again');
  online=false;await delay(400);
  assert.equal(await load.isEnabled(),false,'device disconnection still blocks loading');
  online=true;
  await page.waitForFunction(el=>!el.disabled,await load.elementHandle(),{timeout:5000});
  assert.equal(await load.isEnabled(),true);
  await load.click();
  await page.getByText('等待首个档位完成',{exact:true}).waitFor();
  assert.equal(await curves.count(),6);
  assert.equal(await page.getByLabel('曲线时间范围').count(),0);
  const loadedAt=loadRun.loadStartedAt;
  const characteristicPoints=[0,.15,.3].map((torque,stage)=>({runId:'load-readiness',stage,kind:'stage',torque,measuredTorque:[.27,.04,.27][stage],speed:100-stage*20,power:stage,voltage:24,current:4,inputPower:96,efficiency:stage,startedAt:loadedAt+(stage+1)*2-.5,scheduledAt:loadedAt+(stage+1)*2,receivedAt:loadedAt+(stage+1)*2-stage*.02,sampleCount:20,electricalCount:20,toleranceNm:.03}));
  loadRun={...loadRun,loadRequested:true,phase:'loading',torqueConfig:{startNm:0,stepNm:.15},loadElapsedS:6,targetNm:.45,loadStartedAt:loadedAt,characteristicSchema:2,characteristicPoints:characteristicPoints.flatMap(p=>[
    {...p,kind:'sweep',measuredTorque:.8,torque:p.torque,receivedAt:p.receivedAt-.2},
    {...p,kind:'sweep',measuredTorque:.04,torque:p.torque,receivedAt:p.receivedAt-.1},p])};await delay(400);
  assert.equal(await curves.count(),6,'measured lines by default');
  assert.equal(await page.getByLabel('力矩步长 N·m',{exact:true}).inputValue(),'0.15');
  assert.deepEqual(await page.locator('.stage-progress dd').allTextContents(),['0.45 N·m','0.30 N·m','3 点']);
  const inspector=page.getByRole('slider',{name:'按输出档位读数'});
  const area=await inspector.boundingBox();
  await page.mouse.move(area.x+area.width*.15,area.y+area.height/2);
  const readout=page.locator('[data-stage-readout]');
  assert.equal(await readout.getAttribute('data-stage-readout'),'1');
  assert.equal(await readout.locator('text').count(),7);
  assert.match(await readout.textContent(),/制动设定 0.15 N·m.*母线电流 4 A/);
  await page.screenshot({path:'.runtime/characteristic-stage-readout.png'});
  await inspector.focus();await page.keyboard.press('End');
  assert.equal(await readout.getAttribute('data-stage-readout'),'2');
  await page.keyboard.press('Escape');assert.equal(await readout.count(),0);
  await page.getByRole('button',{name:'坐标与连线',exact:true}).click();
  assert.equal(await page.getByLabel('扭矩横轴上限 N·m',{exact:true}).inputValue(),'');
  assert.equal(await page.getByLabel('输出功率轴上限 W',{exact:true}).inputValue(),'');
  await page.getByLabel('特性曲线连线方式').selectOption('scatter');
  await page.keyboard.press('Escape');
  assert.equal(await curves.count(),0,'discrete mode must not connect accepted points');
  assert.equal(await dots.count(),18,'six series, one marker per accepted stage');
  const xs=await page.locator('[data-series=voltage] circle[data-stage]').evaluateAll(nodes=>nodes.map(n=>Number(n.getAttribute('cx'))));
  assert.ok(xs[1]>xs[0]&&Math.abs((xs[2]-xs[1])-(xs[1]-xs[0]))<.001,'markers advance one commanded torque step, independent of shaft feedback');
  const currentDots=await page.locator('[data-series=current] circle[data-stage]').evaluateAll(nodes=>nodes.map(n=>[Number(n.getAttribute('cx')),Number(n.getAttribute('cy'))]));
  assert.deepEqual(currentDots.map(([x])=>x),xs);
  assert.equal(new Set(currentDots.map(([,y])=>y)).size,1,'current at its limit extends horizontally across successive output settings');
  await page.getByRole('button',{name:'采样点表（3）',exact:true}).click();
  const table=page.getByRole('dialog',{name:'采样点表（3）'});
  assert.equal(await table.locator('tbody tr').count(),3);
  assert.deepEqual(await table.locator('tbody tr td:nth-child(6)').allTextContents(),['0.000','0.150','0.300']);
  assert.deepEqual(await table.locator('tbody tr td:nth-child(7)').allTextContents(),['100.000','80.000','60.000']);
  assert.deepEqual(await table.locator('tbody tr td:nth-child(2)').allTextContents(),['2.000','4.000','6.000']);
  assert.deepEqual(await table.locator('tbody tr td:nth-child(4)').allTextContents(),['0.0','-20.0','-40.0']);
  assert.deepEqual(await table.locator('tbody tr td:nth-child(5)').allTextContents(),['—','1.980','1.980']);
  assert.equal(await page.getByText('② 制动力矩阶梯 · 固定 2 s',{exact:true}).isVisible(),true);
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'坐标与连线',exact:true}).click();
  assert.equal(await page.getByLabel('特性曲线连线方式').inputValue(),'scatter');
  await page.getByLabel('特性曲线连线方式').selectOption('measured');
  await page.keyboard.press('Escape');
  assert.equal(await curves.count(),6);
  assert.ok((await curves.evaluateAll(nodes=>nodes.map(n=>n.getAttribute('d')))).every(d=>d?.includes('L')));
  assert.equal(await dots.count(),0);

  assert.equal(await load.isEnabled(),false,'loading cannot be requested twice');
  loadRun={...loadRun,stopConfirmation:{state:'confirming',durationS:4.2,confirmed:false}};await delay(400);
  assert.equal(await page.getByText(/保持制动 · 零速确认 4.2 \/ 5 s/).isVisible(),true);
  await page.getByRole('button',{name:'查看时间曲线',exact:true}).click();
  const endpoint={...characteristicPoints.at(-1),kind:'stall',measuredTorque:.26,speed:0,power:0};
  loadRun={...loadRun,running:false,phase:'stopped',measurementEndedAt:Date.now()/1000,characteristicPoints:[...characteristicPoints.slice(0,-1),endpoint],stallEndpoint:{torque:.26,speed:0,receivedAt:endpoint.receivedAt},stopConfirmation:{state:'confirmed',durationS:5,confirmed:true}};await delay(400);
  assert.equal(await page.getByRole('button',{name:'查看时间曲线',exact:true}).isVisible(),true,'stall completion switches back to the characteristic graph');
  assert.equal(await page.getByText(/堵转时制动设定 0.30 N·m/).isVisible(),true,'the endpoint uses the confirmed brake output setting');
  assert.equal(await load.isEnabled(),false,'a stopped run must be started before loading');
  const frozenPaths=await curves.evaluateAll(nodes=>nodes.map(n=>n.getAttribute('d')));
  loadRun={...loadRun,postStopZero:{receivedAt:Date.now()/1000,torque:0,speed:0},postStopTailState:'zero'};await delay(400);
  assert.deepEqual(await curves.evaluateAll(nodes=>nodes.map(n=>n.getAttribute('d'))),frozenPaths,'post-stop zero must not extend a characteristic curve');
  await page.getByRole('button',{name:'查看时间曲线',exact:true}).click();await delay(400);
  assert.equal(await page.getByRole('button',{name:'查看扭矩特性曲线',exact:true}).isVisible(),true,'completed status refresh must not override manual chart selection');
  await page.getByRole('button',{name:'查看扭矩特性曲线',exact:true}).click();

  loadRun={...loadRun,stopConfirmed:false,stopErrors:['上次退出时输出未确认关闭，请重新连接原设备并执行停止确认。'.repeat(3)]};await delay(400);
  for(const [width,height] of [[1465,832],[1366,768],[1024,600],[900,600],[800,600],[720,480]]){
    await page.setViewportSize({width,height});
    if(width<=1000&&await page.locator('.control-area').isHidden())await page.getByRole('combobox',{name:'工作区视图',exact:true}).selectOption('controls');
    const size=await page.locator('.control-area').evaluate(el=>({height:el.clientHeight,content:el.scrollHeight,panel:el.querySelector('.coupled-current-panel').clientHeight,panelContent:el.querySelector('.coupled-current-panel').scrollHeight}));
    assert.ok(size.content<=size.height+1&&size.panelContent<=size.panel+1,`fixed controls ${width}x${height}: ${JSON.stringify(size)}`);
  }
  await page.getByRole('button',{name:'状态详情',exact:true}).click();
  await page.getByRole('dialog',{name:'状态详情'}).waitFor();
  assert.match(await page.getByRole('dialog',{name:'状态详情'}).textContent(),/上次退出时输出未确认关闭/);
  await page.keyboard.press('Escape');
  await page.setViewportSize({width:1366,height:900});
  driverOnline=false;
  await page.getByRole('button',{name:'设备设置 · 3/4',exact:true}).waitFor({timeout:1200});
  await page.getByRole('button',{name:'设备设置 · 3/4',exact:true}).click();
  const devicesDialog=page.getByRole('dialog',{name:'设备设置',exact:true});
  const report=devicesDialog.locator('.connection-report');
  assert.match(await report.innerText(),/电机驱动板：等待驱动板上电回传/);
  driverOnline=true;
  await page.getByRole('button',{name:'设备设置 · 4/4',exact:true}).waitFor({state:'attached',timeout:1200});
  await report.getByText('电机驱动板：已连接',{exact:true}).waitFor({timeout:1200});
  assert.equal(await report.getByText(/等待驱动板/).count(),0,'stale scan text must not contradict current connection');
  online=false;
  await page.getByRole('button',{name:'设备设置 · 0/4',exact:true}).waitFor({state:'attached',timeout:1200});
  assert.equal(await report.getByText(/已连接/).count(),0,'all device rows track loss of connection');
  online=true;supplyOutputOn=false;
  const expandPower=devicesDialog.getByRole('button',{name:'展开电机电源控制',exact:true});
  if(await expandPower.count())await expandPower.click();
  await devicesDialog.getByRole('button',{name:'开启电机电源',exact:true}).waitFor();
  await devicesDialog.getByRole('button',{name:'开启电机电源',exact:true}).click();
  await devicesDialog.getByText('电源输出状态读回与指令不一致',{exact:true}).waitFor();
  supplyOutputOn=true;
  await devicesDialog.getByText('电源输出状态读回与指令不一致',{exact:true}).waitFor({state:'hidden',timeout:1200});
  await devicesDialog.getByRole('button',{name:'关闭电机电源',exact:true}).waitFor();
  await page.keyboard.press('Escape');

 }
 const result={desktop,baseline,idleRafRequestsIn1_2Seconds:idleRaf,positions,buttons:buttons.map(s=>s.trim()).sort(),headings:headings.map(s=>s.trim()).sort(),encoderPaths,replayFramesIn3Seconds:frameCount,displayHz:Number(match[1]),rendererTaskSecondsIn3Seconds:b.TaskDuration-a.TaskDuration,rendererScriptSecondsIn3Seconds:b.ScriptDuration-a.ScriptDuration,heapUsedBytes:b.JSHeapUsedSize};
 if(!baseline&&!desktop){const before=JSON.parse(await readFile('.runtime/layout-performance-ui-before.json','utf8'));const normalize=values=>values.map(s=>s.replace(/\d{2}:\d{2}:\d{2}/g,'TIME'));assert.deepEqual(normalize(result.buttons),normalize(before.buttons));assert.deepEqual(result.headings,before.headings);assert.deepEqual(encoderPaths,before.encoderPaths);}
 assert.deepEqual(errors,[]);assert.equal(writes.filter(p=>p.includes('/supplies/')&&!p.endsWith('/read')||/\/(motor|motor-current|coupled-current|brake-current)\//.test(p)).length,desktop?2:0);
 await page.setViewportSize({width:1366,height:900});await page.locator('.recording-panel, .auto-save-status').evaluate(node=>node.scrollIntoView({block:'start'}));await delay(200);
 await page.screenshot({path:`.runtime/layout-performance-${desktop?'desktop':baseline?'before':'after'}.png`});
 await writeFile(`.runtime/layout-performance-ui-${desktop?'desktop':baseline?'before':'after'}.json`,JSON.stringify(result,null,2)+'\n');
 console.log(JSON.stringify(result));console.log(`PASS ${baseline?'baseline':'optimized'} layout/performance UI: controls preserved, curves retained, 1 kHz replay, responsive layout; all APIs mocked`);
}finally{await browser.close();await new Promise(done=>server.close(done));}
