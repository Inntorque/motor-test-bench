import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import { CharacteristicWindow, linearCharacteristic } from './characteristicData.js';
const tick = value => Number(value.toPrecision(4));

const SERIES = [
  { key: 'power', label: '输出轴功率', unit: 'W', upper: 500, color: '#1455c0' },
  { key: 'efficiency', label: '链路效率', unit: '%', upper: 100, color: '#087d3a' },
  { key: 'current', label: '目标 Iq', unit: 'A', upper: 25, color: '#c62828' },
  { key: 'speed', label: '输出转速', unit: 'rpm', upper: 110, color: '#b65b00' },
];

function TorqueCharacteristics({ samples, windowSeconds }) {
  const clipId = useId().replace(/:/g, '');
  const [torqueLimit, setTorqueLimit] = useState('1');
  const bound = Number.isFinite(Number(torqueLimit)) && Number(torqueLimit) > 0 ? Number(torqueLimit) : 1;
  const [lineMode, setLineMode] = useState('measured');
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    if (!expanded) return;
    const close = event => { if (event.key === 'Escape') setExpanded(false); };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [expanded]);
  const statistics = useRef(null);
  if (!statistics.current) statistics.current = new CharacteristicWindow();
  const { points } = useMemo(() => {
  const rows = statistics.current.update(samples, windowSeconds);
  const maxTorque = rows.reduce((max, row) => Math.max(max, row.torque / row.count), 0);
  const step = Math.max(.001, maxTorque / 600);
  const bins = new Map();
  for (const row of rows) {
    const key = Math.floor(row.torque / row.count / step);
    let bin = bins.get(key);
    if (!bin) { bin = { torque:0, count:0, sums:[0,0,0,0], counts:[0,0,0,0] }; bins.set(key,bin); }
    bin.torque+=row.torque; bin.count+=row.count;
    for (let i=0;i<4;i++) { bin.sums[i]+=row.sums[i]; bin.counts[i]+=row.counts[i]; }
  }
  const points = [...bins.entries()].sort((a,b)=>a[0]-b[0]).map(([,b]) => ({
    torque:b.torque/b.count,
    ...Object.fromEntries(SERIES.map((s,i)=>[s.key,b.counts[i] ? b.sums[i]/b.counts[i] : null])),
  }));
  return { points };
  }, [samples, windowSeconds]);
  const left = 250, right = 930, top = 65, bottom = 385;
  return <div className={`torque-characteristics ${expanded ? 'characteristic-expanded' : ''}`}>
    <div className="characteristic-tools"><button className="button small" onClick={() => setExpanded(!expanded)}>{expanded ? '退出放大' : '放大查看'}</button><span>固定量程 · 扭矩 {bound} N·m / 功率 500 W / 效率 100% / 目标 Iq 25 A / 转速 110 rpm</span></div>
    <div className="characteristic-tools"><label>扭矩横轴上限 N·m <input aria-label="扭矩横轴上限 N·m" type="number" min="0.01" step="0.1" value={torqueLimit} onChange={event => setTorqueLimit(event.target.value)} /></label><label>连线方式 <select aria-label="特性曲线连线方式" value={lineMode} onChange={event => setLineMode(event.target.value)}><option value="measured">实测连续折线</option><option value="linear">线性趋势（拟合）</option></select></label></div>
    <div className="characteristic-legend">{SERIES.map(s => <span key={s.key} style={{color:s.color}}>━ {s.label} {s.unit}</span>)}</div>
    <div className="characteristic-scroll"><svg viewBox="0 0 960 440" role="img" aria-label="扭矩特性曲线：功率、链路效率、目标电流和转速">
      <defs><clipPath id={clipId}><rect x={left} y={top} width={right-left} height={bottom-top} /></clipPath></defs>
      {Array.from({length:6},(_,i) => <g key={i}>
        <line x1={left} x2={right} y1={top+i*(bottom-top)/5} y2={top+i*(bottom-top)/5} stroke="#cbd5e1" />
        <line x1={left+i*(right-left)/5} x2={left+i*(right-left)/5} y1={top} y2={bottom} stroke="#cbd5e1" />
        <text x={left+i*(right-left)/5} y={bottom+23} textAnchor="middle">{tick(i*bound/5)}</text>
      </g>)}
      {SERIES.map((series,index) => {
        const upper = series.upper;
        const axis = 52 + index*60;
        let drawing = false, path = '';
        const curve = lineMode === 'linear' ? linearCharacteristic(points, series.key) : points;
        for (const p of curve) {
          const value = p[series.key];
          if (!Number.isFinite(value)) { drawing = false; continue; }
          path += `${drawing ? 'L' : 'M'}${(left+p.torque/bound*(right-left)).toFixed(2)},${(bottom-value/upper*(bottom-top)).toFixed(2)} `;
          drawing = true;
        }
        return <g key={series.key} fill={series.color}>
          <text x={axis} y={38} textAnchor="middle">{series.unit}</text>
          <line x1={axis} x2={axis} y1={top} y2={bottom} stroke={series.color}/>
          {Array.from({length:6},(_,i) => <text key={i} x={axis-6} y={bottom-i*(bottom-top)/5+4} textAnchor="end">{tick(i*upper/5)}</text>)}
          <g clipPath={`url(#${clipId})`}><path d={path} fill="none" stroke={series.color} strokeWidth="3.2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
          {curve.length === 1 && Number.isFinite(curve[0][series.key]) && <circle cx={left+curve[0].torque/bound*(right-left)} cy={bottom-curve[0][series.key]/upper*(bottom-top)} r="2.6"/>}</g>
        </g>;
      })}
      <text x={(left+right)/2} y={432} textAnchor="middle">输出扭矩幅值 / N·m</text>
    </svg></div>
    {points.some(p => p.torque > bound || SERIES.some(s => Number.isFinite(p[s.key]) && p[s.key] > s.upper)) && <p>有数据超出固定量程，图中仅显示量程内部分；原始采集数据保留。</p>}
    {points.length > 0 && <p className="panel-caption">当前实测扭矩覆盖：{tick(points[0].torque)}～{tick(points.at(-1).torque)} N·m。固定横轴为 0～{bound} N·m，曲线只显示实际覆盖区间。</p>}
    {lineMode === 'linear' && <p>当前显示线性拟合趋势，并非原始测量曲线；至少需要两个不同扭矩点，不向未测量区间外推。</p>}
    {!points.length && <p>接入真实扭矩传感器后显示特性曲线。</p>}
    <p className="panel-caption">最近 {windowSeconds} 秒真实数据按扭矩分箱取均值；横轴使用扭矩幅值。目标 Iq 非实测输入电流。链路效率 = 输出轴功率 / 电机电源输入功率 × 100%，缺少有效输入功率时留空。</p>
  </div>;
}

export default React.memo(TorqueCharacteristics);
