import assert from 'node:assert/strict';
import {mkdtemp, mkdir, readFile, readdir, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {createRequire} from 'node:module';
import {_electron} from 'playwright';
import {build} from 'vite';

const root = resolve(import.meta.dirname, '..'), output = resolve(root, '.runtime/desktop-validation');
await mkdir(output, {recursive:true});
const profile = await mkdtemp(resolve(tmpdir(), 'inntorque-desktop-test-'));
const executablePath = process.env.DESKTOP_EXECUTABLE || createRequire(resolve(root,'desktop/package.json'))('electron');
const args = process.env.DESKTOP_EXECUTABLE ? [] : [resolve(root, 'desktop')];
const launch = extra => _electron.launch({executablePath, args:[...args,...(process.platform === 'linux' ? ['--ozone-platform=x11'] : []),...extra], chromiumSandbox:true,
  env:{...process.env,INNTORQUE_TEST_PROFILE:profile},timeout:20000});
let app;
const results = [], errors = [];
try {
  app = await launch([]);
  let page = await app.firstWindow();
  page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  await page.getByText('应用已就绪 · 本机设备直连', {exact:true}).waitFor({state:'attached'});
  assert.deepEqual(await page.evaluate(() => ({serial:!!navigator.serial,usb:!!navigator.usb,node:typeof require})), {serial:true,usb:true,node:'undefined'});
  assert.equal(await app.evaluate(({app}) => app.commandLine.hasSwitch('no-sandbox')), false);
  assert.equal(await page.getByRole('button',{name:'开始记录',exact:true}).count(),0);
  await page.getByRole('img',{name:'自动保存已开启',exact:true}).waitFor();
  assert.equal(await page.locator('.bench-status').count(),0);
  assert.equal(await page.locator('.sidebar .auto-save-status').count(),1);
  await page.evaluate(()=>{window.originalAutoSaveFetch=window.fetch;window.fetch=(input,options)=>String(input)==='/api/desktop/autosave'?Promise.resolve(new Response(JSON.stringify({running:true,error:'测试：磁盘空间不足'}),{headers:{'Content-Type':'application/json'}})):window.originalAutoSaveFetch(input,options);});
  await page.getByRole('alert').filter({hasText:'自动保存失败：测试：磁盘空间不足'}).waitFor();
  await page.evaluate(()=>{window.fetch=window.originalAutoSaveFetch;delete window.originalAutoSaveFetch;});
  await page.getByRole('img',{name:'自动保存已开启',exact:true}).waitFor();
  const dataDirectory=await page.evaluate(()=>window.inntorqueDesktop.dataDirectory());
  assert.equal(dataDirectory,resolve(profile,'测功数据'));
  await app.evaluate(({shell})=>{globalThis.openedDataDirectory='';shell.openPath=async path=>{globalThis.openedDataDirectory=path;return '';};});
  await page.getByRole('button',{name:'打开数据目录',exact:true}).click();
  assert.equal(await app.evaluate(()=>globalThis.openedDataDirectory),dataDirectory);
  const health = await page.evaluate(async () => (await fetch('/api/v1/health')).json());
  assert.equal(Object.values(health.channels).some(channel => channel.connected), false);
  assert.equal(await page.evaluate(async () => (await fetch('/api/desktop/close-ready')).json().then(result => result.ready)), true);

  await page.getByLabel('电机输出模式',{exact:true}).selectOption('current_loop');
  await page.getByLabel('目标 Iq / A 峰值',{exact:true}).fill('1.25');
  const windowTitle=await app.evaluate(({BrowserWindow,app})=>({title:BrowserWindow.getAllWindows()[0].getTitle(),version:app.getVersion()}));
  assert.equal(windowTitle.title,`Inntorque 测功机 ${windowTitle.version}`);
  for (const [width,height] of [[1600,1000],[1366,768],[1024,768],[800,600],[720,480]]) {
    await app.evaluate(({BrowserWindow}, size) => { const win=BrowserWindow.getAllWindows()[0];win.unmaximize();win.setContentSize(...size); }, [width,height]);
    await page.waitForTimeout(150);
    const layout = await page.evaluate(() => {
      const main=document.querySelector('main'), nav=document.querySelector('.sidebar');
      const chart=document.querySelector('.measurement-area'), controls=document.querySelector('.control-area');
      return {width:innerWidth,height:innerHeight,main:main.getBoundingClientRect().toJSON(),nav:nav.getBoundingClientRect().toJSON(),
        chart:chart.getBoundingClientRect().toJSON(),controls:controls.getBoundingClientRect().toJSON(),
        overflow:main.scrollWidth-main.clientWidth,bodyOverflow:document.body.scrollWidth-innerWidth};
    });
    assert.ok(layout.main.top >= layout.nav.bottom - 1, JSON.stringify(layout));
    assert.ok(layout.main.top < layout.height, JSON.stringify(layout));
    assert.equal(await page.locator('.page-header').isVisible(),false,'bench has no second toolbar row');
    assert.ok(await page.locator('.sidebar').evaluate(el=>el.scrollWidth<=el.clientWidth+1&&el.clientHeight<=40),'single toolbar must fit the window');
    assert.equal(await page.locator('.test-subject-picker:visible').count(),1);
    await page.locator('.test-subject-picker > summary').click();
    assert.ok(await page.locator('.subject-popover').evaluate(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight;}),'subject picker must stay on screen');
    await page.keyboard.press('Escape');
    assert.ok(layout.overflow <= 1 && layout.bodyOverflow <= 1, JSON.stringify(layout));
    assert.ok(layout.controls.width === 0 || layout.chart.right <= layout.controls.left + 1, 'chart and controls must not overlap');
    if(layout.controls.width>0)assert.ok(await page.locator('.control-area').evaluate(el=>el.scrollHeight<=el.clientHeight+1),'right controls must fit without scrolling');
    assert.ok(await page.evaluate(()=>document.querySelector('main').scrollHeight-document.querySelector('main').clientHeight)<=1, 'workspace must fit the window');
    assert.equal(await page.locator('.desktop-devices').isVisible(),false);
    assert.ok(await page.locator('.plot').evaluate(el=>el.getBoundingClientRect().width > el.closest('.chart-panel').clientWidth*.8), 'plot must use the available width');
    await page.screenshot({path:resolve(output,`window-${width}x${height}.png`)});
    await page.getByRole('button',{name:'查看扭矩特性曲线',exact:true}).click();
    const checkChart = async () => {
      await page.waitForTimeout(150);
      const fit=await page.locator('.motor-performance-chart').evaluate(svg=>{
        const box=svg.getBoundingClientRect(),panel=svg.closest('.torque-characteristics');
        const texts=[...svg.querySelectorAll('text')].map(text=>text.getBoundingClientRect());
        const containers=[panel,svg.parentElement,...(panel.classList.contains('characteristic-expanded')?[]:[panel.closest('.chart-panel')])];
        return {width:box.width,height:box.height,
          outside:texts.some(r=>r.left<box.left-1||r.right>box.right+1||r.top<box.top-1||r.bottom>box.bottom+1),
          overflow:containers.some(el=>el.scrollHeight>el.clientHeight+1||el.scrollWidth>el.clientWidth+1),
          offscreen:box.bottom>innerHeight||box.right>innerWidth};
      });
      await page.screenshot({path:resolve(output,'chart-fit-current.png')});
      assert.ok(fit.width>200&&fit.height>80&&!fit.outside&&!fit.overflow&&!fit.offscreen,`complete chart ${width}x${height}: ${JSON.stringify(fit)}`);
    };
    await checkChart();
    await page.screenshot({path:resolve(output,`chart-${width}.png`)});
    await page.getByRole('button',{name:'坐标与连线',exact:true}).click();
    await page.getByLabel('特性曲线连线方式',{exact:true}).selectOption('curve');
    await page.keyboard.press('Escape');
    await checkChart();
    await page.getByRole('button',{name:'放大查看',exact:true}).click();
    await checkChart();
    await page.screenshot({path:resolve(output,`chart-expanded-${width}.png`)});
    assert.equal(await page.getByRole('button',{name:'曲线说明'}).count(),0);
    await page.getByRole('button',{name:'退出放大',exact:true}).click();
    await checkChart();
    await page.getByRole('button',{name:'查看时间曲线',exact:true}).click();
    results.push({requested:[width,height],actual:[layout.width,layout.height],overflow:layout.overflow});
  }
  await page.keyboard.press('Control++');
  await page.keyboard.down('Control'); await page.mouse.wheel(0,-400); await page.keyboard.up('Control');
  assert.equal(await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].webContents.getZoomFactor()), 1);
  await page.evaluate(() => { window.desktopReloadSentinel = true; });
  await page.keyboard.press('F5');
  assert.equal(await page.evaluate(() => window.desktopReloadSentinel), true);

  assert.equal(await page.getByRole('button',{name:/授权/}).count(),0);
  const detected=await page.evaluate(async()=>({
    serial:(await navigator.serial.getPorts()).length,
    ports:(await(await fetch('/api/ports')).json()).ports,
  }));
  assert.ok(detected.ports.length>=detected.serial);
  assert.equal(detected.ports.some(port=>port.opened),false);
  console.log('Automatically listed interfaces:',detected.ports.length);

  await page.getByRole('navigation').getByRole('button',{name:'试验记录'}).click();
  await page.getByRole('heading',{name:'试验记录',exact:true}).waitFor();
  await page.getByRole('navigation').getByRole('button',{name:'专项测试'}).click();
  await page.getByRole('heading',{name:'专项测试',exact:true,level:1}).waitFor();
  assert.equal(await page.evaluate(()=>document.querySelector('main').scrollWidth-document.querySelector('main').clientWidth),0);
  await page.getByRole('navigation').getByRole('button',{name:'扭矩工作台'}).click();
  await page.getByRole('button',{name:/设备设置 ·/}).click();
  await page.getByLabel('CAN适配器').waitFor();
  assert.ok(await page.locator('.desktop-devices').evaluate(el=>el.scrollWidth-el.clientWidth)<=1);
  await page.screenshot({path:resolve(output,'settings-small.png')});
  await page.keyboard.press('Escape');

  await page.getByRole('combobox',{name:'工作区视图',exact:true}).selectOption('diagnostics');
  await page.getByRole('region',{name:'电流详细反馈'}).waitFor();
  await page.getByRole('combobox',{name:'工作区视图',exact:true}).selectOption('chart');
  await page.getByRole('combobox',{name:'工作区视图',exact:true}).selectOption('controls');
  await page.getByRole('button',{name:'停止两路输出',exact:true}).waitFor();
  assert.ok(await page.evaluate(()=>document.querySelector('main').scrollWidth-document.querySelector('main').clientWidth)<=1);
  assert.ok(await page.locator('.control-area').evaluate(el=>el.scrollHeight<=el.clientHeight+1),'small-window controls must fit without scrolling');
  assert.ok(await page.locator('.coupled-current-panel').evaluate(el=>el.scrollHeight<=el.clientHeight+1),'common controls must not be clipped');
  await page.getByRole('button',{name:'诊断读数',exact:true}).click();
  await page.getByRole('dialog',{name:'诊断读数'}).waitFor();
  await page.getByRole('dialog',{name:'诊断读数'}).getByText('制动器励磁电流 · 回读',{exact:true}).waitFor();
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'电机独立控制',exact:true}).click();
  await page.getByRole('dialog',{name:'电机独立控制'}).waitFor();
  await page.getByRole('button',{name:'开始电流步进',exact:true}).waitFor();
  await page.keyboard.press('Escape');
  await page.screenshot({path:resolve(output,'controls-small.png')});
  await page.getByRole('combobox',{name:'工作区视图',exact:true}).selectOption('chart');

  await build({configFile:false,root,publicDir:false,build:{outDir:resolve(output,'fixture'),emptyOutDir:true,minify:true,
    lib:{entry:resolve(root,'tests/fixtures/browser-serial.js'),name:'BrowserSerialFixture',formats:['iife'],fileName:()=>'fixture.js'}}});
  await app.context().addInitScript({content:(await readFile(resolve(output,'fixture/fixture.js'),'utf8'))+'\nwindow.__serialFixture.granted = true;'});
  await app.context().addInitScript(()=>{const NativeWorker=window.Worker;window.Worker=class extends NativeWorker{constructor(...args){super(...args);window.__statusWorker=this;}};});
  await page.reload();
  await page.getByText('应用已就绪 · 本机设备直连',{exact:true}).waitFor({state:'attached'});
  const recoveryBanner=await page.evaluate(()=>{
    const emit=control=>window.__statusWorker.dispatchEvent(new MessageEvent('message',{data:{kind:'event',event:'state',data:{control}}}));
    const text=()=>document.querySelector('#inntorque-browser-access [role=status]').textContent;
    emit({blocked:true,reason:'测试：等待设备回读'});const blocked=text();
    emit({blocked:false});return {blocked,recovered:text()};
  });
  assert.deepEqual(recoveryBanner,{blocked:'测试：等待设备回读',recovered:'应用已就绪 · 本机设备直连'});
  assert.equal(await page.evaluate(()=>window.__serialFixture.writeCount),0);
  assert.equal(await page.evaluate(()=>window.__serialFixture.authorizations),0);
  assert.equal(await page.evaluate(()=>window.__serialFixture.writeCount),0);
  const command = (path,body={}) => page.evaluate(async ({path,body}) => {
    const health=await(await fetch('/api/v1/health')).json();
    const result=await fetch('/api/v1'+path,{method:'POST',headers:{'Content-Type':'application/json','X-Motorbench-Token':health.token,'X-Workbench-Client':'desktop-test-client-0001','X-Control-Epoch':String(health.safety.epoch)},body:JSON.stringify(body)});
    const data=await result.json();if(!result.ok)throw new Error(JSON.stringify(data));return data;
  },{path,body});
  await command('/motor-current/connect');
  const motor=(await command('/test-subjects/create',{kind:'motor',name:'Desktop fixture motor'})).item;
  const driver=(await command('/test-subjects/create',{kind:'driverBoard',name:'Desktop fixture driver'})).item;
  const setup=(await command('/test-subjects/save-setup',{motorId:motor.id,name:'Desktop bare motor',hasGearbox:false,ratio:1})).item;
  await command('/test-subjects/select',{motorId:motor.id,driverBoardId:driver.id,testSetupId:setup.id});
  await page.waitForTimeout(1200);
  const automatic=await page.evaluate(async()=>(await fetch('/api/desktop/autosave')).json());
  const autoFolder=resolve(dataDirectory,(await readdir(dataDirectory)).find(name=>name.endsWith(automatic.id)));
  const autoRows=(await readFile(resolve(autoFolder,'timeline.jsonl'),'utf8')).trim().split('\n').map(JSON.parse);
  assert.ok(autoRows.some(row=>row.source==='current-motor'));
  const alignedCsv=await readFile(resolve(autoFolder,'measurements.csv'),'utf8');
  assert.match(alignedCsv,/motor_sample_sequence/);
  assert.ok(alignedCsv.trim().split('\r\n').length>1);
  assert.ok(autoRows.some(row=>row.source==='measurement'&&row.motor_valid));
  assert.ok(autoRows.some(row=>row.motor_name==='Desktop fixture motor'));
  const record=(await command('/recordings/start',{name:'Desktop persistence test'})).current;
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(()=>window.inntorqueCanClose()),true);
  const report=await page.evaluate(async id=>(await fetch(`/api/v1/recordings/files/${id}/report.json`)).json(),record.id);
  assert.equal(report.name,'Desktop persistence test');
  await app.evaluate(({session}, file)=>{
    globalThis.desktopDownload=new Promise(done=>session.defaultSession.once('will-download',(_event,item)=>{
      item.setSavePath(file);
      item.once('done',(_event,state)=>done(state));
    }));
  },resolve(output,'desktop-record.csv'));
  await page.evaluate(id=>{
    const link=document.createElement('a');link.href=`/api/v1/recordings/files/${id}/timeline.csv`;link.download='desktop-record.csv';link.click();
  },record.id);
  assert.equal(await app.evaluate(()=>globalThis.desktopDownload),'completed');
  assert.match(await readFile(resolve(output,'desktop-record.csv'),'utf8'),/Desktop fixture motor/);
  const savedChart=await page.evaluate(async()=>{
    const id=crypto.randomUUID().replaceAll('-',''),runId=crypto.randomUUID().replaceAll('-','');
    const point={runId,stage:0,kind:'stage',torque:.5,measuredTorque:.24,speed:100,power:2.51,voltage:24,current:1,inputPower:24,efficiency:10.46};
    const {directory}=await window.inntorqueDesktop.saveData({id,report:{id,startedAt:100,characteristicPoints:[{...point,kind:'sweep',torque:.5,measuredTorque:1.4},point],
      characteristicRuns:[{id:runId,motorName:'离线曲线验收',loadStartedAt:100}]}});
    return {directory,runId};
  });
  const svg=await readFile(resolve(savedChart.directory,`特性图-${savedChart.runId}.svg`),'utf8');
  assert.equal(svg.match(/data-series=/g)?.length,6);assert.match(svg,/离线曲线验收/);assert.match(svg,/制动输出设定扭矩/);
  assert.match(svg,/M510\.00,/);assert.doesNotMatch(svg,/L510\.00,/);
  await page.evaluate(()=>localStorage.setItem('desktop-persistence-check','saved'));
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(()=>window.inntorqueCanClose()),true);
  const closed=app.waitForEvent('close');
  await app.evaluate(({BrowserWindow})=>{BrowserWindow.getAllWindows()[0].close();});
  await closed;
  const autoReport=JSON.parse(await readFile(resolve(autoFolder,'report.json'),'utf8'));
  assert.equal(autoReport.running,false);assert.equal(autoReport.state,'complete');
  for (const scale of [1,1.25,1.5,2]) {
    app=await launch([`--force-device-scale-factor=${scale}`]);
    page=await app.firstWindow();
    await page.getByText('应用已就绪 · 本机设备直连',{exact:true}).waitFor({state:'attached'});
    assert.equal(await page.evaluate(()=>localStorage.getItem('desktop-persistence-check')),'saved');
    const history=await page.evaluate(async()=>(await fetch('/api/v1/recordings/status')).json());
    assert.ok(history.records.some(row=>row.id===record.id && row.state==='complete'));
    await page.screenshot({path:resolve(output,`scale-${scale*100}-percent.png`)});
    assert.equal(await page.evaluate(()=>document.querySelector('main').scrollWidth-document.querySelector('main').clientWidth),0);
    await app.close();
    app=null;
  }
  assert.deepEqual(errors,[]);
  await writeFile(resolve(output,'results.json'),JSON.stringify({results,automaticDeviceAccess:true,zoomLocked:true,reloadBlocked:true,recordingFinalizedOnClose:true,persistence:true,errors},null,2));
  console.log('Desktop checks passed:',JSON.stringify(results));
} catch (error) {
  console.error(error);
  if (app) { try { await (await app.firstWindow()).screenshot({path:resolve(output,'failure.png')}); } catch {} }
  throw error;
} finally {
  if (app && app.process().exitCode === null) {
    const exited=new Promise(done=>app.process().once('exit',done));
    app.process().kill('SIGKILL');
    await exited;
  }
  await rm(profile,{recursive:true,force:true});
}
