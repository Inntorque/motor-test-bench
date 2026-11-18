import assert from 'node:assert/strict';
import {spawn,execFileSync} from 'node:child_process';
import {createServer} from 'node:net';
import {cp,mkdir,mkdtemp,readFile,writeFile,readdir,rm} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {generateKeyPairSync,createHash,sign} from 'node:crypto';
import {chromium} from 'playwright';

const project=resolve(import.meta.dirname,'..');
const go=process.env.GO_BINARY||'go';
const version=JSON.parse(await readFile(join(project,'package.json'),'utf8')).version;
const parts=version.split('.').map(Number),nextVersion=[parts[0],parts[1],parts[2]+1].join('.'),badVersion=[parts[0],parts[1],parts[2]+2].join('.');
const source=resolve(process.env.PORTABLE_SOURCE||join(project,'artifacts/releases',`v${version}`,`InntorqueBench-${process.platform}-x64`));
const output=join(project,'.runtime/portable-validation');await mkdir(output,{recursive:true});
const fixture=await mkdtemp(join(output,'run-')),root=join(fixture,'app');
const launcher=process.platform==='win32'?'测功机.exe':'测功机';
const {privateKey,publicKey}=generateKeyPairSync('ed25519');
let child,browser,exit,inspectorPort,debugPort,passed=false;
const wait=ms=>new Promise(done=>setTimeout(done,ms));
async function until(check,message,timeout=30000){const end=Date.now()+timeout;while(Date.now()<end){if(await check())return;await wait(100);}throw new Error(message);}
async function freePort(){
 const server=createServer();await new Promise(done=>server.listen(0,'127.0.0.1',done));const port=server.address().port;await new Promise(done=>server.close(done));return port;
}
async function open(){
 debugPort=await freePort();inspectorPort=await freePort();
 child=spawn(join(root,launcher),[`--remote-debugging-port=${debugPort}`,`--inspect=${inspectorPort}`,...(process.platform==='linux'?['--ozone-platform=x11']:[])],{env:{...process.env,INNTORQUE_TEST_PROFILE:join(root,'config')},stdio:['ignore','pipe','pipe']});
 exit=new Promise(done=>child.once('exit',(code,signal)=>done({code,signal})));let diagnostic='';child.stderr.on('data',d=>diagnostic+=d);
 await until(async()=>{
   if(child.exitCode!==null)throw new Error(`launcher exited ${child.exitCode}: ${diagnostic}`);
   try{const r=await fetch(`http://127.0.0.1:${debugPort}/json/version`);return r.ok;}catch{return false;}
 },'portable app did not open');
 browser=await chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`);
 const context=browser.contexts()[0];await until(()=>context.pages().some(p=>p.url().includes('bench.inntorque.local')),'missing desktop page');
 const page=context.pages().find(p=>p.url().includes('bench.inntorque.local'));
 await page.getByText('应用已就绪 · 本机设备直连',{exact:true}).waitFor({state:'attached'});
 await page.getByRole('button',{name:'打开数据目录',exact:true}).waitFor();
 await until(async()=>!(JSON.parse(await readFile(join(root,'active.json'),'utf8'))).pending,'new version did not commit');
 return page;
}
async function evaluateMain(expression){
 const targets=await(await fetch(`http://127.0.0.1:${inspectorPort}/json/list`)).json();
 const socket=new WebSocket(targets[0].webSocketDebuggerUrl);
 await new Promise((done,fail)=>{socket.onopen=done;socket.onerror=fail;});
 const response=new Promise((done,fail)=>{socket.onmessage=e=>{const message=JSON.parse(e.data);if(message.id===1){if(message.error||message.result?.exceptionDetails)fail(new Error(JSON.stringify(message)));else done();}};socket.onerror=fail;});
 socket.send(JSON.stringify({id:1,method:'Runtime.evaluate',params:{expression}}));
 await response;socket.close();
}
const electron="process.getBuiltinModule('module').createRequire(process.cwd()+'/main.cjs')('electron')";
async function close(page){
 const current=await page.evaluate(async()=>(await fetch('/api/desktop/autosave')).json());
 await evaluateMain(electron+'.BrowserWindow.getAllWindows()[0].close()');
 const result=await Promise.race([exit,wait(15000).then(()=>{throw new Error('launcher did not close after data save');})]);
 assert.equal(result.code,0);child=null;browser=null;
 const name=(await readdir(join(root,'测功数据'))).find(name=>name.endsWith(current.id));
 const report=JSON.parse(await readFile(join(root,'测功数据',name,'report.json'),'utf8'));
 console.log('closed recording',report.id,report.state);
 assert.equal(report.state,'complete');
}
async function stage(targetVersion,{broken=false}={}){
 const packageRoot=join(fixture,targetVersion);await cp(join(root,'versions',version),packageRoot,{recursive:true});
 const app=join(packageRoot,'resources/app');const pkg=JSON.parse(await readFile(join(app,'package.json'),'utf8'));pkg.version=targetVersion;await writeFile(join(app,'package.json'),JSON.stringify(pkg));
 if(broken)await writeFile(join(app,'main.mjs'),"import {app} from 'electron';app.exit(7);\n");
 const zip=join(fixture,`${targetVersion}.zip`);execFileSync(go,['run','./cmd/pack',packageRoot,zip],{cwd:join(project,'desktop/launcher')});
 const bytes=await readFile(zip),sha256=createHash('sha256').update(bytes).digest('hex');
 const payload=Buffer.from(JSON.stringify({schema:1,version:targetVersion,platform:process.platform,arch:'x64',minLauncher:1,protocolMajor:1,dataSchema:3,size:bytes.length,sha256,url:'https://example.invalid/test-only.zip'}));
 const envelope=JSON.stringify({payload:payload.toString('base64'),signature:sign(null,payload,privateKey).toString('base64')});
 await rm(packageRoot,{recursive:true});
 if(broken){
   await cp(zip,join(root,'updates',sha256+'.zip'));
   await writeFile(join(root,'updates/pending.json'),envelope);
   await rm(zip);
 }else{
   await writeFile(join(fixture,'manifest.json'),envelope);
   return zip;
 }
}
try {
 await cp(source,root,{recursive:true});
 await writeFile(join(root,'release-public-key.pem'),publicKey.export({type:'spki',format:'pem'}));
 await mkdir(join(root,'测功数据'),{recursive:true});await writeFile(join(root,'测功数据/preserve.txt'),'measurements must survive');
 const zip=await stage(nextVersion);
 let page=await open();
 assert.equal(await page.evaluate(()=>window.inntorqueDesktop.dataDirectory()),join(root,'测功数据'));
 await page.evaluate(()=>localStorage.setItem('portable-update-check','saved'));
 // Replace only the remote distribution and operator choices in this isolated Electron process.
 await evaluateMain(`(()=>{
   const app=${electron},fs=process.getBuiltinModule('fs'),stream=process.getBuiltinModule('stream');
   app.net.fetch=async url=>{
     const manifest=String(url).endsWith('.json');
     if(!manifest && url!=='https://example.invalid/test-only.zip')throw Error('unexpected fixture request');
     const path=manifest?${JSON.stringify(join(fixture,'manifest.json'))}:${JSON.stringify(zip)};
     const result=new Response(stream.Readable.toWeb(fs.createReadStream(path)));
     Object.defineProperty(result,'url',{value:url});return result;
   };
   app.dialog.showMessageBox=async(win,options)=>({response:options.buttons?.[0]==='下载更新'?0:1});
   app.Menu.getApplicationMenu().getMenuItemById('check-update').click();
 })()`);
 await until(async()=>{try{return !!JSON.parse(await readFile(join(root,'updates/downloaded.json'),'utf8')).signature;}catch{return false;}},'menu did not stage the signed release');
 await close(page);
 page=await open();
 await evaluateMain(`(()=>{
   const app=${electron};
   app.net.fetch=async()=>{throw Error('offline')};
   app.dialog.showMessageBox=async(win,options)=>({response:options.buttons?.[0]==='保存并重启更新'?0:1});
   app.Menu.getApplicationMenu().getMenuItemById('check-update').click();
 })()`);
 await until(async()=>{const state=JSON.parse(await readFile(join(root,'active.json'),'utf8'));return state.version===nextVersion && !state.pending;},'menu did not restart and commit downloaded release',60000);
 browser=await chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`);
 page=browser.contexts()[0].pages().find(p=>p.url().includes('bench.inntorque.local'));
 await page.getByRole('button',{name:'打开数据目录',exact:true}).waitFor();
 assert.equal(JSON.parse(await readFile(join(root,'active.json'),'utf8')).version,nextVersion);
 assert.equal(await page.evaluate(()=>localStorage.getItem('portable-update-check')),'saved');
 await page.screenshot({path:join(output,'updated.png')});await close(page);
 // An unconfirmed version must restore config and actually reopen the previous app.
 await stage(badVersion,{broken:true});page=await open();
 assert.equal(JSON.parse(await readFile(join(root,'active.json'),'utf8')).version,nextVersion);
 assert.equal(await page.evaluate(()=>localStorage.getItem('portable-update-check')),'saved');
 await page.screenshot({path:join(output,'rolled-back.png')});await close(page);
 assert.equal(await readFile(join(root,'测功数据/preserve.txt'),'utf8'),'measurements must survive');
 const folders=(await readdir(join(root,'测功数据'),{withFileTypes:true})).filter(e=>e.isDirectory());
 assert.ok(folders.length>=3);
 for(const folder of folders){const report=JSON.parse(await readFile(join(root,'测功数据',folder.name,'report.json'),'utf8'));console.log('archive',folder.name,report.state);assert.equal(report.state,'complete');}
 const result={platform:process.platform,launch:true,menuDownload:true,offlineInstall:true,update:`${version} -> ${nextVersion}`,rollback:`failed ${badVersion} -> ${nextVersion}`,configPreserved:true,dataPreserved:true,completeRecordings:folders.length,hardwareOutputsEnabled:false};
 await writeFile(join(output,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));passed=true;
} finally {
 if(browser)await browser.close().catch(()=>{});
 if(child&&child.exitCode===null){child.kill('SIGTERM');await exit;}
 if(passed)await rm(fixture,{recursive:true,force:true});else console.error(`Retained failed fixture: ${fixture}`);
}
