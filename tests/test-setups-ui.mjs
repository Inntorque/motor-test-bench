// All APIs are mocked on an ephemeral static server; never contacts hardware.
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
page.on('pageerror',error=>errors.push(error.message));
const catalog={motors:[{id:'motor1',name:'同一电机 A'}],driverBoards:[{id:'board1',name:'驱动板 A'}],testSetups:[{id:'bare1',motorId:'motor1',name:'裸机',hasGearbox:false,axis:'motor',ratio:1,revision:1,massKg:.5,volumeL:null}],selected:{motorId:'motor1',driverBoardId:'board1',testSetupId:'bare1'}};
let passive=null,streamLock=null,catalogGate=null,releaseCatalog;
function subject(){return {motor:catalog.motors.find(row=>row.id===catalog.selected.motorId),driverBoard:catalog.driverBoards.find(row=>row.id===catalog.selected.driverBoardId),testSetup:catalog.testSetups.find(row=>row.id===catalog.selected.testSetupId)};}
function motor(){return {connected:true,run:{running:false},testSubject:subject(),snapshot:{receivedAt:Date.now()/1000,version:'mock',limitA:10,motor:{state:0,output_on:false},feedback:{position_deg:100,speed_rpm:200,iq_a:0,id_a:0,stale:false,position_stale:false},motorProfile:{nameplate:{reduction_ratio:9.67}}}};}
await page.route('**/api/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname.replace('/api/v1/','/api/');let data={};
  const locked=!!passive;
  if(request.method()==='POST')writes.push({path,body:request.postDataJSON()});
  if(path==='/api/test-subjects'){await catalogGate;data={...catalog,configurationLocked:locked};}
  if(path==='/api/test-subjects/create'){
    const {kind,name}=request.postDataJSON(),list=kind==='motor'?'motors':'driverBoards',item={id:`${kind}${catalog[list].length+1}`,name};catalog[list].push(item);data={catalog,item};
  }
  if(path==='/api/test-subjects/save-setup'){
    const value=request.postDataJSON(),old=catalog.testSetups.find(row=>row.id===value.id),item={...value,id:value.id||`setup${catalog.testSetups.length+1}`,axis:value.hasGearbox?'output':'motor',revision:old?old.revision+1:1,ratioConfirmed:true};
    if(old)catalog.testSetups[catalog.testSetups.indexOf(old)]=item;else catalog.testSetups.push(item);data={catalog,item};
  }
  if(path==='/api/test-subjects/select'){catalog.selected=request.postDataJSON();data=catalog;}
  if(path==='/api/ports')data={ports:[]};
  if(path==='/api/health')data={app:'motor-test-bench-hardware',token:'mock',channels:{'current-motor':motor()},safety:{}};
  if(path==='/api/automatic/status')data={current:null,records:[]};
  if(path==='/api/specialized/status')data={current:passive,records:[],availability:{}};
  if(path==='/api/coupled-current/status')data={run:{running:false},motor:motor(),brake:{},capabilities:{maximumOutput:true}};
  if(path==='/api/specialized/start'){passive={id:'capture1',category:'performance',specializedSchema:2,name:'mock',running:true,state:'running',testSubject:subject(),config:{ratio:subject().testSetup.ratio}};data={current:passive};}
  if(path==='/api/specialized/stop'){passive=null;data={current:null};}
  if(path.includes('/recordings/status'))data={current:null,records:[]};
  if(path==='/api/state')data={control:{epoch:0},routes:{'/api/health':{app:'motor-test-bench-hardware',token:'mock',channels:{'current-motor':motor()},safety:{}},'/api/motor-current/status':motor(),'/api/brake-current/status':{device:{connected:false},run:{}},'/api/coupled-current/status':{run:{running:false},motor:motor(),brake:{},capabilities:{maximumOutput:true}},'/api/test-subjects':{...catalog,configurationLocked:streamLock??locked}}};
  await route.fulfill({json:data});
});
await page.addInitScript(()=>{window.EventSource=class{constructor(){this.listeners={};this.timer=setInterval(async()=>{const response=await fetch('/api/v1/state');const data=await response.json();for(const cb of this.listeners.state||[])cb({data:JSON.stringify({data})});},100);}addEventListener(name,cb){(this.listeners[name]??=[]).push(cb);}close(){clearInterval(this.timer);}};});
const picker=page.getByLabel('选择电机和驱动板',{exact:true}),dialog=page.getByRole('dialog',{name:'电机和驱动板列表'});
async function output(value){await page.waitForFunction(expected=>Array.from(document.querySelectorAll('.motor-current-readings > div')).find(node=>node.textContent.includes('被测轴折算角度'))?.querySelector('strong')?.textContent===expected+'°',value);}
try{
  await page.goto(`http://127.0.0.1:${server.address().port}/`);await output('100.000');
  await picker.click();await dialog.getByRole('button',{name:'新建配置',exact:true}).click();
  await dialog.getByLabel('测试配置名称').fill('带箱版本');await dialog.getByLabel('机械形式').selectOption('gearbox');
  await dialog.getByLabel('减速箱名称',{exact:true}).fill('减速箱 A');await dialog.getByLabel('测试配置减速比').fill('9.67');await dialog.getByLabel('配置质量 kg').fill('1.2');
  await dialog.getByRole('button',{name:'保存配置',exact:true}).click();await dialog.getByRole('button',{name:'应用选择',exact:true}).click();await output('10.341');
  assert.match(await picker.textContent(),/带箱版本.*9\.67:1/);
  await picker.click();await dialog.getByRole('button',{name:'编辑配置',exact:true}).click();await dialog.getByLabel('测试配置减速比').fill('100');
  await dialog.getByRole('button',{name:'保存配置',exact:true}).click();await dialog.getByRole('button',{name:'应用选择',exact:true}).click();await output('1.000');
  await page.getByRole('button',{name:'专项测试',exact:true}).click();const suite=page.getByRole('region',{name:'专项测试',exact:true});
  await suite.getByLabel('当前测试配置').filter({hasText:'100:1'}).waitFor();
  assert.equal(await suite.getByLabel('本次转子/输出减速比',{exact:true}).count(),0);
  // Hold stream snapshots stale while the backend lock changes.
  streamLock=false;
  await suite.getByRole('button',{name:'开始专项测试',exact:true}).click();
  assert.equal(writes.find(row=>row.path==='/api/specialized/start').body.testSetupRevision,2);
  assert.equal(writes.find(row=>row.path==='/api/specialized/start').body.ratio,undefined);
  catalogGate=new Promise(resolve=>{releaseCatalog=resolve;});
  await picker.click();await page.waitForFunction(()=>document.querySelector('[aria-label="电机测试配置"]').disabled,null,{timeout:5000});
  assert.equal(await dialog.getByRole('button',{name:'应用选择',exact:true}).isDisabled(),true);
  catalogGate=null;releaseCatalog();
  await dialog.getByText('测试或记录进行中，本次配置已冻结，结束后可切换。',{exact:true}).waitFor({state:'attached',timeout:5000});
  assert.equal(await dialog.getByLabel('电机测试配置').isDisabled(),true);
  assert.equal(await dialog.getByRole('button',{name:'应用选择',exact:true}).isDisabled(),true);
  streamLock=true;
  await page.keyboard.press('Escape');await suite.getByRole('button',{name:'仅结束专项采集',exact:true}).click();
  await picker.click();await page.waitForFunction(()=>!document.querySelector('[aria-label="测试电机"]').disabled);
  await dialog.getByLabel('新建电机名称').fill('电机 B');await dialog.getByRole('button',{name:'新建电机',exact:true}).click();
  streamLock=null;
  await page.waitForFunction(()=>document.querySelector('[aria-label="测试电机"]').value==='motor2');
  assert.equal(await dialog.getByLabel('电机测试配置').inputValue(),'');
  await dialog.getByRole('button',{name:'应用选择',exact:true}).click();await page.getByRole('button',{name:'扭矩工作台',exact:true}).click();
  await page.waitForFunction(()=>Array.from(document.querySelectorAll('.motor-current-readings > div')).find(node=>node.textContent.includes('被测轴折算角度'))?.querySelector('strong')?.textContent==='—°');
  await picker.click();await dialog.getByRole('button',{name:'新建配置',exact:true}).click();await dialog.getByLabel('测试配置名称').fill('裸机版本');
  assert.equal(await dialog.getByLabel('测试配置减速比').inputValue(),'1');assert.equal(await dialog.getByLabel('测试配置减速比').isDisabled(),true);
  await dialog.getByRole('button',{name:'保存配置',exact:true}).click();await dialog.getByRole('button',{name:'应用选择',exact:true}).click();await output('100.000');
  for(const width of [1920,1366,1024,768,390]){
    await page.setViewportSize({width,height:900});await page.evaluate(()=>scrollTo(0,0));await picker.click();
    await dialog.getByRole('button',{name:'编辑配置',exact:true}).click();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow ${width}`);
    const bounds=await dialog.boundingBox();assert.ok(bounds.y>=0&&bounds.y+bounds.height<=902,`popover below viewport ${width}: ${JSON.stringify(bounds)}`);
    await page.keyboard.press('Escape');
  }
  await page.setViewportSize({width:1366,height:900});await picker.click();await dialog.getByRole('button',{name:'编辑配置',exact:true}).click();await page.screenshot({path:'.runtime/test-setups-ui.png'});
  assert.deepEqual(errors,[]);
  assert.equal(writes.filter(row=>/\/(motor|motor-current|coupled-current|brake-current)\//.test(row.path)||row.path.includes('/supplies/')).length,0);
  console.log('PASS setup UI: create/edit/select, bare/9.67/100 conversion, shared specialized context, delayed catalog read with stale SSE lock/unlock, new motor without inherited ratio, responsive popup; all APIs mocked');
}finally{releaseCatalog?.();await browser.close();await new Promise(done=>server.close(done));}
