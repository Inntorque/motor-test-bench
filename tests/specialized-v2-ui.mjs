// Isolated static server. Every API is mocked; no workbench or hardware access.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve,extname} from 'node:path';
const root=resolve('dist');
const server=createServer(async(req,res)=>{try{const path=resolve(root,req.url==='/'?'index.html':req.url.slice(1));if(!path.startsWith(root+'/'))throw Error();res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css'})[extname(path)]||'text/plain');res.end(await readFile(path));}catch{res.statusCode=404;res.end();}});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
const page=await browser.newPage({viewport:{width:1366,height:900}}),errors=[],writes=[];
page.on('pageerror',e=>errors.push(e.message));
const identity={motor:{name:'测试电机'},driverBoard:{name:'测试驱动板'}};
const position={id:'position1',specializedSchema:2,category:'position',name:'定位结果',startedAt:1,running:false,state:'completed',config:{},testSubject:identity,positionVisits:[1,2,3,4].map((visitId,i)=>({visitId,nominalDeg:10,targetDeg:10,meanDeg:10.001,direction:i<2?'forward':'reverse'})),positionMetrics:{feedbackMaxAbsMeanErrorDeg:.001,feedbackWorstRepeatRangeDeg:.0001,externalCoverageComplete:false,feedback:[{targetDeg:10,direction:'forward',mean:.001,count:2}]}};
const performance={id:'perf1',specializedSchema:2,category:'performance',name:'性能结果',startedAt:2,running:false,state:'completed',testSubject:identity,config:{},performance:{highestObservedRpm:100,rawTorquePeak:{torqueNm:20},windowTorquePeak:{torque:18},maxObservedPowerPoint:{powerW:80},densities:{peakTorquePerKg:9}},preview:[{n:100,torque:2,iq:1,valid:true},{n:60,torque:10,iq:3,valid:true}]};
const temperature={id:'temp1',specializedSchema:2,category:'thermal',name:'温升结果',startedAt:3,running:false,state:'completed',testSubject:identity,config:{ambientC:20},thermal:{count:3,initialC:25,lastC:30,maxC:30,riseK:10,changeFromInitialK:5,stable:false},thermalPreview:[{t:0,temperatureC:25},{t:100,temperatureC:30}]};
let passive=null,coupled={running:false};
await page.route('**/api/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname.replace('/api/v1/','/api/');let data={};
  if(request.method()==='POST')writes.push({path,body:request.postDataJSON()});
  if(path==='/api/test-subjects')data={motors:[{id:'motor1',name:'测试电机'}],driverBoards:[],testSetups:[{id:'setup1',motorId:'motor1',name:'带箱测试',ratio:9.67,axis:'output',revision:1}],selected:{motorId:'motor1',driverBoardId:'',testSetupId:'setup1'}};
  if(path==='/api/ports')data={ports:[]};
  if(path==='/api/health')data={app:'motor-test-bench-hardware',token:'offline',channels:{},safety:{}};
  if(path==='/api/automatic/status')data={current:null,records:[position,performance,temperature]};
  if(path==='/api/specialized/status')data={current:passive,records:passive?[passive]:[],availability:{}};
  if(path==='/api/coupled-current/status')data={run:coupled,capabilities:{maximumOutput:true}};
  if(path.includes('/files/'))data=path.includes('/position1/')?position:path.includes('/perf1/')?performance:temperature;
  if(path==='/api/automatic/start')data={current:position};
  if(path==='/api/specialized/start'){passive={...performance,id:'capture1',running:true,state:'running',phase:'采集'};data={current:passive};}
  if(path==='/api/coupled-current/start'){coupled={id:'coupled1',phase:'awaiting_load',running:true};data={run:coupled};}
  if(path==='/api/coupled-current/load'){coupled={...coupled,phase:'loading'};data={run:coupled};}
  if(path==='/api/coupled-current/stop'){coupled={running:false};passive=null;data={run:coupled};}
  if(path==='/api/specialized/stop'){passive=null;data={current:{}};}
  if(path.includes('/recordings/status'))data={current:null,records:[]};
  if(path==='/api/state')data={control:{epoch:0},routes:{'/api/health':{token:'offline',channels:{},safety:{}},'/api/motor-current/status':{connected:false,run:{}},'/api/brake-current/status':{device:{connected:false},run:{}},'/api/coupled-current/status':{run:{running:false},motor:{},brake:{},capabilities:{maximumOutput:true}}}};
  await route.fulfill({json:data});
});
await page.addInitScript(()=>{window.EventSource=class{constructor(){this.listeners={};this.timer=setInterval(async()=>{const response=await fetch('/api/v1/state');const data=await response.json();for(const cb of this.listeners.state||[])cb({data:JSON.stringify({data})});},100);}addEventListener(n,cb){(this.listeners[n]??=[]).push(cb);}close(){clearInterval(this.timer);}};});
try{
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.getByRole('button',{name:'专项测试',exact:true}).click();
  const suite=page.getByRole('region',{name:'专项测试',exact:true});
  await suite.getByRole('button',{name:'性能测试',exact:true}).waitFor();
  await suite.getByRole('img',{name:'T-N 实测窗口',exact:true}).waitFor();
  await suite.getByRole('button',{name:'定位精度',exact:true}).click();
  await suite.getByText('外部参考尚未完整覆盖：当前只显示驱动反馈或部分参考统计，不能作为完整绝对精度验收。').waitFor();
  assert.equal(await suite.getByRole('button',{name:'开始专项测试',exact:true}).isDisabled(),true);
  const row=suite.locator('tr').filter({hasText:'定位结果'});await row.getByRole('button',{name:'查看完整结果'}).click();
  await suite.getByLabel('参考仪器',{exact:true}).fill('独立角度编码器');
  await suite.getByLabel('外部参考零位 °',{exact:true}).fill('20');
  await suite.getByLabel('参考坐标和零位已经对齐，角度单位为 °').check();
  await suite.getByLabel('导入参考角度 CSV').setInputFiles({name:'reference.csv',mimeType:'text/csv',buffer:Buffer.from('visit_id,reference_deg\n1,30.1\n2,30.12\n3,29.9\n4,29.92')});
  await suite.getByRole('button',{name:'导出外部参考 CSV',exact:true}).waitFor();
  assert.equal(writes.filter(row=>row.path==='/api/specialized/start').length,0,'offline angle analysis sends no control request');
  await page.reload();await page.getByRole('button',{name:'专项测试',exact:true}).click();
  await suite.locator('tr').filter({hasText:'定位结果'}).getByRole('button',{name:'查看完整结果'}).click();
  await suite.getByRole('button',{name:'导出外部参考 CSV',exact:true}).waitFor();
  await suite.getByRole('button',{name:'温升曲线',exact:true}).click();
  await suite.getByRole('img',{name:'温度—时间曲线（含降温段）',exact:true}).waitFor();
  assert.equal(await suite.getByLabel('采集/运行时长 s').getAttribute('max'),null,'temperature duration does not inherit 240 s / one-hour UI cap');
  await suite.getByRole('button',{name:'性能测试',exact:true}).click();
  await suite.getByLabel('性能配方').selectOption('envelope');
  await suite.getByLabel('主动测试：已确认电源、负载、散热及本次目标和换向路径允许运动').check();
  await suite.getByRole('button',{name:'① 最大输出空载启动',exact:true}).click();
  await suite.getByRole('button',{name:'② 开始阶梯加载',exact:true}).click();
  assert.equal(writes.filter(row=>row.path==='/api/coupled-current/start').length,1);
  assert.equal(writes.find(row=>row.path==='/api/coupled-current/start').body.motorMode,'maximum_output');
  await suite.getByRole('button',{name:'停止加载并停机',exact:true}).click();
  for(const width of [1920,1366,1024,768,390]){await page.setViewportSize({width,height:900});await page.evaluate(()=>scrollTo(0,0));assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow ${width}`);}
  await page.setViewportSize({width:1366,height:900});await page.screenshot({path:'.runtime/specialized-v2-ui.png'});
  assert.deepEqual(errors,[]);console.log('PASS specialized V2: three modules, offline reference persistence, maximum-output two-stage API reuse, responsive layout; all APIs mocked');
}finally{await browser.close();await new Promise(done=>server.close(done));}
