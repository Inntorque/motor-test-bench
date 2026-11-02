const message = document.querySelector('#message');
const help = document.querySelector('#help');
try {
  const response = await fetch('../../catalog.json', {cache: 'no-store'});
  if (!response.ok) throw new Error('APP 清单加载失败');
  const catalog = await response.json();
  const app = catalog.apps.find(row => row.id === document.body.dataset.app);
  if (!app) throw new Error('此 APP 已从总网关移除');
  message.textContent = `正在打开${app.name}…`;
  const health = await fetch(`${catalog.serviceUrl}/status`, {signal: AbortSignal.timeout(8000), cache: 'no-store'});
  if (!health.ok) throw new Error('本机连接服务没有响应');
  const service = await health.json();
  if (service.app !== 'inntorque-local-services' || service.hostApi !== 1) throw new Error('本机连接服务需要更新');
  const opened = await fetch(`${catalog.serviceUrl}/open/${app.id}`, {
    method: 'POST', body: new URLSearchParams({token: service.token, version: app.version}),
  });
  const result = await opened.json();
  if (!opened.ok) throw new Error(result.error || 'APP 启动失败');
  window.location.assign(result.url);
} catch (error) {
  message.textContent = '暂时无法打开此 APP';
  document.querySelector('#detail').textContent = error.message;
  help.hidden = false;
}
