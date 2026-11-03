import {Connection} from './connection.js';
import {Bootloader,validateImage,sha256} from './n32.js';

const $ = id => document.getElementById(id),events = [];
let core,engine,manifest,image,lastRecord,busy = false,halted = false,choosing = false,choice = 0;
let connectionState = 'off',lastConnectionError = '';
const connection = new Connection(navigator.usb,state => {
  const previous = connectionState;connectionState = state.state;core = state.session?.core;
  $('device-state').textContent = {off:'不连接',waiting:'连接中，自动识别',connected:'已连接，持续识别'}[state.state];
  $('connection-detail').textContent = state.message + (state.state === 'waiting' ? `（第 ${state.attempts} 次）` : '');
  $('connection-detail').dataset.error = String(!!state.error);
  if (state.session) {
    const {probe,chip} = state.session,device = probe.device;
    $('identity').textContent = `${chip.model} · Flash ${chip.flashKiB} KiB · SRAM ${chip.sramKiB} KiB\nCPUID ${chip.cpuid} · DBG_ID ${chip.dbgId}\nJ-Link ${device.serialNumber || '无序列号'} · ${(probe.millivolts / 1000).toFixed(3)} V · SWD 100 kHz\n${probe.version}`;
  } else $('identity').textContent = state.state === 'off' ? '尚未读取芯片信息' : '芯片尚未识别，持续等待连接';
  if (state.error && state.message !== lastConnectionError) {log(state.message);lastConnectionError = state.message;}
  if (state.state !== previous) {
    if (state.state === 'connected') {log('J-Link 与 N32G455 识别成功；持续检查连接，未自动擦写');lastConnectionError = '';}
    else if (state.state === 'off') {log('已选择不连接；停止识别并释放本 APP 的探针');lastConnectionError = '';}
    else log('保持连接选择，正在自动识别 J-Link 与芯片');
  }
  buttons();
});
function log(message) {
  events.push({time:new Date().toISOString(),message});
  $('log').textContent = events.map(event => `${new Date(event.time).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}  ${event.message}`).join('\n');
  $('log').scrollTop = $('log').scrollHeight;
}
function update(message,value = 0) {$('stage').textContent = message;$('progress').value = value;}
function buttons() {
  $('connection-mode').disabled = busy;
  $('choose-device').disabled = busy || choosing || $('connection-mode').value !== 'on';
  $('flash').disabled = $('backup').disabled = busy || !core || !image || halted;
  $('resume').disabled = busy || !core || !halted || !!engine?.started;
  $('download-backup').disabled = !lastRecord?.before;
}
async function database() {
  return new Promise((resolve,reject) => {
    const request = indexedDB.open('inntorque-bootloader',1);
    request.onupgradeneeded = () => request.result.createObjectStore('records',{keyPath:'id'});
    request.onsuccess = () => resolve(request.result);request.onerror = () => reject(request.error);
  });
}
async function persist(record) {
  const db = await database();
  try {
    await new Promise((resolve,reject) => {
      const transaction = db.transaction('records','readwrite');transaction.objectStore('records').put(record);
      transaction.oncomplete = resolve;transaction.onerror = transaction.onabort = () => reject(transaction.error || new Error('备份保存失败'));
    });
  } finally {db.close();}
  lastRecord = record;$('saved').textContent = `最近备份：${record.id} · ${record.before.length.toLocaleString()} B · ${record.stage}`;buttons();
}
function download(data,name,type) {
  const url = URL.createObjectURL(new Blob([data],{type})),link = document.createElement('a');
  link.href = url;link.download = name;link.click();setTimeout(() => URL.revokeObjectURL(url),30000);
}
async function action(fn,hardware = false) {
  busy = true;$('stage').dataset.error = 'false';buttons();
  try {if (hardware) core = await connection.pause();await fn();}
  catch (error) {
    let message = error.message;
    if (error.name === 'NotFoundError') message = '未选择 J-Link';
    if (error.name === 'SecurityError' || error.name === 'NetworkError') message += '；请检查 USB 权限、驱动及是否被其他程序占用';
    log(message);update(message);$('stage').dataset.error = 'true';
  } finally {busy = false;if (hardware) connection.resume();buttons();}
}
async function chooseDevice() {
  if (choosing) return;
  const generation = ++choice;choosing = true;buttons();
  try {
    if (!navigator.usb || !window.isSecureContext) throw new Error('请使用 Chrome / Edge，通过 HTTPS 打开此 APP');
    const device = await navigator.usb.requestDevice({filters:[{vendorId:0x1366}]});
    if (generation === choice && $('connection-mode').value === 'on') connection.connect(device);
  } catch (error) {
    if (generation !== choice) return;
    const message = error.name === 'NotFoundError' ? '未选择 J-Link' : error.message;
    if (!connection.enabled) {$('connection-mode').value = 'off';connection.disconnect();}
    $('connection-detail').textContent = message;$('connection-detail').dataset.error = 'true';log(message);
  } finally {choosing = false;buttons();}
}
$('connection-mode').onchange = () => {
  if ($('connection-mode').value === 'off') {choice++;connection.disconnect();}
  else if (connection.selected) connection.connect(connection.selected);
  else chooseDevice();
};
$('choose-device').onclick = chooseDevice;
$('backup').onclick = () => action(async () => {
  engine = new Bootloader(core,update);halted = true;
  const {chip,before} = await engine.backup();
  await persist({id:new Date().toISOString(),chip,before,stage:'read-only',backupSha256:await sha256(before)});
  update('全片备份已保存；芯片保持暂停，可导出备份或恢复运行',1);log('仅读取全片备份，未擦写、未复位');
},true);
$('flash').onclick = () => action(async () => {
  engine = new Bootloader(core,update);halted = true;
  try {
    log('开始：校验镜像 → 暂停 → 全片备份 → 仅烧写 Boot → 全片回读 → 复位');
    const record = await engine.flash(image,manifest,persist);halted = false;
    log(`完成：${manifest.bytes} B Bootloader；全片回读一致；备份 SHA-256 ${record.backupSha256}`);
  } catch (error) {
    log(engine.started && !engine.verified ? '尚未完整验证；未自动复位，请保留并导出备份。' : '请查看最后完成阶段与操作日志。');
    throw error;
  }
},true);
$('resume').onclick = () => action(async () => {await core.resume();halted = false;update('已恢复原程序运行');log('操作者恢复原程序运行');},true);
$('download-backup').onclick = () => download(lastRecord.before,`N32G455-before-${lastRecord.id.replaceAll(':','-')}.bin`,'application/octet-stream');
$('download-log').onclick = () => {
  const record = lastRecord ? {...lastRecord,before:undefined} : null;
  download(JSON.stringify({manifest,record,events},null,2),'Bootloader-log.json','application/json');
};
addEventListener('beforeunload',event => {if (busy || halted) {event.preventDefault();event.returnValue = '';}});
navigator.usb?.addEventListener('disconnect',event => {
  connection.deviceChanged(event.device);
});
navigator.usb?.addEventListener('connect',event => connection.deviceChanged(event.device));
await action(async () => {
  const [metadata,binary] = await Promise.all([fetch('./firmware/manifest.json',{cache:'no-store'}),fetch('./firmware/boot.bin',{cache:'no-store'})]);
  if (!metadata.ok || !binary.ok) throw new Error('发布镜像加载失败');
  const data = new Uint8Array(await binary.arrayBuffer()),description = await metadata.json();await validateImage(data,description);
  manifest = description;image = data;$('release').textContent = manifest.release;$('image-size').textContent = `${image.length.toLocaleString()} B`;
  $('image-hash').textContent = manifest.sha256;$('image-state').textContent = '镜像校验通过';update('网页已就绪；连接状态见设备区');log('发布镜像校验通过；默认不连接，等待选择连接');
});
try {
  const db = await database();
  try {
    const record = await new Promise((resolve,reject) => {
      const request = db.transaction('records').objectStore('records').openCursor(null,'prev');
      request.onsuccess = () => resolve(request.result?.value);request.onerror = () => reject(request.error);
    });
    if (record) {lastRecord = record;$('saved').textContent = `最近备份：${record.id} · ${record.stage}`;buttons();}
  } finally {db.close();}
} catch (error) {log(`浏览器备份存储不可用：${error.message}；保存成功前不会擦写`);}
