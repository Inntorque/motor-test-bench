import {UpdateService} from './update-service.mjs';
import {DataArchive} from './archive.mjs';
import {app, BrowserWindow, dialog, ipcMain, Menu, net, powerSaveBlocker, screen, session, shell} from 'electron';
import {readFile, writeFile, mkdir, rm} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {dirname, resolve, sep} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const site = existsSync(resolve(root, 'site')) ? resolve(root, 'site') : resolve(root, '../site');
const origin = 'https://bench.inntorque.local';
const entry = `${origin}/apps/dynamometer/`;
app.setName('InntorqueBench');
if (process.platform === 'linux') {
  app.setDesktopName('inntorque-bench.desktop');
  app.commandLine.appendSwitch('class', 'InntorqueBench');
}
app.setPath('userData', process.env.INNTORQUE_TEST_PROFILE || resolve(app.getPath('appData'), 'InntorqueBench'));
const portableRoot = process.env.INNTORQUE_APP_ROOT;
if(portableRoot&&!process.env.INNTORQUE_TEST_PROFILE)app.setPath('userData',resolve(portableRoot,'config'));
const dataDirectory = resolve(portableRoot || process.env.INNTORQUE_TEST_PROFILE || (app.isPackaged ? dirname(app.getPath('exe')) : root), '测功数据');
const updater = portableRoot ? new UpdateService({root:portableRoot,version:app.getVersion(),fetcher:(url,options)=>net.fetch(url,{...options,bypassCustomProtocolHandlers:true})}) : null;
let deviceActive=false,updateRequested=false,checkingUpdate=false,lastNotifiedVersion='';
const archive = new DataArchive(dataDirectory,async run=>{
  const {renderCharacteristic}=await import(pathToFileURL(resolve(site,'export/characteristic.mjs')).href);
  return renderCharacteristic(run);
});
const boundsFile = resolve(app.getPath('userData'), 'window.json');
let win, blocker, closing = false, allowClose = false;

function isApp(url) {
  try { return new URL(url).origin === origin; } catch { return false; }
}

function activity(active) {
  deviceActive=active;
  if (active && blocker === undefined) blocker = powerSaveBlocker.start('prevent-app-suspension');
  if (!active && blocker !== undefined) { powerSaveBlocker.stop(blocker); blocker = undefined; }
}

async function createWindow() {
  const area = screen.getPrimaryDisplay().workArea;
  let saved = {};
  try { saved = JSON.parse(await readFile(boundsFile, 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT' && !(error instanceof SyntaxError)) console.error(error); }
  const width = Math.min(area.width, Math.max(720, Number(saved.width) || 1440));
  const height = Math.min(area.height, Math.max(480, Number(saved.height) || 960));
  win = new BrowserWindow({
    width, height, minWidth: Math.min(720, area.width), minHeight: Math.min(480, area.height),
    title: `Inntorque 测功机 ${app.getVersion()}`, icon: resolve(root, 'icon.png'), backgroundColor: '#f2f4f7', show: false,
    webPreferences: {preload: resolve(root, 'preload.cjs'), sandbox: true, contextIsolation: true,
      nodeIntegration: false, backgroundThrottling: false, zoomFactor: 1},
  });
  win.webContents.setWindowOpenHandler(() => ({action: 'deny'}));
  win.webContents.on('will-navigate', (event, url) => { if (url !== entry) event.preventDefault(); });
  win.on('page-title-updated', event => event.preventDefault());
  win.webContents.on('before-input-event', (event, input) => {
    if (input.key === 'F5' || (input.control || input.meta) && ['r', '+', '-', '=', '0'].includes(input.key.toLowerCase())) event.preventDefault();
  });
  win.webContents.on('zoom-changed', () => win.webContents.setZoomFactor(1));
  win.on('close', event => {
    if (allowClose) return;
    event.preventDefault();
    if (closing) return;
    closing = true;
    void (async () => {
      const bounds = {...win.getNormalBounds(), maximized: win.isMaximized()};
      await writeFile(boundsFile, JSON.stringify(bounds));
      if(updateRequested) {
        const result=await win.webContents.executeJavaScript("fetch('/api/desktop/update-ready').then(r=>r.json())");
        if(!result.ready)throw new Error(result.error||'请先结束试验并关闭设备输出，再执行更新');
        await win.webContents.executeJavaScript('document.body.inert=true');
      }
      const ready = await win.webContents.executeJavaScript('window.inntorqueCanClose ? window.inntorqueCanClose(true) : true');
      if (!ready) throw new Error('退出前数据保存未完成');
      if(updateRequested)await updater.requestApply();
      allowClose = true;
      win.close();
    })().catch(async error => {
      if(updateRequested){updateRequested=false;await win.webContents.executeJavaScript("document.body.inert=false;fetch('/api/desktop/update-cancel')").catch(()=>{});}
      void dialog.showMessageBox(win, {type: 'error', message: '退出前保存未完成', detail: error.message}); })
      .finally(() => { closing = false; });
  });
  win.once('ready-to-show', () => {
    if (saved.maximized !== false) win.maximize();
    win.show();
  });
  win.webContents.on('render-process-gone', (_event, details) => {
    activity(false);
    void dialog.showMessageBox(win, {type: 'error', title: '测功机界面已停止',
      message: '界面执行器已退出，请检查设备实际输出。', detail: `退出原因：${details.reason}。重新打开应用后，按界面提示确认设备状态。`});
  });
  await win.loadURL(entry);
  await win.webContents.setVisualZoomLevelLimits(1, 1);
  win.webContents.setZoomFactor(1);
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.show(); win.focus(); } });
  app.on('window-all-closed', () => app.quit());
  app.whenReady().then(async () => {
    await mkdir(app.getPath('userData'), {recursive: true});
    const ses = session.defaultSession;
    ses.protocol.handle('https', async request => {
      const url = new URL(request.url);
      if (!isApp(url) || request.method !== 'GET') return new Response('Forbidden', {status: 403});
      let pathname;
      try { pathname = decodeURIComponent(url.pathname); } catch { return new Response('Bad path', {status: 400}); }
      if (!pathname.startsWith('/apps/dynamometer/') && !pathname.startsWith('/browser/')) return new Response('Not found', {status: 404});
      const file = resolve(site, '.' + pathname + (pathname.endsWith('/') ? 'index.html' : ''));
      if (!file.startsWith(site + sep)) return new Response('Forbidden', {status: 403});
      try {
        const response = await net.fetch(pathToFileURL(file).href);
        if (pathname.endsWith('/') || pathname.endsWith('.html')) response.headers.set('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' blob:; worker-src 'self'; object-src 'none'; frame-src 'none'");
        return response;
      } catch { return new Response('Not found', {status: 404}); }
    });
    const allowed = (contents, permission, url) => contents === win?.webContents && isApp(url) && ['serial', 'usb'].includes(permission);
    ses.setPermissionCheckHandler((contents, permission, url) => allowed(contents, permission, url));
    ses.setPermissionRequestHandler((contents, permission, callback, details) => callback(allowed(contents, permission, details.requestingUrl)));
    ses.setDevicePermissionHandler(details => isApp(details.origin) && ['serial', 'usb'].includes(details.deviceType));
    ipcMain.on('desktop:activity', (event, active) => {
      if (event.sender === win?.webContents && event.senderFrame === win.webContents.mainFrame && isApp(event.senderFrame.url) && typeof active === 'boolean') activity(active);
    });
    const localRequest = event => event.sender === win?.webContents && event.senderFrame === win.webContents.mainFrame && isApp(event.senderFrame.url);
    ipcMain.handle('desktop:save-data', (event,packet) => {
      if (!localRequest(event)) throw new Error('不允许访问数据目录');
      return archive.write(packet);
    });
    const openDataFolder = async () => {
      await mkdir(dataDirectory,{recursive:true});
      const error = await shell.openPath(dataDirectory);
      if (error) throw new Error(error);
    };
    ipcMain.handle('desktop:ready',async event=>{
      if(!localRequest(event))throw new Error('不允许确认启动');
      const token=process.env.INNTORQUE_BOOT_TOKEN;
      if(portableRoot&&/^[a-f0-9]{48}$/.test(token||'')) {
        await mkdir(resolve(portableRoot,'updates'),{recursive:true});
        await writeFile(resolve(portableRoot,'updates',`health-${token}.json`),JSON.stringify({token,version:app.getVersion()}));
      }
    });
    ipcMain.handle('desktop:data-directory', event => {if (!localRequest(event)) throw new Error('不允许访问数据目录');return dataDirectory;});
    ipcMain.handle('desktop:open-data', event => {if (!localRequest(event)) throw new Error('不允许访问数据目录');return openDataFolder();});
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      {label: '文件', submenu: [
        {label: '打开数据目录', click: () => { void openDataFolder().catch(error => dialog.showErrorBox('无法打开数据目录',error.message)); }},
        {label: '打开导出文件夹', click: () => { void shell.openPath(app.getPath('downloads')); }},
        {label: '打开应用数据文件夹', click: () => { void shell.openPath(app.getPath('userData')); }},
        {type: 'separator'}, {label: '退出', accelerator: 'Alt+F4', click: () => win.close()},
      ]},
      {label: '窗口', submenu: [{label: '最小化', role: 'minimize'}, {label: '全屏', role: 'togglefullscreen'}]},
      {label: '帮助', submenu: [{id:'check-update',label:'检查更新',click:()=>{void checkUpdate(true);}}, {label: '关于测功机', click: () => { void dialog.showMessageBox(win, {
        title: 'Inntorque 测功机', message: `Inntorque 测功机 ${app.getVersion()}`,
        detail: '本地桌面上位机 · 设备直连 · 试验记录保存在本机。\n应用自动发现本机设备接口，请核对配置后连接。原浏览器记录仍保留在原浏览器中。',
      }); }}]},
    ]));
    await createWindow();
    if(updater&&!process.env.INNTORQUE_TEST_PROFILE){
      setTimeout(()=>{void checkUpdate(false);},10000).unref();
      setInterval(()=>{void checkUpdate(false);},10*60*1000).unref();
      try {const previous=JSON.parse(await readFile(resolve(portableRoot,'updates/last-error.json'),'utf8'));
        await dialog.showMessageBox(win,{type:'warning',message:'上次启动或更新未完成，已保留可用版本',detail:previous.error});
        await rm(resolve(portableRoot,'updates/last-error.json'),{force:true});
      } catch(error){if(error.code!=='ENOENT')console.error(error);}
    }
  }).catch(error => { dialog.showErrorBox('测功机启动失败', error.stack || error.message); app.quit(); });
}

async function checkUpdate(manual) {
  if(checkingUpdate)return;
  if(!updater){if(manual)await dialog.showMessageBox(win,{message:'在线更新由便携启动器管理',detail:'请使用软件根目录的测功机启动入口。开发运行不执行更新。'});return;}
  checkingUpdate=true;
  const menu=Menu.getApplicationMenu()?.getMenuItemById('check-update');
  try {
    const staged=await updater.staged();
    const release=staged || await updater.check();
    if(win.isDestroyed()||closing)return;
    if(!release){if(menu)menu.label='检查更新';if(manual)await dialog.showMessageBox(win,{message:'当前已是最新发布版本'});return;}
    if(menu)menu.label=`发现新版 ${release.version} · 检查更新`;
    if(!manual&&(deviceActive||lastNotifiedVersion===release.version))return;
    lastNotifiedVersion=release.version;
    if(!staged){
      const choice=await dialog.showMessageBox(win,{message:`发现新版 ${release.version}`,detail:release.notes||'双端桌面上位机更新',buttons:['下载更新','稍后'],defaultId:1,cancelId:1});
      if(choice.response!==0)return;
      await updater.stage(progress=>{if(!win.isDestroyed())win.setProgressBar(progress);});
      if(win.isDestroyed()||closing)return;
      win.setProgressBar(-1);
    }
    const ready=await dialog.showMessageBox(win,{message:'新版已下载并校验',detail:'结束试验、关闭设备输出并保存当前数据后，重启到新版。',buttons:['保存并重启更新','稍后'],defaultId:1,cancelId:1});
    if(ready.response===0){updateRequested=true;win.close();}
  } catch(error){if(manual&&!win.isDestroyed())await dialog.showMessageBox(win,{type:'error',message:'更新检查或下载未完成',detail:error.message});else console.error('Update:',error.message);}
  finally {if(!win.isDestroyed())win.setProgressBar(-1);checkingUpdate=false;}
}
