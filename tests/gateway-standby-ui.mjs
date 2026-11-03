import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {resolve,extname} from 'node:path';
import {chromium} from 'playwright';

const root=resolve('site'),output=resolve('.runtime/standby-20261103');await mkdir(output,{recursive:true});
const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.png':'image/png','.svg':'image/svg+xml'};
const server=createServer(async(req,res)=>{
  const path=new URL(req.url,'http://localhost').pathname,file=resolve(root,'.'+(path==='/'?'/index.html':path));
  try {assert.ok(file.startsWith(root+'/'));res.setHeader('Content-Type',types[extname(file)]||'application/octet-stream');res.end(await readFile(file));}
  catch {res.writeHead(404).end();}
});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
const base=`http://127.0.0.1:${server.address().port}/`,browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
const probe=()=>{
  window.standbyProbe={contexts:[],frames:0,backgroundFrames:0};
  const getContext=HTMLCanvasElement.prototype.getContext,drawImage=CanvasRenderingContext2D.prototype.drawImage;
  HTMLCanvasElement.prototype.getContext=function(type,...args){
    const result=getContext.call(this,type,...args);
    if(type==='webgl2'&&this.classList.contains('cosmic-canvas')&&result&&!window.standbyProbe.contexts.includes(result)) {
      window.standbyProbe.contexts.push(result);
      const draw=result.drawArrays.bind(result);result.drawArrays=(mode,...args)=>{if(mode===result.POINTS)window.standbyProbe.frames++;return draw(mode,...args);};
    }
    return result;
  };
  CanvasRenderingContext2D.prototype.drawImage=function(...args){if(this.canvas.closest('.app-icon'))window.standbyProbe.backgroundFrames++;return drawImage.apply(this,args);};
};
const enter=page=>page.getByRole('button',{name:'进入宇宙待机模式',exact:true});
const leave=page=>page.getByRole('button',{name:'退出待机模式，返回总网关',exact:true});
const frames=page=>page.evaluate(()=>window.standbyProbe.frames);
const active=page=>page.evaluate(()=>window.standbyProbe.contexts.filter(gl=>!gl.isContextLost()).length);
const result={rootOnly:true,lazy:true,rapidClose:true,repeatOpen:true,hiddenPause:true,reducedMotion:true,resourcesReleased:true,responsive:[],errors:[]};
try {
  const context=await browser.newContext({viewport:{width:1440,height:900}});await context.addInitScript(probe);
  const page=await context.newPage(),api=[],chunks=[];
  page.on('pageerror',error=>result.errors.push(error.message));
  page.on('request',request=>{const path=new URL(request.url()).pathname;if(path.startsWith('/api/'))api.push(path);if(/universe-[\w-]+\.js/.test(path))chunks.push(path);});
  await page.goto(base);await page.locator('.app-icon.is-animated').nth(3).waitFor();
  assert.equal(chunks.length,0);assert.equal(await active(page),0);
  const links=await page.locator('.apps a').evaluateAll(nodes=>nodes.map(n=>({text:n.textContent,href:n.getAttribute('href')})));
  let release,finish;const held=new Promise(done=>{release=done;}),finished=new Promise(done=>{finish=done;});
  await page.route('**/universe-*.js',async route=>{await held;await route.continue();finish();});
  await enter(page).click();await leave(page).click();release();await finished;await page.unroute('**/universe-*.js');
  await page.waitForTimeout(250);assert.equal(await active(page),0);assert.equal(await page.locator('dialog').evaluate(d=>d.open),false);
  await page.locator('.apps a').first().hover();await page.waitForTimeout(200);
  await enter(page).evaluate(button=>button.click());
  await page.waitForFunction(()=>window.standbyProbe.frames>3);
  assert.equal(await active(page),1);assert.equal(await page.locator('#leave-standby').evaluate(button=>button===document.activeElement),true);
  const background=await page.evaluate(()=>window.standbyProbe.backgroundFrames),start=await frames(page);
  await page.waitForTimeout(1000);result.framesInOneSecond=await frames(page)-start;
  assert.ok(result.framesInOneSecond>=2&&result.framesInOneSecond<=33);
  assert.equal(await page.evaluate(()=>window.standbyProbe.backgroundFrames),background,'APP icons must pause behind the standby scene');
  const first=await page.screenshot({path:resolve(output,'validated-desktop.png')});
  await page.waitForTimeout(350);assert.equal(first.equals(await page.screenshot()),false,'the particle field must move');
  result.pixels=await page.evaluate(async image=>{
    const bitmap=await createImageBitmap(new Blob([Uint8Array.from(atob(image),c=>c.charCodeAt(0))]));
    const canvas=document.createElement('canvas');canvas.width=bitmap.width;canvas.height=bitmap.height;
    const context=canvas.getContext('2d');context.drawImage(bitmap,0,0);bitmap.close();const p=context.getImageData(0,0,canvas.width,canvas.height).data;
    let pink=0,cyan=0;for(let i=0;i<p.length;i+=4){if(p[i]>65&&p[i+2]>60&&p[i]>p[i+1]*1.3)pink++;if(p[i+2]>70&&p[i+1]>40&&p[i+2]>p[i]*1.25)cyan++;}
    return {pink,cyan};
  },first.toString('base64'));
  assert.ok(result.pixels.pink>1500&&result.pixels.cyan>1500,'both chromatic particle streams must be visible');
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
  const hidden=await frames(page);await page.waitForTimeout(350);assert.equal(await frames(page),hidden);
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});
  await page.waitForFunction(before=>window.standbyProbe.frames>before,hidden);
  for(const [width,height] of [[1920,1080],[1366,768],[390,844],[320,480],[844,390]]) {
    await page.setViewportSize({width,height});await page.waitForTimeout(150);
    const bounds=await page.locator('#cosmic-standby').evaluate(d=>({w:d.getBoundingClientRect().width,h:d.getBoundingClientRect().height,canvas:[d.querySelector('canvas').width,d.querySelector('canvas').height]}));
    assert.equal(bounds.w,width);assert.equal(bounds.h,height);assert.ok(bounds.canvas[0]<=1920&&bounds.canvas[1]<=1080);
    assert.equal(await leave(page).isVisible(),true);result.responsive.push({width,height,...bounds});
    if(width===390)await page.screenshot({path:resolve(output,'validated-mobile.png')});
  }
  await page.keyboard.press('Escape');assert.equal(await active(page),0);
  const closed=await frames(page);await page.waitForTimeout(250);assert.equal(await frames(page),closed);
  assert.equal(await enter(page).evaluate(button=>button===document.activeElement),true);
  assert.deepEqual(await page.locator('.apps a').evaluateAll(nodes=>nodes.map(n=>({text:n.textContent,href:n.getAttribute('href')}))),links);
  for(let i=0;i<4;i++) {
    await enter(page).click();await page.waitForFunction(()=>window.standbyProbe.contexts.some(gl=>!gl.isContextLost()));
    await leave(page).click();assert.equal(await active(page),0);
  }
  await enter(page).evaluate(button=>{button.click();document.querySelector('#leave-standby').click();button.click();});
  await page.waitForFunction(()=>window.standbyProbe.contexts.some(gl=>!gl.isContextLost()));assert.equal(await active(page),1);
  await leave(page).click();assert.equal(await active(page),0);
  assert.deepEqual(api,[]);assert.ok(chunks.length>=1);
  await context.close();
  const reduced=await browser.newContext({reducedMotion:'reduce'});await reduced.addInitScript(probe);
  const quiet=await reduced.newPage();await quiet.goto(base);await enter(quiet).click();await quiet.waitForFunction(()=>window.standbyProbe.frames>0);
  const staticFrames=await frames(quiet);await quiet.waitForTimeout(350);assert.equal(await frames(quiet),staticFrames);await quiet.keyboard.press('Escape');assert.equal(await active(quiet),0);await reduced.close();
  const unsupported=await browser.newContext();
  await unsupported.addInitScript(()=>{const get=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){return type==='webgl2'&&this.classList.contains('cosmic-canvas')?null:get.call(this,type,...args);};});
  const fallback=await unsupported.newPage();await fallback.goto(base);await enter(fallback).click();
  await fallback.getByRole('status').filter({hasText:'当前浏览器暂时无法显示粒子场'}).waitFor();
  await leave(fallback).click();assert.equal(await fallback.locator('dialog').evaluate(d=>d.open),false);await unsupported.close();
  for(const id of ['dynamometer','sg-6010hb','rm-7010','bootloader']) {
    const html=await readFile(resolve(root,'apps',id,'index.html'),'utf8');assert.equal(html.includes('cosmic-standby'),false,'standby must only be installed on the gateway');
  }
  assert.deepEqual(result.errors,[]);await writeFile(resolve(output,'validation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
}finally{await browser.close();await new Promise(done=>server.close(done));}
