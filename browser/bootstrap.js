import {DeviceAccess} from './hardware.js';
const script = document.currentScript;
const app = script.dataset.app;
window.INNTORQUE_BROWSER_DIRECT = true;
const workerUrl = new URL('worker.js',script.src);
workerUrl.search = new URL(script.src).search;
const worker = new Worker(workerUrl,{type:'module',name:`inntorque-${app}`});
const fetchNetwork = window.fetch.bind(window), NativeEventSource = window.EventSource;
const pending = new Map(), streams = new Set(); let nextRequest = 0,canDevice = '',node = app === 'dynamometer' ? 1 : 0;
let resolveReady,rejectReady;
const ready = new Promise((resolve,reject) => {resolveReady = resolve;rejectReady = reject;});
const bar = document.createElement('div'); bar.id = 'inntorque-browser-access';
bar.innerHTML = '<a href="../../">返回总网关</a><span>浏览器直接连接</span><button type="button" data-serial>授权串口</button><button type="button" data-usb>授权 USB-CAN</button><label>CAN <select aria-label="CAN适配器"><option value="">自动选择</option></select></label><label>节点 <input aria-label="CAN节点" type="number" min="0" max="63" style="width:3.2em"></label><span role="status">正在初始化…</span>';
const css = document.createElement('style');
css.textContent = '#inntorque-browser-access{position:relative;z-index:20;display:flex;align-items:center;flex-wrap:wrap;gap:10px;padding:8px 16px;background:#fff;border-bottom:1px solid #e1e8ed;color:#425466;font:13px system-ui,sans-serif}#inntorque-browser-access button,#inntorque-browser-access select,#inntorque-browser-access input{font:inherit;color:inherit;padding:5px 8px;border:1px solid #cfd9e2;border-radius:6px;background:#fff}#inntorque-browser-access a{color:#436b82;text-decoration:none}#inntorque-browser-access [role=status]{margin-left:auto;font-size:12px}';
document.head.append(css);
const status = bar.querySelector('[role=status]'), select = bar.querySelector('select'), nodeInput = bar.querySelector('input'); nodeInput.value = node;
bar.querySelector('[data-serial]').title = 'CANable2（SLCAN）、ZQWL 与 USB-RS485 均使用串口授权';
bar.querySelector('[data-usb]').title = '仅 gs_usb / candleLight；CANable2 串口固件请使用“授权串口”';
select.onchange = () => {canDevice = select.value;}; nodeInput.onchange = () => {node = Number(nodeInput.value);};
const access = new DeviceAccess((message,transfer) => worker.postMessage(message,transfer || []),() => {
  const previous = select.value;
  select.replaceChildren(new Option('自动选择',''),...access.ports().map(port => new Option(port.description,port.port)));
  if ([...select.options].some(option => option.value === previous)) select.value = previous;
  window.dispatchEvent(new Event('inntorque-ports-changed'));
});
for (const [selector,authorize] of [['[data-serial]',() => access.authorizeSerial()],['[data-usb]',() => access.authorizeUsb()]]) {
  bar.querySelector(selector).onclick = async () => {
    try {const id = await authorize();status.textContent = '已授权，请选择对应接口并点击连接/重连';if (access.ports().find(port => port.port === id)?.canInterface) {canDevice = id;select.value = id;}}
    catch (error) {status.textContent = error.name === 'NotFoundError' ? '未选择设备；CANable2 / ZQWL 请使用“授权串口”' : error.message;}
  };
}
const mount = () => document.body.prepend(bar);
if (document.body) mount(); else document.addEventListener('DOMContentLoaded',mount,{once:true});
worker.addEventListener('message',async event => {
  const message = event.data;
  if (message.kind === 'io-call') {
    try {const value = message.method === 'can-config' ? {device:canDevice,node} : await access.call(message.method,message.args);worker.postMessage({kind:'io-result',id:message.id,value});}
    catch (error) {worker.postMessage({kind:'io-result',id:message.id,error:error.message});}
  } else if (message.kind === 'ready') {status.textContent = '网页已就绪 · 无需本机服务';resolveReady();}
  else if (message.kind === 'fatal') {status.textContent = message.error;rejectReady(new Error(message.error));}
  else if (message.kind === 'response') {
    const item = pending.get(message.id); if (!item) return;
    pending.delete(message.id);item.cleanup?.();
    const result = message.result;
    const headers = {'Content-Type':result?.file ? result.type : 'application/json','X-Control-Epoch':String(message.epoch || 0),'X-Command-Epoch':String(message.epoch || 0)};
    item.resolve(new Response(result?.file ? result.body : JSON.stringify(result),{status:message.status,headers}));
  } else if (message.kind === 'event') {
    if (message.event === 'activity') bar.dataset.active = String(message.data.active);
    if (message.event === 'download') {
      const url = URL.createObjectURL(new Blob([message.data.body],{type:message.data.type})), link = document.createElement('a');
      link.href = url; link.download = message.data.name; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
    }
    for (const stream of streams) stream.dispatchEvent(new MessageEvent(message.event,{data:JSON.stringify({type:message.event,data:message.data})}));
    if (message.event === 'state') {
      const safety = message.data.control;
      bar.dataset.active = String(!!message.data.test?.running || !!message.data.devices?.['current-motor']?.snapshot?.motor?.output_on || Object.values(message.data.devices || {}).some(device => device.snapshot?.status & 128));
      if (safety?.blocked) status.textContent = safety.reason || '输出关闭未确认';
    }
  }
});
worker.addEventListener('error',event => {status.textContent = '浏览器执行器异常：' + event.message;rejectReady(new Error(event.message));});
window.fetch = (input,options = {}) => {
  const url = new URL(input instanceof Request ? input.url : input,location.href);
  if (url.origin !== location.origin || !url.pathname.startsWith('/api/')) return fetchNetwork(input,options);
  return new Promise((resolve,reject) => {
    const id = ++nextRequest, signal = options.signal;
    if (signal?.aborted) {reject(signal.reason);return;}
    const abort = () => {pending.delete(id);reject(signal.reason || new DOMException('Aborted','AbortError'));};
    signal?.addEventListener('abort',abort,{once:true});
    pending.set(id,{resolve,reject,cleanup:() => signal?.removeEventListener('abort',abort)});
    const method = (options.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    let body = {}; try {body = options.body ? JSON.parse(options.body) : {};} catch {}
    ready.then(() => {if (pending.has(id)) worker.postMessage({kind:'request',id,path:url.pathname + url.search,method,body,headers:Object.fromEntries(new Headers(options.headers || (input instanceof Request ? input.headers : {})))});},error => {pending.delete(id);reject(error);});
  });
};
window.EventSource = class BrowserEventSource extends EventTarget {
  static CONNECTING = 0; static OPEN = 1; static CLOSED = 2;
  constructor(url,options) {
    super();
    if (new URL(url,location.href).pathname !== '/api/v1/events') return new NativeEventSource(url,options);
    this.url = url; this.readyState = 0; streams.add(this);
    ready.then(() => {if (this.readyState === 2) return;this.readyState = 1;this.dispatchEvent(new Event('open'));},() => this.dispatchEvent(new Event('error')));
  }
  close() {this.readyState = 2;streams.delete(this);}
};
async function downloadLink(link) {
  try {
    const response = await window.fetch(link.href), blob = await response.blob();
    if (!response.ok) throw new Error(await blob.text());
    const url = URL.createObjectURL(blob), download = document.createElement('a');
    download.href = url;download.download = link.getAttribute('download') || new URL(link.href).pathname.split('/').at(-1);download.click();setTimeout(() => URL.revokeObjectURL(url),1000);
  } catch (error) {status.textContent = error.message;}
}
const clickAnchor = HTMLAnchorElement.prototype.click;
HTMLAnchorElement.prototype.click = function() {
  if (new URL(this.href,location.href).pathname.startsWith('/api/')) {void downloadLink(this);return;}
  return clickAnchor.call(this);
};
document.addEventListener('click',event => {
  const link = event.target.closest('a[href]');
  if (!link || !new URL(link.href,location.href).pathname.startsWith('/api/')) return;
  event.preventDefault();void downloadLink(link);
});
window.addEventListener('beforeunload',event => {if (bar.dataset.active === 'true') {event.preventDefault();event.returnValue = '';}});
worker.postMessage({kind:'init',app});
void access.initialize().catch(error => {status.textContent = error.message;});
if (app !== 'dynamometer') {
  const recordings = document.createElement('select');recordings.setAttribute('aria-label','运行记录');recordings.innerHTML = '<option value="">运行记录</option>';
  const refresh = async () => {
    const {records} = await (await fetch('/api/browser-recordings')).json(), previous = recordings.value;
    recordings.replaceChildren(new Option('运行记录',''),...records.map(report => new Option(new Date(report.startedAt * 1000).toLocaleString() + ` · 模式 ${report.mode}`,report.id)));
    if ([...recordings.options].some(option => option.value === previous)) recordings.value = previous;
  };
  recordings.addEventListener('focus',() => {void refresh();});bar.append(recordings);
  for (const [title,format] of [['CSV','run.csv'],['JSONL','timeline.jsonl'],['冻结轨迹','maximum_trace.csv']]) {
    const button = document.createElement('button');button.textContent = title;
    button.onclick = () => {
      if (!recordings.value) {status.textContent = '请先选择运行记录';return;}
      const link = document.createElement('a');link.href = `/api/browser-recordings/file?id=${recordings.value}&format=${format}`;link.download = recordings.value + '-' + format;void downloadLink(link);
    };bar.append(button);
  }
  void ready.then(refresh);
}
