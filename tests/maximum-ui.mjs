// All API requests are intercepted. Never contact the hardware bridge.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve,extname} from 'node:path';
const root=resolve('dist');
const server=createServer(async(req,res)=>{
  try {
    const path=resolve(root,req.url==='/'?'index.html':req.url.slice(1));
    if(!path.startsWith(root+'/'))throw new Error('bad path');
    const types={'.html':'text/html','.js':'text/javascript','.css':'text/css'};
    res.setHeader('Content-Type',types[extname(path)]||'application/octet-stream');res.end(await readFile(path));
  }catch{res.statusCode=404;res.end();}
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
let maximumSupported=true;
const subjects={motors:[],driverBoards:[],selected:{motorId:'',driverBoardId:''}};
const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[],writes=[];
page.on('pageerror',e=>errors.push(e.message));
await page.route('**/api/**',async route=>{
  const p=new URL(route.request().url()).pathname.replace('/api/v1/','/api/');
  if(route.request().method()!=='GET')writes.push(p);
  let data={};
  if(p==='/api/test-subjects')data=subjects;
  if(p==='/api/test-subjects/create') {
    const {kind,name}=route.request().postDataJSON();const list=kind==='motor'?'motors':'driverBoards';
    const item={id:`${kind}-${subjects[list].length+1}`,name};subjects[list].push(item);data={catalog:subjects,item};
  }
  if(p==='/api/test-subjects/select') {subjects.selected=route.request().postDataJSON();data=subjects;}
  if(p==='/api/health')data={app:'motor-test-bench-hardware',token:'offline',channels:{},safety:{}};
  if(p==='/api/coupled-current/status')data={run:{running:false,motorMode:'full_current'},motor:{},brake:{},capabilities:{fullIqRamp:true,maximumOutput:maximumSupported}};
  if(p.includes('recordings')||p.includes('specialized'))data={current:null,records:[]};
  if(p==='/api/ports')data={ports:[]};
  if(p==='/api/state')data={control:{epoch:0},routes:{
    '/api/health':{token:'offline',channels:{},safety:{}},
    '/api/coupled-current/status':{run:{running:false},motor:{},brake:{},capabilities:{fullIqRamp:true,maximumOutput:maximumSupported}},
    '/api/motor-current/status':{connected:false,run:{}},'/api/brake-current/status':{device:{connected:false},run:{}}
  }};
  await route.fulfill({json:data});
});
await page.addInitScript(()=>{
  window.EventSource=class {
    constructor(){this.listeners={};this.timer=setInterval(async()=>{
      const response=await fetch('/api/v1/state');const data=await response.json();
      for(const callback of this.listeners.state||[])callback({data:JSON.stringify({data})});
    },100);}
    addEventListener(name,callback){(this.listeners[name]??=[]).push(callback);}
    close(){clearInterval(this.timer);}
  };
});
try {
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  const mode=page.getByLabel('电机输出模式',{exact:true});await mode.waitFor();
  assert.equal(await mode.inputValue(),'maximum_output');
  await page.getByRole('button',{name:'① 启动最大输出',exact:true}).waitFor();
  await mode.selectOption('full_current');
  await page.getByRole('button',{name:'① 启动 10 A 调流',exact:true}).waitFor();
  await mode.selectOption('maximum_output');
  await page.getByText('最大输出设置',{exact:true}).click();
  assert.equal(await page.getByLabel('弱磁 Id 下限 A（0=关闭）',{exact:true}).inputValue(),'0');
  assert.equal(await page.getByLabel('相电流运行上限 A（0=自动）',{exact:true}).inputValue(),'0');
  const picker=page.getByLabel('选择电机和驱动板',{exact:true});
  await picker.click();
  const subjectDialog=page.getByRole('dialog',{name:'电机和驱动板列表'});
  await subjectDialog.getByLabel('新建电机名称').fill('测试电机 A');
  await subjectDialog.getByRole('button',{name:'新建电机',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[aria-label="测试电机"]').value==='motor-1');
  await subjectDialog.getByLabel('新建驱动板名称').fill('测试驱动板 N32');
  await subjectDialog.getByRole('button',{name:'新建驱动板',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[aria-label="测试驱动板"]').value==='driverBoard-1');
  await subjectDialog.getByRole('button',{name:'应用选择',exact:true}).click();
  await subjectDialog.waitFor({state:'hidden'});
  assert.match(await picker.textContent(),/测试电机 A.*测试驱动板 N32/);
  await page.reload();
  await page.waitForFunction(()=>document.querySelector('.test-subject-picker summary').textContent.includes('测试电机 A'));
  assert.match(await picker.textContent(),/测试电机 A.*测试驱动板 N32/);
  for(const width of [2560,1920,1536,1366,1280,1120,1024,850,768,580,390,1536,1920]) {
    await page.setViewportSize({width,height:768});
    await page.evaluate(()=>window.scrollTo(0,0));
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow at ${width}`);
    const layout=await page.evaluate(()=>{
      const main=document.querySelector('main').getBoundingClientRect();
      const nav=document.querySelector('.sidebar').getBoundingClientRect();
      return {left:main.left,right:main.right,top:main.top,navRight:nav.right,navBottom:nav.bottom,viewport:document.documentElement.clientWidth};
    });
    assert.ok(Math.abs(layout.right-layout.viewport)<2,`unused right margin at ${width}`);
    if(width<=1600) {
      assert.ok(Math.abs(layout.left)<2,`unused left gutter at ${width}`);
      assert.ok(layout.top>=layout.navBottom-1,`navigation overlaps content at ${width}`);
    }else assert.ok(Math.abs(layout.left-layout.navRight)<2,`unused sidebar gutter at ${width}`);
    if(width===1366)await page.screenshot({path:'.runtime/layout-laptop-1366.png'});
    await picker.click();
    const box=await subjectDialog.boundingBox();
    assert.ok(box.x>=0 && box.x+box.width<=width,`subject picker overflow at ${width}`);
    await page.keyboard.press('Escape');
  }
  maximumSupported=false;
  await page.getByText('通信服务尚未加载最大输出模式，请重启服务。',{exact:true}).waitFor();
  assert.ok(await page.getByRole('button',{name:'① 启动最大输出',exact:true}).isDisabled());
  assert.deepEqual(errors,[]);assert.ok(writes.every(path=>path.endsWith('/control/heartbeat')||path.endsWith('/control/release')||path.startsWith('/api/test-subjects/')));
  console.log('PASS maximum-output UI: defaults, preserved legacy selection, optional Id/current inputs, narrow layout; all APIs mocked, no control writes');
}finally{await browser.close();await new Promise(r=>server.close(r));}
