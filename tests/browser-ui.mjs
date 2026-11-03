import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdir} from 'node:fs/promises';
import {resolve,dirname,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
import {build} from 'vite';
const root = resolve(dirname(fileURLToPath(import.meta.url)),'..'), output = resolve(root,'.runtime/browser-native-ui');
await mkdir(output,{recursive:true});
await build({configFile:false,root,publicDir:false,build:{outDir:resolve(root,'.runtime/browser-fixture-bootstrap'),emptyOutDir:true,minify:true,lib:{entry:resolve(root,'tests/fixtures/browser-serial.js'),name:'BrowserSerialFixture',formats:['iife'],fileName:() => 'fixture.js'}}});
const fixture = await readFile(resolve(root,'.runtime/browser-fixture-bootstrap/fixture.js'),'utf8');
const types = {'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml','.png':'image/png'};
const server = createServer(async (request,response) => {
  const path = decodeURIComponent(new URL(request.url,'http://localhost').pathname);
  const file = resolve(root,'site','.' + (path.endsWith('/') ? path + 'index.html' : path));
  if (!file.startsWith(resolve(root,'site') + '/')) {response.writeHead(403).end();return;}
  try {response.setHeader('Content-Type',types[extname(file)] || 'application/octet-stream');response.end(await readFile(file));}
  catch {response.writeHead(404).end();}
});
await new Promise(done => server.listen(0,'127.0.0.1',done));
const base = process.env.BROWSER_SITE_URL || `http://127.0.0.1:${server.address().port}/`;
const catalog = JSON.parse(await readFile(resolve(root,'site/catalog.json'),'utf8'));
assert.equal(catalog.runtime,'browser');assert.equal(catalog.serviceUrl,undefined);
assert.equal(catalog.apps.length,3);assert.equal(catalog.apps.every(app => app.runtime === 'browser' && !app.port),true);
let browser;
try {
  browser = await chromium.launch({headless:true,...(process.env.CHROME_PATH ? {executablePath:process.env.CHROME_PATH} : {})});
  const context = await browser.newContext({viewport:{width:1440,height:1000}}), results = [];
  const page = await context.newPage(), errors = [], apiNetwork = [], serviceNetwork = [];
  page.on('pageerror',error => errors.push(error.message));
  page.on('request',request => {const url = new URL(request.url());if (url.pathname.startsWith('/api/')) apiNetwork.push(request.url());if (['5181','5173','8892','8888'].includes(url.port)) serviceNetwork.push(request.url());});
  for (const id of ['dynamometer','sg-6010hb','rm-7010']) {
    await page.goto(`${base}apps/${id}/`);
    await page.getByRole('status').filter({hasText:'网页已就绪'}).first().waitFor();
    await page.waitForTimeout(1200);
    assert.equal(await page.getByText('暂时无法打开此 APP',{exact:true}).count(),0);
    const status = await page.evaluate(async id => {
      const response = await fetch(id === 'dynamometer' ? '/api/v1/health' : '/api/status');
      return {ok:response.ok,data:await response.json()};
    },id);
    assert.equal(status.ok,true);
    if (id === 'dynamometer') {assert.equal(status.data.runtime,'browser');assert.equal(status.data.channels['current-motor'].connected,false);assert.equal(status.data.channels['torque-sensor'].connected,false);await page.getByRole('heading',{name:'扭矩工作台',exact:true}).waitFor();}
    else {assert.equal(status.data.connected,false);assert.equal(status.data.params.every(param => param.value === null),true);}
    await page.screenshot({path:resolve(output,`${id}.png`),fullPage:false});
    results.push({id,openedWithoutService:true,disconnectedMeasurements:true});
  }
  const direct = await browser.newContext({viewport:{width:1440,height:1000}});
  await direct.addInitScript({content:fixture});
  const control = await direct.newPage();
  control.on('pageerror',error => errors.push(error.message));
  control.on('request',request => {const url = new URL(request.url());if (url.pathname.startsWith('/api/')) apiNetwork.push(request.url());if (['5181','5173','8892','8888'].includes(url.port)) serviceNetwork.push(request.url());});
  await control.goto(`${base}apps/dynamometer/`);
  await control.getByRole('status').filter({hasText:'网页已就绪'}).first().waitFor();
  assert.equal(await control.evaluate(() => window.__serialFixture.writeCount),0,'opening the page must not touch devices');
  await control.getByRole('button',{name:'授权串口',exact:true}).click();
  assert.equal(await control.evaluate(() => window.__serialFixture.authorizations),1);
  assert.equal(await control.evaluate(() => window.__serialFixture.writeCount),0,'granting permission must not configure or enable hardware');
  async function command(path,body = {}) {
    return control.evaluate(async ({path,body}) => {
      const health = await (await fetch('/api/v1/health')).json();
      const response = await fetch('/api/v1' + path,{method:'POST',headers:{'Content-Type':'application/json','X-MotorBench-Token':health.token,'X-Workbench-Client':'browser-fixture-client-0001','X-Control-Epoch':String(health.safety.epoch)},body:JSON.stringify(body)});
      const data = await response.json();if (!response.ok) throw new Error(data.error);return data;
    },{path,body});
  }
  async function waitForState(path,predicate) {
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      const value = await control.evaluate(async path => (await fetch(path)).json(),path);
      if (predicate(value)) return value;
      await control.waitForTimeout(50);
    }
    throw new Error('Browser state did not reach expected condition: ' + path);
  }
  const connected = await command('/motor-current/connect');assert.equal(connected.connected,true);
  assert.equal(await control.evaluate(() => window.__serialFixture.board.commands.filter(command => command.opcode === 6).length),0,'connecting must not start the motor');
  const motor = (await command('/test-subjects/create',{kind:'motor',name:'Browser fixture motor'})).item;
  const driver = (await command('/test-subjects/create',{kind:'driverBoard',name:'Browser fixture driver'})).item;
  const setup = (await command('/test-subjects/save-setup',{motorId:motor.id,name:'bare fixture',hasGearbox:false,ratio:1})).item;
  await command('/test-subjects/select',{motorId:motor.id,driverBoardId:driver.id,testSetupId:setup.id});
  const recording = (await command('/recordings/start',{name:'Worker end-to-end test'})).current;
  await command('/motor-current/start',{mode:'maximum_output',busLimitA:20,busVoltageV:24});
  await waitForState('/api/v1/motor-current/status',value => value.run.phase === 'running' && value.snapshot?.feedback?.iq_reference_a === 27);
  const active = await control.evaluate(async () => (await fetch('/api/v1/motor-current/status')).json());
  assert.equal(active.snapshot.motor.output_on,true,JSON.stringify({run:active.run,error:active.error,snapshot:active.snapshot?.motor,commands:await control.evaluate(() => window.__serialFixture.board.commands.map(command => command.opcode))}));assert.equal(active.snapshot.feedback.iq_a,26.8);assert.equal(active.snapshot.feedback.id_a,-.4);
  assert.equal(await control.evaluate(() => window.__serialFixture.board.commands.filter(command => command.opcode === 6).length),1);
  assert.equal(await control.evaluate(() => window.__serialFixture.board.commands.filter(command => command.opcode === 7).length),0,'maximum mode must leave the available-Iq demand in the driver');
  const locked = await control.evaluate(async () => (await fetch('/api/v1/test-subjects')).json());assert.equal(locked.configurationLocked,true);
  await command('/recordings/stop',{id:recording.id});
  const history = await control.evaluate(async () => (await fetch('/api/v1/recordings/status?history=1')).json());
  assert.equal(history.records[0].testSubject.motor.name,'Browser fixture motor');assert.equal(history.records[0].testSubject.driverBoard.name,'Browser fixture driver');
  const downloaded = control.waitForEvent('download');
  await control.evaluate(id => {const anchor = document.createElement('a');anchor.href=`/api/v1/recordings/files/${id}/timeline.csv`;anchor.download='worker-test.csv';anchor.click();},recording.id);
  const download = await downloaded;await download.saveAs(resolve(output,'worker-test.csv'));
  const csv = await readFile(resolve(output,'worker-test.csv'),'utf8');assert.ok(csv.includes('Browser fixture motor'));assert.ok(csv.includes('Browser fixture driver'));
  await command('/control/stop');
  await waitForState('/api/v1/control/status',value => !value.stopping);
  const stopped = await control.evaluate(async () => (await fetch('/api/v1/motor-current/status')).json());assert.equal(stopped.snapshot.motor.output_on,false);assert.equal(stopped.run.stopConfirmed,true);
  results.push({id:'dynamometer',virtualSerialOnly:true,workerCanRoundTrip:true,connectNeverStarts:true,maximumIqReference:27,actualIq:26.8,stopConfirmed:true,subjectLocked:true,indexedDbRecord:true,detachedAnchorCsvExport:true});
  await direct.close();
  assert.deepEqual(errors,[]);assert.deepEqual(apiNetwork,[]);assert.deepEqual(serviceNetwork,[]);
  console.log(JSON.stringify({passed:true,apps:results,pageErrors:errors,httpApiRequests:apiNetwork,localhostServiceRequests:serviceNetwork},null,2));
} finally {await browser?.close();await new Promise(done => server.close(done));}
