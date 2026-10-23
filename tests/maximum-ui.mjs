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
const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[],writes=[];
page.on('pageerror',e=>errors.push(e.message));
await page.route('**/api/**',async route=>{
  const p=new URL(route.request().url()).pathname;
  if(route.request().method()!=='GET')writes.push(p);
  let data={};
  if(p==='/api/health')data={app:'motor-test-bench-hardware',token:'offline',channels:{},safety:{}};
  if(p==='/api/coupled-current/status')data={run:{running:false,motorMode:'full_current'},motor:{},brake:{},capabilities:{fullIqRamp:true,maximumOutput:maximumSupported}};
  if(p.includes('recordings')||p.includes('specialized'))data={current:null,records:[]};
  if(p==='/api/ports')data={ports:[]};
  await route.fulfill({json:data});
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
  for(const width of [1440,768,390]) {
    await page.setViewportSize({width,height:1000});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow at ${width}`);
  }
  maximumSupported=false;
  await page.getByText('通信服务尚未加载最大输出模式，请重启服务。',{exact:true}).waitFor();
  assert.ok(await page.getByRole('button',{name:'① 启动最大输出',exact:true}).isDisabled());
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
  console.log('PASS maximum-output UI: defaults, preserved legacy selection, optional Id/current inputs, narrow layout; all APIs mocked, no control writes');
}finally{await browser.close();await new Promise(r=>server.close(r));}
