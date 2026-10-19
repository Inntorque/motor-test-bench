import React, { useEffect, useRef, useState } from 'react';
import { motorTemperatureC, motorTargetCurrentA, motorOutputFeedback, motorDeviceState, motorStepInputError } from './model.js';

const FORM_KEY = 'motor-bench:current-step:form';
function savedForm() {
  const empty = { stepA: '', intervalS: '', durationS: '' };
  try {
    const saved = JSON.parse(localStorage.getItem(FORM_KEY) || '{}');
    return Object.fromEntries(Object.keys(empty).map(key => [key, String(saved?.[key] ?? '')]));
  } catch { return empty; }
}

function MotorCurrentPanel({ token, bridgeReady, onState, onEvent }) {
  const [device, setDevice] = useState({ connected: false, run: {} });
  const [form, setForm] = useState(savedForm);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [actionMessage, setActionMessage] = useState('');
  const [stopping, setStopping] = useState(false);
  const stopInFlight = useRef(false);
  const actionEpoch = useRef(0);
  const pendingActions = useRef(0);
  const callback = useRef(onState);
  callback.current = onState;
  const snapshot = device.connected ? device.snapshot : null;
  const run = device.run || {};
  const feedback = snapshot?.feedback;
  const temperature = motorTemperatureC(device);
  const outputFeedback = motorOutputFeedback(device);
  const ntc = snapshot?.ntc;
  const temperatureStatus = !device.connected ? '电机未连接' : ntc?.fault ? 'NTC 故障'
    : temperature == null ? '温度无效或已过期'
    : ntc?.over || ntc?.tripped || ntc?.tripped_latched ? '过温保护' : 'NTC 实时反馈';
  const show = (value) => Number.isFinite(value) ? value.toFixed(3) : '—';
  const step = Number(form.stepA), interval = Number(form.intervalS), duration = Number(form.durationS);
  const inputError = motorStepInputError(form, snapshot?.limitA || 5);
  useEffect(() => {
    try { localStorage.setItem(FORM_KEY, JSON.stringify(form)); } catch { /* Storage may be unavailable. */ }
  }, [form]);

  useEffect(() => {
    let active = true, polling = false;
    async function poll() {
      if (polling) return;
      polling = true;
      const epoch = actionEpoch.current;
      try {
        const response = await fetch('/api/motor-current/status', { cache: 'no-store', signal: AbortSignal.timeout(2500) });
        if (!response.ok) throw new Error('电机通信桥不可用');
        const result = await response.json();
        if (active && epoch === actionEpoch.current && !pendingActions.current) { setDevice(result); callback.current(result); }
      } catch {
        if (active && epoch === actionEpoch.current && !pendingActions.current) { setDevice((previous) => ({ ...previous, connected: false, snapshot: null })); callback.current({ connected: false }); }
      } finally { polling = false; }
    }
    void poll();
    const timer = window.setInterval(poll, 250);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  async function action(name) {
    if (name === 'stop' && stopInFlight.current) return;
    if (name === 'start' && inputError) {
      setError(inputError); setActionMessage(''); return;
    }
    if (name === 'stop') { stopInFlight.current = true; setStopping(true); }
    actionEpoch.current++; pendingActions.current++;
    setBusy(true);
    setError('');
    setActionMessage(name === 'stop' ? '正在发送停止指令，等待设备确认…' : name === 'start' ? '正在请求启动电流步进…' : '正在连接设备…');
    try {
      // Obtain the current bridge token after service restarts, for every action.
      {
        const health = await fetch('/api/health', {cache:'no-store', signal:AbortSignal.timeout(2500)});
        if (!health.ok) throw new Error('通信桥不可用，无法发送指令');
        token.current = (await health.json()).token;
      }
      const response = await fetch(`/api/motor-current/${name}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MotorBench-Token': token.current },
        body: JSON.stringify(name === 'start' ? { stepA: step, intervalS: interval, durationS: duration } : {}),
        signal: AbortSignal.timeout(12000),
      });
      const result = await response.json();
      setDevice(result); callback.current(result);
      if (!response.ok) throw new Error(result.error || '电流环操作失败');
      if (name === 'stop' && result.run?.stopConfirmed !== true) throw new Error('设备尚未确认停止，请再次停止或切断电机供电');
      setActionMessage(name === 'stop' ? `停止成功：设备已确认输出关闭（${new Date().toLocaleTimeString()}）`
        : name === 'start' ? '启动请求已接受，运行状态见下方。' : name === 'connect' ? '电机连接成功。' : '已断开连接。');
      onEvent({ connect: '电机电流环已连接', start: `电流步进启动：${step} A / 每 ${interval} s 一步 / ${duration} s`,
        stop: '电机停止输出已确认', disconnect: '电机工作台连接已断开' }[name]);
    } catch (failure) { setActionMessage(''); setError(failure.name === 'TimeoutError' ? '设备操作超时，未确认执行结果；可再次点击停止。' : failure.message); }
    finally { actionEpoch.current++; pendingActions.current--; setBusy(false); if (name === 'stop') { stopInFlight.current = false; setStopping(false); } }
  }

  return <section className="panel control-panel motor-current-panel" aria-label="电机电流步进控制">
    <div className="control-title"><div><h2>电机电流环</h2><span>IQ · CAN MODE 5</span></div>
      <span className={`hw-status ${device.connected ? 'is-connected' : ''}`}><i />{device.connected ? '已连接' : '未连接'}</span>
    </div>
    <p className="setpoint-hint">西格电机 · Iq 从 0 A 按步长递增，Id 目标固定 0 A。</p>
    {run.running && run.scriptId && <p className="hw-readback">专项自动脚本正在控制电机，定位/速度/电流模式按阶段切换；可在此停止输出。</p>}
    <div className="hw-connection-actions">
      <button className="button" disabled={!bridgeReady || busy || run.running} onClick={() => action('connect')}>连接电机</button>
      {device.connected && <button className="button" disabled={busy || run.running || snapshot?.motor?.output_on} onClick={() => action('disconnect')}>断开连接</button>}
    </div>
    {snapshot && <p className="hw-readback">{snapshot.adapter?.channel || 'CAN'} · 节点 {snapshot.node} · 固件 {snapshot.version}<br />设备电流上限：{snapshot.limitA ?? '—'} A</p>}
    <div className="hw-supply-readings motor-current-readings">
      <div><span>目标 Iq（设备反馈）</span><strong>{show(motorTargetCurrentA(device))}<small>A</small></strong></div>
      <div><span>实际 Iq</span><strong>{show(feedback?.stale ? null : feedback?.iq_a)}<small>A</small></strong></div>
      <div><span>实际 Id</span><strong>{show(feedback?.stale ? null : feedback?.id_a)}<small>A</small></strong></div>
      <div><span>电机温度</span><strong>{temperature == null ? '—' : temperature.toFixed(1)}<small>°C</small></strong><span>{temperatureStatus}</span></div>
      <div><span>电机状态</span><strong>{motorDeviceState(device)}</strong></div>
      <div><span>编码器输出角度</span><strong>{show(outputFeedback.positionDeg)}<small>°</small></strong><span>按参考工程减速比换算</span></div>
      <div><span>编码器输出转速</span><strong>{show(outputFeedback.speedRpm)}<small>rpm</small></strong></div>
    </div>
    <div className="hw-form-grid">
      {[
        ['stepA', '电流步长 A', '0.001', '0.001', snapshot?.limitA || 5],
        ['intervalS', '步进间隔 s', '0.1', '0.2', 10],
        ['durationS', '持续时间 s', '0.1', 1, 255],
      ].map(([key, label, increment, min, max]) => <label key={key}>{label}<input type="number" step={increment} min={min} max={max}
        value={form[key]} disabled={run.running || busy} placeholder="请输入" onChange={(event) => setForm((previous) => ({ ...previous, [key]: event.target.value }))} /></label>)}
    </div>
    <p className="hw-safety-note">步进间隔 0.2–10 s；持续时间 1–255 s。每隔指定秒数增加一次电流，达到设备上限后保持；保护、通信或页面心跳超时、到期都会停止。</p>
    {snapshot?.startBlockReason && !run.running && <p className="hw-inline-error">{snapshot.startBlockReason}</p>}
    <div className="hw-output-actions">
      <button className="button primary" disabled={busy || run.running} onClick={() => action('start')}>{run.running ? '电流步进执行中' : '开始电流步进'}</button>
      <button className="button danger" aria-busy={stopping} onClick={() => action('stop')}>{stopping ? '正在停止…' : '停止电机输出'}</button>
    </div>
    {actionMessage && <p className="hw-readback" role="status" aria-live="polite">{actionMessage}</p>}
    {(error || device.error) && <p className="hw-inline-error" role="alert">{error || device.error}</p>}
    {!error && inputError && !run.running && <p className="hw-safety-note">{inputError}</p>}
    <p className="hw-readback">{run.running ? run.scriptId ? '自动脚本运行中' : run.phase === 'starting' ? '正在启动电流环' : '步进运行中' : '无步进任务'} · 已步进 {run.steps || 0} 次 · {Number(run.elapsed || 0).toFixed(1)} s</p>
    {run.limitReached && <p className="hw-safety-note">已达到设备电流上限，保持当前目标，等待保护或持续时间结束。</p>}
    {Number.isFinite(run.peakIqA) && <p className="hw-readback">本次采集 Iq 峰值（幅值）：{show(run.peakIqA)} A</p>}
    {run.reason && <p className="hw-readback">结束原因：{run.reason}；末次目标 {show(run.lastTargetA)} A</p>}
    {run.stopConfirmed != null && <p className={run.stopConfirmed ? 'hw-readback' : 'hw-inline-error'}>{run.stopConfirmed ? '已收到新鲜的输出关闭确认' : '输出关闭未确认，请切断电机供电'}</p>}
  </section>;
}

export default React.memo(MotorCurrentPanel);
