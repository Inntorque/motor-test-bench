import React, { useEffect, useMemo, useRef, useState } from "react";
import { appendSamples, plotColumns } from "./livePerformance.js";
import { useLiveActions } from "./useLiveActions.js";
import MotorCurrentPanel from "./MotorCurrentPanel.jsx";
import CoupledCurrentPanel from "./CoupledCurrentPanel.jsx";
import BrakeCurrentPanel from "./BrakeCurrentPanel.jsx";
import TorqueCharacteristics from "./TorqueCharacteristics.jsx";
import EncoderCompareView from "./EncoderCompareView.jsx";
import {LoadTrial} from './loadTrial.js';
import SpecializedTestsPanel from "./SpecializedTestsPanel.jsx";
import {supplyMeasurements, supplyPoint, supplyReadbackPoint, BRAKE_SUPPLY_CURRENT_LIMIT, MOTOR_SUPPLY_CURRENT_LIMIT, MOTOR_SUPPLY_PRESET_CURRENT_A, brakeModeText, supplyOperatingMode} from './supplyPolicy.js';
import {
  STORE_KEY,
  readRecords,
  saveRecords,
  mergeRecords,
  relativeSamples,
  eventCsv,
} from "./records.js";
import { toCsv, formatDuration, shaftPowerKw as calculateShaftPowerKw, motorTemperatureC, motorTargetCurrentA, motorLiveCurrents, characteristicAxisMax } from "./model.js";

const MAX_SAMPLES = 1500;
const SENSOR_POLL_MS = 50;
const LIVE_SAMPLE_LIMIT = 60000;
const CHANNELS = [
  {
    key: "motorConnected",
    name: "电机驱动板",
    icon: "motor",
    detail: "CAN · 西格电机 Iq 电流环",
  },
  {
    key: "sensorConnected",
    name: "动态扭矩传感器",
    icon: "sensor",
    detail: "独立 RS485 · 只读测量",
  },
  {
    key: "supplyConnected",
    name: "制动电源",
    icon: "power",
    detail: "Modbus RTU · 制动负载",
  },
  {
    key: "motorSupplyConnected",
    name: "电机电源",
    icon: "power",
    detail: "SSJ / 万瑞达 RU · Modbus RTU",
  },
];

const SUPPLIES = [
  { id: "motor-supply", title: "电机电源", role: "驱动侧供电", icon: "motor", stateKey: "motorSupplyConnected" },
  { id: "brake-supply", title: "制动电源", role: "制动线圈加载", icon: "power", stateKey: "supplyConnected" },
];
const DEFAULT_CONFIG = {
  transport: "serial", device: "", host: "", tcpPort: "", baud: "9600",
  address: "1", parity: "N", stopbits: "1", voltageDecimals: "2",
  currentDecimals: "2", ratedVoltage: "", ratedCurrent: "",
  maxVoltage: "", maxCurrent: "",
};
const DEFAULT_SENSOR_CONFIG = { device: "", protocol: "modbus", baud: "19200", address: "1", stopbits: "2", torqueDecimals: "2", speedDecimals: "1" };

function initialBenchState() {
  return {
    range: 100, t: 0, torque: null, speed: null, current: null,
    targetSpeed: null, targetCurrent: null, motorEnabled: false,
    loadEnabled: null, motorConnected: false, sensorConnected: false,
    supplyConnected: false, motorSupplyConnected: false,
    motorSupplyVoltage: null, motorSupplyCurrent: null, motorSupplyPower: null,
    brakeSupplyVoltage: null, brakeSupplyCurrent: null, brakeSupplyPower: null,
  };
}

function loadConfig(id) {
  const motor=id === "motor-supply";
  const defaults={ ...DEFAULT_CONFIG, protocol:motor?"ru":"ssj", currentDecimals:motor?"2":"3",
    maxVoltage:"24", maxCurrent:motor?String(MOTOR_SUPPLY_CURRENT_LIMIT):String(BRAKE_SUPPLY_CURRENT_LIMIT) };
  try {
    const saved=JSON.parse(localStorage.getItem(`motor-bench:${id}:config`) || "{}");
    const next={ ...defaults, ...saved };
    // Switch old browser profiles once; later manual protocol choices persist.
    if(motor && saved.protocolDefaultRevision!=="ru-20261021" && saved.protocol!=="ru") {
      Object.assign(next,{protocol:"ru",baud:"9600",parity:"N",stopbits:"1",address:"1",
        voltageDecimals:"2",currentDecimals:"2",measuredCurrentDecimals:"2",
        ratedVoltage:"",ratedCurrent:"",ratingsVerified:false});
    }
    if(motor)next.protocolDefaultRevision="ru-20261021";
    // SSJ keeps its independently confirmed current scaling.
    if(id === "brake-supply") { next.protocol="ssj"; next.currentDecimals="3"; }
    if(id === "motor-supply" && next.protocol === "ssj") next.currentDecimals="3";
    next.maxVoltage="24";
    next.maxCurrent=id === "motor-supply"?String(MOTOR_SUPPLY_CURRENT_LIMIT):String(BRAKE_SUPPLY_CURRENT_LIMIT);
    return next;
  } catch {
    return { ...defaults, ...(motor?{protocolDefaultRevision:"ru-20261021"}:{}) };
  }
}

function loadSensorConfig() {
  try {
    return { ...DEFAULT_SENSOR_CONFIG, ...JSON.parse(localStorage.getItem("motor-bench:torque-sensor:config") || "{}") };
  } catch {
    return { ...DEFAULT_SENSOR_CONFIG };
  }
}

function numberText(value, decimals = 2) {
  return Number.isFinite(value) ? value.toFixed(decimals) : "—";
}

const snapshotValues = supplyMeasurements;

function Icon({ name, size = 20, ...props }) {
  const paths = {
    bench: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1.5" />
        <rect x="14" y="3" width="7" height="7" rx="1.5" />
        <rect x="3" y="14" width="7" height="7" rx="1.5" />
        <rect x="14" y="14" width="7" height="7" rx="1.5" />
      </>
    ),
    records: (
      <>
        <path d="M7 3h8l4 4v14H5V3h2Z" />
        <path d="M14 3v5h5M8 12h8M8 16h6" />
      </>
    ),
    settings: (
      <>
        <path d="M4 7h16M4 17h16" />
        <circle cx="9" cy="7" r="3" />
        <circle cx="15" cy="17" r="3" />
      </>
    ),
    motor: (
      <>
        <rect x="5" y="6" width="14" height="12" rx="3" />
        <path d="M1 10h4m14 2h4M8 3v3m8-3v3M8 18v3m8-3v3M9 10v4m3-4v4m3-4v4" />
      </>
    ),
    sensor: (
      <>
        <path d="M2 12h5m10 0h5" />
        <circle cx="12" cy="12" r="5" />
        <path d="m9 15 6-6" />
      </>
    ),
    power: (
      <>
        <path d="M12 2v9M6.5 5.5a9 9 0 1 0 11 0" />
      </>
    ),
    flask: (
      <>
        <path d="M9 3h6m-5 0v6L4 19a1.4 1.4 0 0 0 1 2h14a1.4 1.4 0 0 0 1-2L14 9V3M7 15h10" />
      </>
    ),
    play: <path d="m8 4 12 8-12 8Z" />,
    pause: (
      <>
        <path d="M8 4v16M16 4v16" />
      </>
    ),
    stop: <rect x="5" y="5" width="14" height="14" rx="2" />,
    download: (
      <>
        <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" />
      </>
    ),
    chevron: <path d="m8 10 4 4 4-4" />,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    check: <path d="m5 12 4 4L19 6" />,
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
    info: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 11v6M12 7v.5" />
      </>
    ),
    pulse: <path d="M2 12h5l3-8 4 16 3-8h5" />,
    plus: <path d="M12 5v14M5 12h14" />,
    minus: <path d="M5 12h14" />,
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {paths[name] || paths.info}
    </svg>
  );
}

function PlotView({
  samples,
  metric,
  range,
  paused,
  empty,
  onConnect,
  windowSeconds,
}) {
  const svgRef = useRef(null);
  const [plotSize, setPlotSize] = useState({ width: 900, height: 220 });
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => {
      setPlotSize({
        width: Math.max(280, entry.contentRect.width),
        height: Math.max(140, entry.contentRect.height),
      });
    });
    observer.observe(svgRef.current);
    return () => observer.disconnect();
  }, []);
  const meta = {
    torque: {
      label: "减速箱输出扭矩",
      unit: "N·m",
      color: "#3479df",
      max: range,
    },
    speed: {
      label: "输出转速", unit: "rpm", color: "#318978",
      max: 10,
    },
    shaftPowerKw: {
      label: "计算轴功率幅值", unit: "kW", color: "#ab7651",
      max: 10,
    },
    current: {
      label: "电机输出目标电流", unit: "A", color: "#d28131",
      max: 10,
    },
  }[metric];

  const { width, height } = plotSize;
  const tickCount = width < 500 ? 3 : 6;
  const left = 48,
    right = 24,
    top = 20,
    bottom = 36;
  const end = Math.max(windowSeconds, samples.at(-1)?.t || 0),
    start = end - windowSeconds;
  const { peak, plotted } = useMemo(() => plotColumns(samples, metric, start, windowSeconds,
    width - left - right), [samples, metric, start, windowSeconds, width]);
  meta.max = characteristicAxisMax(peak);
  const minimum = metric === "torque" ? -meta.max : 0;
  let path = "",
    drawing = false;
  for (const sample of plotted) {
    const value = sample[metric];
    if (!Number.isFinite(value)) {
      drawing = false;
      continue;
    }
    const x =
      left + ((sample.t - start) / windowSeconds) * (width - left - right);
    const y =
      top +
      (1 - Math.min(1, Math.max(0, (value - minimum) / (meta.max - minimum)))) *
        (height - top - bottom);
    path += `${drawing ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)} `;
    drawing = true;
  }
  return (
    <div className="plot-wrap">
      <svg
        ref={svgRef}
        className="plot"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${meta.label}实时采集曲线，单位${meta.unit}${paused ? "，视图已暂停" : ""}`}
      >
        {[0, 0.25, 0.5, 0.75, 1].map((v) => {
          const y = top + v * (height - top - bottom);
          return (
            <g key={v}>
              <line
                x1={left}
                y1={y}
                x2={width - right}
                y2={y}
                stroke="#e7ecf2"
                strokeDasharray="4 5"
              />
              <text x={left - 14} y={y + 4} textAnchor="end">
                {Number((meta.max - v * (meta.max - minimum)).toFixed(1))}
              </text>
            </g>
          );
        })}
        {Array.from({ length: tickCount + 1 }, (_, v) => v).map((v) => {
          const x = left + (v / tickCount) * (width - left - right);
          return (
            <g key={v}>
              <line
                x1={x}
                y1={top}
                x2={x}
                y2={height - bottom}
                stroke="#f1f3f6"
              />
              <text x={x} y={height - 11} textAnchor="middle">
                {windowSeconds < 10
                  ? `${(start + (v / tickCount) * windowSeconds).toFixed(1)} s`
                  : formatDuration(start + (v / tickCount) * windowSeconds)}
              </text>
            </g>
          );
        })}
        <path
          d={path}
          fill="none"
          stroke={meta.color}
          strokeWidth="2.5"
          strokeLinejoin="round"
        />
      </svg>
      {empty && (
        <div className="plot-empty">
          <span>暂无真实采集数据</span>
          <p>接入并读取实际设备后，这里显示扭矩、转速、计算轴功率和制动电流曲线；未接入的量保持为空。</p>
          <button
            className="button primary"
            onClick={onConnect}
          >
            <Icon name="settings" size={16} />
            前往电源控制
          </button>
        </div>
      )}
    </div>
  );
}

const Plot = React.memo(PlotView);

const SupplyPanel = React.memo(function SupplyPanel({
  supply, device, config, setConfig, point, setPoint, ports, bridgeReady,
  busy, onRefreshPorts, onConnect, onDisconnect, onRead, onSet, onOutput, onLimits, token, onDeviceState, onEvent,
}) {
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(`motor-bench:${supply.id}:collapsed`) === "true"; }
    catch { return false; }
  });
  const values = snapshotValues(device);
  const ru = config.protocol === "ru";
  const motorSupply = supply.id === 'motor-supply';
  const settings = device?.snapshot?.settings;
  const validPoint = [point.voltage, point.current].every((value) => value.trim() !== "" && Number.isFinite(Number(value)) && Number(value) >= 0);
  const under = (value, cap) => cap == null || cap === "" || Number(value) <= Number(cap);
  const withinLimits = under(point.voltage,config.ratedVoltage) && under(point.current,config.ratedCurrent) &&
    under(point.voltage,config.maxVoltage) && under(point.current,config.maxCurrent) && (motorSupply || Number(point.current)<=BRAKE_SUPPLY_CURRENT_LIMIT);
  const registerSafe = Number(point.voltage) * 10 ** Number(config.voltageDecimals) <= 65535 &&
    Number(point.current) * 10 ** Number(config.currentDecimals) <= 65535;
  const outputOn = Boolean(device?.snapshot?.status & 0x80);
  const mayBeOn=outputOn||device?.outputUncertain||(!device?.connected&&Boolean(device?.config?.device));
  const locked = device?.connected || busy;
  const patchConfig = (patch) => setConfig({ ...config, ...patch });
  const patchPoint = (patch) => setPoint({ ...point, ...patch });
  const voltageScale = 10 ** Number(config.voltageDecimals || 0);
  const currentScale = 10 ** Number(config.currentDecimals || 0);
  const brakeApplied = device?.connected && settings?.[1] === 24*voltageScale && settings?.[2] === Math.round(Number(point.current)*currentScale);
  const outputControls = <div className="hw-output-controls">
          {!motorSupply && <label className="hw-current-field">制动电流 A<input aria-label="制动电源目标电流 A" type="number" min="0" max={BRAKE_SUPPLY_CURRENT_LIMIT} step="any" value={point.current} disabled={!device?.connected || busy} onChange={(e) => patchPoint({ current: e.target.value })} /></label>}
          {!withinLimits && <p className="hw-inline-error">设定值超过电源额定范围或工作台最大限幅。</p>}
          {device?.snapshot?.protection && <p className="hw-readback">设备保护读回：OVP {device.snapshot.protection.voltage} V（{device.snapshot.protection.ovpEnabled ? "已启用" : "未启用"}） / OCP {device.snapshot.protection.current} A（{device.snapshot.protection.ocpEnabled ? "已启用" : "未启用"}）</p>}
          {device?.snapshot?.ruStatus && <p className="hw-readback">{device.snapshot.ruStatus.cc ? "恒流 CC" : device.snapshot.ruStatus.cv ? "恒压 CV" : "CC/CV 未激活"} · OVP {device.snapshot.ruStatus.ovp ? "触发" : "未触发"} · OCP {device.snapshot.ruStatus.ocp ? "触发" : "未触发"} · OTP {device.snapshot.ruStatus.otp ? "触发" : "未触发"}</p>}
          <div className="hw-output-actions">
            {!motorSupply && <button className="button" disabled={!device?.connected || busy || !validPoint || !withinLimits || !registerSafe} onClick={() => onSet(supply.id)}>设置制动电流</button>}
            <button className={`button ${mayBeOn?'danger':'primary'}`} disabled={!mayBeOn&&(!device?.connected || busy || !validPoint || !withinLimits || !registerSafe || (!motorSupply && !brakeApplied))} onClick={() => onOutput(supply.id, !mayBeOn)}>{mayBeOn?`关闭${supply.title}`:`开启${supply.title}`}</button>
          </div>
          <p className="hw-safety-note">{motorSupply?`电源在面板固定设为 24 V / ${MOTOR_SUPPLY_PRESET_CURRENT_A} A；开启时只核验预设并切换输出，不重写 V/A。电机 Iq 在电机控制区设置。`:'先设置目标电流，再开启。恒流时电源随负载调节实际电压，最高 24 V；实际 CC/CV 以设备反馈为准。'}断开通信不会关闭电源输出。</p>
  </div>;

  useEffect(() => {
    try { localStorage.setItem(`motor-bench:${supply.id}:collapsed`, String(collapsed)); }
    catch { /* storage is optional */ }
  }, [collapsed, supply.id]);

  return (
    <section className={`hw-supply-card hw-compact-supply ${motorSupply ? 'is-motor-supply' : ''}`} aria-label={`${supply.title}控制`}>
      <div className="hw-supply-summary">
      <header className="hw-supply-head">
        <span className="hw-device-icon"><Icon name={supply.icon} size={21} /></span>
        <div className="hw-supply-title"><h2>{supply.title}</h2><p>{supply.role} · {ru ? "万瑞达 RU-36-6060" : "SSJ"} · Modbus RTU</p></div>
        <span className={`hw-status ${device?.connected ? "is-connected" : ""}`}><i />{device?.connected ? "已连接" : "未连接"}</span>
        <button
          type="button"
          className="hw-collapse-toggle"
          aria-label={`${collapsed ? "展开" : "收起"}${supply.title}控制`}
          aria-expanded={!collapsed}
          aria-controls={`${supply.id}-controls`}
          title={collapsed ? "展开控制区" : "收起控制区"}
          onClick={() => setCollapsed((current) => !current)}
        >
          {collapsed ? "展开" : "收起"}<span aria-hidden="true">{collapsed ? "⌄" : "⌃"}</span>
        </button>
      </header>
      <div className="hw-supply-readings" aria-label={`${supply.title}实时读数`}>
        <div><span>实际电压</span><strong>{values ? numberText(values.voltage, values.voltageDecimals) : "—"}<small>V</small></strong></div>
        <div><span>实际电流</span><strong>{values ? numberText(values.current, values.currentDecimals) : "—"}<small>A</small></strong></div>
        <div><span>{device?.snapshot?.powerCalculated ? "功率（V×A）" : "实际功率"}</span><strong>{values ? numberText(values.power, 0) : "—"}<small>W</small></strong></div>
        <div><span>设备输出</span><strong className={outputOn ? "hw-output-on" : ""}>{values ? values.output : "状态未知"}</strong></div>
      </div>
      </div>
      <div className="hw-supply-body" id={`${supply.id}-controls`} hidden={collapsed}>
        {device?.error && <div className="hw-inline-error" role="alert">{device.error}</div>}
        {!bridgeReady && <div className="hw-inline-error">本机通信桥未运行，请先启动 <code>npm run hardware</code>。</div>}
        <details className="hw-connection-config" open={!device?.connected}>
          <summary>独立通信参数</summary>
          <div className="hw-form-grid">
            {supply.id === "motor-supply" && <label>电源型号 / 协议<select aria-label="电机电源协议" value={config.protocol || "ru"} disabled={locked} onChange={e=>patchConfig({protocol:e.target.value,ratedVoltage:"",ratedCurrent:"",parity:"N",stopbits:"1",baud:"9600",address:"1",voltageDecimals:"2",currentDecimals:e.target.value==='ssj'?"3":"2"})}><option value="ru">万瑞达 RU-36-6060 / RU 系列</option><option value="ssj">SSJ 电源</option></select></label>}
            <label>传输方式<select value={config.transport} disabled={locked} onChange={(e) => patchConfig({ transport: e.target.value })}><option value="serial">{ru ? "串口 RS485 / RS232" : "串口 RS485"}</option><option value="tcp">网口 RTU 透明透传</option></select></label>
            {config.transport === "serial" ? <>
              <label>串口<select value={config.device} disabled={locked} onChange={(e) => patchConfig({ device: e.target.value })}><option value="">选择串口</option>{ports.map((port) => <option key={port.port} value={port.port}>{port.port} · {port.description}</option>)}</select></label>
              <label>波特率<select value={config.baud} disabled={locked} onChange={(e) => patchConfig({ baud: e.target.value })}>{(ru ? [9600,1200,2400,4800,14400,19200,38400,43000,57600,76800,115200,128000] : [9600,19200,38400,115200,4800,2400,1200,57600]).map((baud) => <option key={baud} value={baud}>{baud}</option>)}</select></label>
              <label>校验<select value={config.parity} disabled={locked || ru} onChange={(e) => patchConfig({ parity: e.target.value })}><option value="N">无校验 N</option><option value="E">偶校验 E</option><option value="O">奇校验 O</option></select></label>
              <label>停止位<select value={config.stopbits} disabled={locked || ru} onChange={(e) => patchConfig({ stopbits: e.target.value })}><option value="1">1</option><option value="2">2</option></select></label>
            </> : <>
              <label>网关地址<input value={config.host} disabled={locked} placeholder="例如 192.168.1.20" onChange={(e) => patchConfig({ host: e.target.value })} /></label>
              <label>网关 TCP 端口<input type="number" min="1" max="65535" value={config.tcpPort} disabled={locked} onChange={(e) => patchConfig({ tcpPort: e.target.value })} /></label>
            </>}
            <label>Modbus 地址<input type="number" min="1" max={ru ? "255" : "247"} step="1" value={config.address} disabled={locked} onChange={(e) => patchConfig({ address: e.target.value })} /></label>
            <label>电压小数位{ru && "（自动读取）"}<select value={config.voltageDecimals} disabled={locked || ru} onChange={(e) => patchConfig({ voltageDecimals: e.target.value })}>{[0, 1, 2, 3, 4].map((value) => <option key={value}>{value}</option>)}</select></label>
            <label>电流小数位{ru && "（自动读取）"}<select value={config.currentDecimals} disabled={locked || ru} onChange={(e) => patchConfig({ currentDecimals: e.target.value })}>{[0, 1, 2, 3, 4].map((value) => <option key={value}>{value}</option>)}</select></label>
            <label>额定电压上限 V{ru ? "（自动读取）" : "（可选）"}<input type="number" min="0" step="any" value={config.ratedVoltage} disabled={locked || ru} onChange={(e) => patchConfig({ ratedVoltage: e.target.value })} /></label>
            <label>额定电流上限 A{ru ? "（自动读取）" : "（可选）"}<input type="number" min="0" step="any" value={config.ratedCurrent} disabled={locked || ru} onChange={(e) => patchConfig({ ratedCurrent: e.target.value })} /></label>
          </div>
          <div className="hw-connection-actions">
            {!device?.connected ? <>
              <button className="button primary" disabled={!bridgeReady || busy || (config.transport === "serial" ? !config.device : !config.host || !config.tcpPort)} onClick={() => onConnect(supply.id)}>{busy ? "正在验证设备…" : "连接并验证"}</button>
              {config.transport === "serial" && <button className="button" disabled={!bridgeReady || busy} onClick={onRefreshPorts}>刷新串口</button>}
            </> : <>
              <button className="button" disabled={busy} onClick={() => onRead(supply.id)}>立即读取</button>
              <button className="button" disabled={busy || outputOn} title={outputOn ? "先关闭电源输出，再断开通讯" : "断开通讯不会改变电源输出状态"} onClick={() => onDisconnect(supply.id)}>断开通讯</button>
            </>}
            <span className="hw-confirmation">只有设备返回有效 CRC 和寄存器读数后才显示已连接。</span>
          </div>
        </details>
        <div className="hw-setpoint-section">
          <div className="hw-supply-meta">
          <div className="hw-section-title"><strong>电源输出控制</strong><span>设置值按设备读回确认</span></div>
          <p className="hw-readback">{motorSupply?`面板预设 24 V · 电源限流 ${MOTOR_SUPPLY_PRESET_CURRENT_A} A · 开启不改写预设`:'默认恒流 CC 控制 · 电压上限 24 V · 目标电流 0–2.3 A'}</p>
          {!motorSupply && <p className={supplyOperatingMode(device)==='cv'?'hw-inline-error':'hw-readback'} aria-live="polite">{brakeModeText(device)}</p>}
          {settings && device?.connected && <p className="hw-readback">当前设定读回：{(settings[1] / voltageScale).toFixed(Number(config.voltageDecimals))} V / {(settings[2] / currentScale).toFixed(Number(config.currentDecimals))} A</p>}
          </div>
          {motorSupply ? outputControls : <BrakeCurrentPanel token={token} onState={onDeviceState} onEvent={onEvent}>
            {outputControls}
          </BrakeCurrentPanel>}
        </div>
      </div>
    </section>
  );
});

function SensorPanel({ displayRate, device, config, setConfig, ports, bridgeReady, busy, onRefreshPorts, onConnect, onDisconnect, onRead }) {
  const snapshot = device?.connected ? device.snapshot : null;
  const sensorPorts = ports.filter((port) => !/can|slcan/i.test(`${port.port} ${port.description || ""}`));
  const locked = Boolean(device?.connected || busy);
  const patchConfig = (patch) => setConfig({ ...config, ...patch });

  return (
    <section className="hw-supply-card hw-sensor-panel" aria-label="动态扭矩传感器连接">
      <details className="hw-sensor-details" open={!device?.connected}>
      <summary className="hw-supply-head">
        <span className="hw-device-icon"><Icon name="sensor" size={21} /></span>
        <div className="hw-supply-title"><h2>动态扭矩传感器</h2><p>{config.protocol === "hex6" ? "HEX 6 字节" : config.protocol === "hex8" ? "HEX 8 字节" : "Modbus RTU"} · RS485 · 只读采集</p></div>
        <span className={`hw-status ${device?.connected ? "is-connected" : ""}`}><i />{device?.connected ? "已连接" : "未连接"}</span>
        <span className="hw-sensor-toggle" aria-hidden="true" />
      </summary>
      <div className="hw-sensor-details-body">
      <p className="hw-readback">通信校验：{snapshot ? "CRC 有效" : "未验证"}</p>
      {snapshot?.framesReceived != null && <p className="hw-readback">目标接收 1000 次/秒 · 实际接收 {snapshot.frameRateHz} 次/秒 · 累计 {snapshot.framesReceived} 帧 · 重同步跳过 {snapshot.resyncBytes} 字节<br />后台逐帧采集，网页与曲线同步显示 {displayRate} 次/秒（上限 30）；高速缓存保留最近 60000 帧。<a className="button small" href="/api/sensors/torque-sensor/capture.csv" download>导出高速原始数据</a></p>}
      <details className="hw-connection-config" open={!device?.connected}>
        <summary>独立 RS485 参数</summary>
        <div className="hw-form-grid">
          <label>接收协议<select value={config.protocol || "modbus"} disabled={locked} onChange={(e) => patchConfig({ protocol: e.target.value, baud: e.target.value === "modbus" ? "19200" : "115200", stopbits: e.target.value === "modbus" ? "2" : "1" })}><option value="hex6">HEX 6 字节主动接收（模式 0）</option><option value="hex8">HEX 8 字节主动接收（模式 3）</option><option value="modbus">Modbus RTU 只读查询（模式 1）</option></select></label>

          <label>USB-RS485 串口<select value={config.device} disabled={locked} onChange={(e) => patchConfig({ device: e.target.value })}><option value="">选择传感器串口</option>{sensorPorts.map((port) => <option key={port.port} value={port.port}>{port.port} · {port.description}</option>)}</select></label>
          <label>波特率<select value={config.baud} disabled={locked} onChange={(e) => patchConfig({ baud: e.target.value })}>{(config.protocol === "modbus" ? [19200, 14400, 9600, 4800, 2400, 1200] : [115200, 57600, 38400, 19200, 14400, 9600]).map((baud) => <option key={baud} value={baud}>{baud}</option>)}</select></label>
          {config.protocol === "modbus" && <label>Modbus 地址<input type="number" min="1" max="120" step="1" value={config.address} disabled={locked} onChange={(e) => patchConfig({ address: e.target.value })} /></label>}
          <label>停止位<select value={config.stopbits} disabled={locked} onChange={(e) => patchConfig({ stopbits: e.target.value })}><option value="2">2（Modbus 说明默认）</option><option value="1">1（参数表默认）</option></select></label>
          {config.protocol !== "modbus" && <><label>扭矩小数位（与设备一致）<input type="number" min="0" max="4" value={config.torqueDecimals} disabled={locked} onChange={(e) => patchConfig({ torqueDecimals: e.target.value })} /></label><label>转速小数位（与设备一致）<input type="number" min="0" max="3" value={config.speedDecimals} disabled={locked} onChange={(e) => patchConfig({ speedDecimals: e.target.value })} /></label></>}
        </div>
        <p className="hw-readback">串口格式为 8N（8 数据位、无校验）；手册奇偶校验记法有歧义，当前不自动猜测。</p>
        <div className="hw-connection-actions">
          {!device?.connected ? <>
            <button className="button primary" disabled={!bridgeReady || busy || !sensorPorts.some((port) => port.port === config.device)} onClick={onConnect}>{busy ? "正在只读验证…" : "连接并验证"}</button>
            <button className="button" disabled={!bridgeReady || busy} onClick={onRefreshPorts}>刷新串口</button>
          </> : <>
            <button className="button" disabled={busy} onClick={onRead}>立即读取</button>
            <button className="button" disabled={busy} onClick={onDisconnect}>断开通讯</button>
          </>}
          <span className="hw-confirmation">{config.protocol === "modbus" ? "只读查询模式、停止位、小数位和测量值。" : "只接收主动上传；需设备面板设好模式与波特率，连续 CRC 帧验证后连接。"}不会清零或改写传感器参数。</span>
        </div>
      </details>
      </div>
      </details>
      {device?.error && <div className="hw-inline-error" role="alert">{device.error}</div>}
      {!bridgeReady && <div className="hw-inline-error">本机通信桥未运行，请先启动 <code>npm run hardware</code>。</div>}
    </section>
  );
}

function Settings({ onClose, state, devices, bridgeReady, stopAll }) {
  const dialog = useRef(null);
  useEffect(() => {
    dialog.current.showModal();
  }, []);
  return (
    <dialog ref={dialog} className="settings-dialog" onCancel={onClose}
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
      aria-labelledby="settings-title">
      <div className="dialog-head">
        <div><div className="eyebrow">DEVICE SETUP</div><h2 id="settings-title">设备与连接</h2></div>
        <button className="icon-button" aria-label="关闭设备设置" onClick={onClose}><Icon name="close" /></button>
      </div>
      <div className="dialog-body">
        <div className="notice">
          <Icon name="info" />
          <div><strong>{bridgeReady ? "本机设备通信桥已启动" : "本机设备通信桥未启动"}</strong>
            <p>电机、电源和传感器连接状态只由设备真实协议回复确认。未接入的接口保持未连接，读数留空，不生成模拟数据。</p>
          </div>
        </div>
        {CHANNELS.map((channel) => {
          const connected = Boolean(state[channel.key]);
          const deviceId = channel.key === "motorSupplyConnected" ? "motor-supply" : channel.key === "supplyConnected" ? "brake-supply" : channel.key === "sensorConnected" ? "torque-sensor" : "current-motor";
          const snapshot = deviceId ? devices[deviceId]?.snapshot : null;
          return <section className="device-setup" key={channel.key}>
            <div className="setup-title">
              <span className="device-icon"><Icon name={channel.icon} /></span>
              <div><h3>{channel.name}</h3><p>{channel.detail}</p></div>
              <span className={`connection-status ${connected ? "" : "disconnected"}`}><i />{connected ? "已连接" : "未连接"}</span>
            </div>
            <dl>
              {deviceId === "torque-sensor" ? <>
                <div><dt>连接方式</dt><dd>独立 USB-RS485 · {snapshot?.config?.protocol === "hex6" ? "HEX 6 字节主动接收" : snapshot?.config?.protocol === "hex8" ? "HEX 8 字节主动接收" : "Modbus RTU"}</dd></div>
                <div><dt>实际扭矩 / 转速</dt><dd>{connected && snapshot ? `${numberText(snapshot.torque, snapshot.torqueDecimals)} N·m / ${numberText(snapshot.speed, snapshot.speedDecimals)} rpm` : "—"}</dd></div>
                <div><dt>读数验证</dt><dd>{connected ? snapshot?.framesReceived != null ? "连续 HEX 帧 CRC 已验证" : "CRC、通信模式、停止位和缩放已验证" : "未完成握手"}</dd></div>
                <div><dt>写入操作</dt><dd>只读；未执行清零或参数修改</dd></div>
              </> : deviceId === "current-motor" ? <>
                <div><dt>接口</dt><dd>西格 CAN · Iq 电流环（模式 5）</dd></div>
                <div><dt>固件版本</dt><dd>{snapshot?.version || "—"}</dd></div>
                <div><dt>电流上限</dt><dd>{snapshot?.limitA ?? "—"} A</dd></div>
                <div><dt>设备输出</dt><dd>{connected ? snapshot?.motor?.output_on ? "输出开启" : "输出关闭" : "状态未知"}</dd></div>
              </> : deviceId ? <>
                <div><dt>连接方式</dt><dd>Modbus RTU 串口 / RTU 透明 TCP</dd></div>
                <div><dt>设备输出</dt><dd>{connected && Number.isFinite(snapshot?.status) ? (snapshot.status & 0x80 ? "输出开启" : "输出关闭") : "状态未知"}</dd></div>
                <div><dt>实际电压 / 电流</dt><dd>{connected && snapshot?.measured ? `${numberText(snapshotValues(devices[deviceId])?.voltage, snapshotValues(devices[deviceId])?.voltageDecimals)} V / ${numberText(snapshotValues(devices[deviceId])?.current, snapshotValues(devices[deviceId])?.currentDecimals)} A` : "—"}</dd></div>
                <div><dt>寄存器应答</dt><dd>{connected ? "有效 CRC 与读回已验证" : "未完成握手"}</dd></div>
              </> : channel.key === "motorConnected" ? <>
                <div><dt>接口</dt><dd>CAN 电机控制器</dd></div><div><dt>状态</dt><dd>CAN 协议与控制报文尚未接入</dd></div>
              </> : <>
                <div><dt>接口</dt><dd>CAN 电机控制器</dd></div><div><dt>状态</dt><dd>CAN 协议与控制报文尚未接入</dd></div>
              </>}
            </dl>
          </section>;
        })}
        <div className="demo-note"><strong>测功链路</strong><p>电机 → 减速箱 → 动态扭矩传感器 → 磁粉制动器。扭矩、转速和计算轴功率来自传感器有效读数；电机仅接入 Iq 电流环与步进控制，连接不会自动开启输出。</p></div>
        <div className="demo-note"><strong>双电源</strong><p>电机电源与制动电源分别配置独立通信通道。断开通信不会改变设备输出状态，输出开关须以设备状态读回为准。</p></div>
      </div>
      <div className="dialog-foot">
        <button className="button stop-all" onClick={stopAll} disabled={!bridgeReady}>关闭制动电源输出</button>
        <button className="button primary" onClick={onClose}>完成</button>
      </div>
    </dialog>
  );
}

export default function App() {
  const [initial] = useState(() => readRecords());
  const [records, setRecords] = useState(initial.records);
  const recordsRef = useRef(initial.records);
  const engine = useRef(initialBenchState());
  const [state, setState] = useState(engine.current);
  const [samples, setSamples] = useState([]);
  const [view, setView] = useState("bench");
  const viewRef = useRef(view);
  viewRef.current = view;
  const [settings, setSettings] = useState(false);
  const [metric, setMetric] = useState("torque");
  const [characteristicView, setCharacteristicView] = useState(false);
  const [windowSeconds, setWindowSeconds] = useState(30);
  const [frozen, setFrozen] = useState(null);
  const trial = useRef(null);
  const [trialRun,setTrialRun] = useState(null);
  const [frozenRun,setFrozenRun] = useState(null);
  const [events, setEvents] = useState([
    { at: new Date(), text: "真实设备模式已就绪；全部接口初始为未连接。" },
  ]);
  const [toast, setToast] = useState("");
  const [storageError, setStorageError] = useState(initial.error);
  const [storageWarning, setStorageWarning] = useState("");
  const [saving, setSaving] = useState(false);
  const unsavedIds = useRef(new Set());
  const recording = useRef(null);
  const [previewRecord, setActiveRecord] = useState(null);
  const [backendSession, setBackendSession] = useState(null);
  const [fullHistory, setFullHistory] = useState([]);
  const [recordBusy, setRecordBusy] = useState(false);
  const [recordServiceOnline, setRecordServiceOnline] = useState(false);
  const recordBusyRef = useRef(false);
  const backendSessionRef = useRef(null);
  backendSessionRef.current = backendSession;
  const activeRecord = backendSession?.running ? {...previewRecord, id:backendSession.id,
    count:Object.values(backendSession.counts || {}).reduce((sum,value)=>sum+value,0)} : previewRecord;
  const [recordName, setRecordName] = useState("减速箱加载试验");
  const [selected, setSelected] = useState(null);
  const [historyMetric, setHistoryMetric] = useState("torque");
  const [showEvents, setShowEvents] = useState(false);
  const [bridgeReady, setBridgeReady] = useState(false);
  const [ports, setPorts] = useState([]);
  const [connectingAll, setConnectingAll] = useState(false);
  const [connectionReport, setConnectionReport] = useState([]);
  const connectionSweep = useRef(false);
  const [devices, setDevices] = useState({
    "current-motor": { connected: false, snapshot: null, error: "" },
    "motor-supply": { connected: false, config: null, snapshot: null, error: "" },
    "brake-supply": { connected: false, config: null, snapshot: null, error: "" },
    "torque-sensor": { connected: false, config: null, snapshot: null, error: "" },
  });
  const [configs, setConfigs] = useState(() => Object.fromEntries(SUPPLIES.map(({ id }) => [id, loadConfig(id)])));
  const [sensorConfig, setSensorConfig] = useState(loadSensorConfig);
  const [setpoints, setSetpoints] = useState({
    "motor-supply": supplyPoint('motor-supply'),
    "brake-supply": supplyPoint('brake-supply'),
  });
  const [busy, setBusy] = useState({ "motor-supply": false, "brake-supply": false });
  const [sensorBusy, setSensorBusy] = useState(false);
  const [displayRate, setDisplayRate] = useState(0);
  const token = useRef("");
  const inFlight = useRef(new Set());
  const supplyReads = useRef(new Map());
  const supplyGeneration = useRef({});
  const devicesRef = useRef(devices);
  const configsRef = useRef(configs);
  const pageStart = useRef(performance.now());
  const pageWallStart = useRef(Date.now() / 1000);
  devicesRef.current = devices;
  configsRef.current = configs;

  useEffect(()=>{
    let alive=true, inFlight=false, lastHistory=0;
    async function pollRecording() {
      if(inFlight)return;
      inFlight=true;
      try {
        const history=Date.now()-lastHistory>=5000;
        const result=await fetchJson(`/api/recordings/status?history=${history?1:0}`,{cache:'no-store',signal:AbortSignal.timeout(3000)});
        if(!alive || recordBusyRef.current)return;
        const known=backendSessionRef.current;
        let current=result.current;
        if(known && current && (current.startedAt<known.startedAt || (current.id===known.id && known.finishedAt && current.running)))return;
        if(!current && known)current=(result.records || []).find(row=>row.id===known.id) || known;
        if(history){lastHistory=Date.now();setFullHistory(result.records || []);}
        backendSessionRef.current=current;setBackendSession(current);setRecordServiceOnline(true);
        if(current && !current.running && recording.current?.fullRecordId===current.id)
          void finishRef.current(current.reason || '后台记录已结束');
      } catch {if(alive)setRecordServiceOnline(false);}
      finally {inFlight=false;}
    }
    void pollRecording();const timer=setInterval(pollRecording,1000);
    return()=>{alive=false;clearInterval(timer);};
  },[]);

  const addEvent = (text) => {
    const at = new Date();
    setEvents((previous) => [{ at, text }, ...previous].slice(0, 100));
    if (recording.current) {
      recording.current.events.push({
        at: at.toISOString(),
        t: Math.max(0, engine.current.t - recording.current.engineOrigin),
        text,
      });
    }
  };

  const update = (patch) => {
    engine.current = { ...engine.current, ...patch };
    setState(engine.current);
  };

  function hardwareSample(deviceSet = devicesRef.current, t = engine.current.t) {
    const motor = deviceSet["motor-supply"];
    const brake = deviceSet["brake-supply"];
    const sensor = deviceSet["torque-sensor"];
    const motorValues = snapshotValues(motor);
    const brakeValues = snapshotValues(brake);
    const sensorValues = sensor?.connected ? sensor.snapshot : null;
    const received = [motor, brake, sensor]
      .filter((device) => device?.connected && device.snapshot?.receivedAt)
      .map((device) => device.snapshot.receivedAt)
      .filter(Number.isFinite);
    const latestReceived = received.length ? Math.max(...received) : Date.now() / 1000;
    const sampleReceivedAt = sensorValues?.receivedAt ?? latestReceived;
    const shaftPowerKw = calculateShaftPowerKw(sensorValues?.torque, sensorValues?.speed);
    return {
      source: "hardware",
      receivedAt: new Date(sampleReceivedAt * 1000).toISOString(),
      t,
      torque: sensorValues?.torque ?? null,
      speed: Number.isFinite(sensorValues?.speed) ? Math.abs(sensorValues.speed) : null,
      torqueDecimals: sensorValues?.torqueDecimals ?? 2,
      speedDecimals: sensorValues?.speedDecimals ?? 1,
      shaftPowerKw,
      current: motorTargetCurrentA(deviceSet["current-motor"]),
      motorSupplyVoltage: motorValues?.voltage ?? null,
      motorSupplyCurrent: motorValues?.current ?? null,
      motorSupplyPower: motorValues?.power ?? null,
      motorSupplyReceivedAt: motor?.snapshot?.receivedAt ?? null,
      brakeSupplyVoltage: brakeValues?.voltage ?? null,
      brakeSupplyCurrent: brakeValues?.current ?? null,
      brakeSupplyPower: brakeValues?.power ?? null,
      brakeSupplyReceivedAt: brake?.snapshot?.receivedAt ?? null,
      targetSpeed: null,
      targetCurrent: Number.isFinite(engine.current.targetCurrent) ? engine.current.targetCurrent : null,
      motorEnabled: deviceSet["current-motor"]?.connected ? deviceSet["current-motor"].snapshot?.motor?.output_on ?? null : null,
      loadEnabled: brakeValues ? Boolean(brakeValues.status & 0x80) : null,
      range: engine.current.range,
    };
  }

  function syncBenchState(deviceSet) {
    const motor = deviceSet["motor-supply"];
    const brake = deviceSet["brake-supply"];
    const sensor = deviceSet["torque-sensor"];
    const motorValues = snapshotValues(motor);
    const brakeValues = snapshotValues(brake);
    const sensorValues = sensor?.connected ? sensor.snapshot : null;
    const brakeConfig = brake?.config || brake?.snapshot?.config || {};
    const currentDecimals = Number(brakeConfig.currentDecimals ?? 2);
    const rawTargetCurrent = brake?.snapshot?.settings?.[2];
    const targetCurrent = Number.isFinite(rawTargetCurrent)
      ? rawTargetCurrent / 10 ** currentDecimals
      : null;
    const patch = {
      motorConnected: Boolean(deviceSet["current-motor"]?.connected && deviceSet["current-motor"]?.snapshot),
      motorEnabled: deviceSet["current-motor"]?.connected ? deviceSet["current-motor"].snapshot?.motor?.output_on ?? null : null,
      motorSupplyConnected: Boolean(motor?.connected && motor?.snapshot),
      supplyConnected: Boolean(brake?.connected && brake?.snapshot),
      sensorConnected: Boolean(sensor?.connected && sensor?.snapshot),
      torque: sensorValues?.torque ?? null,
      speed: Number.isFinite(sensorValues?.speed) ? Math.abs(sensorValues.speed) : null,
      motorSupplyVoltage: motorValues?.voltage ?? null,
      motorSupplyCurrent: motorValues?.current ?? null,
      motorSupplyPower: motorValues?.power ?? null,
      motorSupplyReceivedAt: motor?.snapshot?.receivedAt ?? null,
      brakeSupplyVoltage: brakeValues?.voltage ?? null,
      brakeSupplyCurrent: brakeValues?.current ?? null,
      brakeSupplyPower: brakeValues?.power ?? null,
      current: deviceSet["current-motor"]?.connected ? deviceSet["current-motor"].run?.targetA ?? null : null,
      loadEnabled: brakeValues ? Boolean(brakeValues.status & 0x80) : null,
      targetCurrent,
    };
    engine.current = { ...engine.current, ...patch };
    setState(engine.current);
  }

  function appendHardwareSample(deviceSet) {
    const connected = Object.entries(deviceSet).some(([id, device]) => id !== "current-motor" && device?.connected && device?.snapshot);
    if (!connected) return;
    const elapsed = Math.max(engine.current.t, (performance.now() - pageStart.current) / 1000);
    engine.current = { ...engine.current, t: elapsed };
    setState(engine.current);
    const sample = hardwareSample(deviceSet, elapsed);
    appendTrialSamples([sample]);
    setSamples(previous => appendSamples(previous, [sample], LIVE_SAMPLE_LIMIT));
    if (recording.current) {
      const recordedSample = { ...sample, t: Math.max(0, elapsed - recording.current.engineOrigin) };
      recording.current.samples.push(recordedSample);
      setActiveRecord({ ...recording.current, count: recording.current.samples.length });
      if (recording.current.samples.length >= MAX_SAMPLES)
        recording.current.samples=recording.current.samples.filter((_,index)=>index%2===0);
    }
  }

  function updateDevice(id, payload, { capture = false } = {}) {
    const connected = payload?.connected === true;
    const snapshot = connected
      ? payload.snapshot || (Array.isArray(payload.measured) ? payload : null)
      : null;
    const previous = devicesRef.current;
    // A slow health response must not overwrite a newer sensor read.
    if (connected && previous[id]?.connected && snapshot?.receivedAt &&
        previous[id]?.snapshot?.receivedAt > snapshot.receivedAt) return;
    const next = {
      ...previous,
      [id]: {
        ...previous[id],
        connected: Boolean(connected && snapshot),
        config: payload?.config || snapshot?.config || previous[id]?.config || null,
        snapshot,
        run: payload?.run || previous[id]?.run || {},
        error: payload?.error || "",
        outputUncertain:payload?.outputUncertain??previous[id]?.outputUncertain??false,
      },
    };
    devicesRef.current = next;
    setDevices(next);
    syncBenchState(next);
    if (capture && connected && snapshot) appendHardwareSample(next);
  }

  const saveFinished = async (message = "真实采集记录已完成") => {
    const record = recording.current;
    const session=backendSessionRef.current;
    if(recordBusyRef.current || (!record && !session?.running))return;
    recordBusyRef.current=true;setRecordBusy(true);
    try {
      if(session?.running || (record?.fullRecordId && session?.id!==record.fullRecordId)) {
        const result=await fetchJson('/api/recordings/stop',{method:'POST',headers:{'Content-Type':'application/json','X-MotorBench-Token':token.current},
          body:JSON.stringify({id:record?.fullRecordId || session.id}),signal:AbortSignal.timeout(10000)});
        backendSessionRef.current=result.current;setBackendSession(result.current);
        setFullHistory(previous=>[result.current,...previous.filter(row=>row.id!==result.current.id)]);
      }
      if(!record){setActiveRecord(null);setToast('完整参数记录已保存，可导出 CSV / JSONL 和报告');return;}
    addEvent(`${message} · 完整时间序列已落盘；${record.samples.length} 个网页预览点`);
    recording.current = null;
    const complete = {
      ...record,
      duration: Math.max(0, engine.current.t - record.engineOrigin),
      finishedAt: Date.now(),
    };
    delete complete.engineOrigin;
    const next = mergeRecords([complete], recordsRef.current);
    recordsRef.current = next;
    unsavedIds.current.add(complete.id);
    setRecords(next);
    setActiveRecord(null);
    setSaving(true);
    const result = await saveRecords(next);
    recordsRef.current = mergeRecords(result.records, recordsRef.current);
    setRecords(recordsRef.current);
    setStorageError(result.error);
    setStorageWarning(result.warning || "");
    if (result.saved) unsavedIds.current.clear();
    setSaving(false);
    setToast(result.saved ? '完整时间序列已保存，可导出 CSV / JSONL 和报告' : '完整时间序列已落盘，网页预览保存失败；可直接导出完整 CSV');
    } catch(error) {setToast(`记录收尾失败：${error.message}；可重试停止记录`);}
    finally {recordBusyRef.current=false;setRecordBusy(false);setSaving(false);}
  };
  const finishRef = useRef(saveFinished);
  finishRef.current = saveFinished;

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.error || `通信失败 (${response.status})`);
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  async function refreshPorts(showToast = false) {
    try {
      const result = await fetchJson("/api/ports");
      setPorts(result.ports || []);
      if (showToast) setToast(result.ports?.length ? `发现 ${result.ports.length} 个串口` : "未发现串口；也可配置 RTU 透明 TCP 网关");
    } catch {
      setPorts([]);
      if (showToast) setToast("无法读取串口列表，请确认本机通信桥已启动");
    }
  }

  function readSupply(id, quiet = false) {
    if (!token.current || inFlight.current.has(id)) return;
    if (supplyReads.current.has(id)) return supplyReads.current.get(id);
    const request = (async () => {
      const generation=supplyGeneration.current[id]||0;
      if (!quiet) setBusy(previous => ({ ...previous, [id]: true }));
      try {
        const payload = await fetchJson(`/api/supplies/${id}/read`, {
          method: "POST", headers: { "X-MotorBench-Token": token.current },
          signal: AbortSignal.timeout(4000),
        });
        if(generation===(supplyGeneration.current[id]||0))updateDevice(id, payload, { capture: true });
      } catch (error) {
        if(generation!==(supplyGeneration.current[id]||0))return;
        if (error.payload && typeof error.payload.connected === "boolean") updateDevice(id, error.payload);
        else updateDevice(id, { connected: false, error: error.message });
      } finally {
        supplyReads.current.delete(id);
        if (!quiet && !inFlight.current.has(id)) setBusy(previous => ({ ...previous, [id]: false }));
      }
    })();
    supplyReads.current.set(id, request);
    return request;
  }

  async function runTorqueSensorAction(action, { quiet = false, payload = {} } = {}) {
    const id = "torque-sensor";
    if (!token.current || inFlight.current.has(id)) return;
    inFlight.current.add(id);
    if (!quiet) setSensorBusy(true);
    try {
      const result = await fetchJson(`/api/sensors/${id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-MotorBench-Token": token.current },
        body: JSON.stringify(payload),
      });
      updateDevice(id, result, { capture: action !== "disconnect" });
      if (result.connected && result.config) {
        setSensorConfig({
          ...DEFAULT_SENSOR_CONFIG,
          ...result.config,
          protocol: ["hex6", "hex8"].includes(result.config.protocol) ? result.config.protocol : "modbus",
          baud: String(result.config.baud || 19200),
          address: String(result.config.address || 1),
          stopbits: String(result.config.stopbits ?? 2),
        });
      }
      if (action !== "read") {
        addEvent(action === "connect" ? "动态扭矩传感器：真实协议验证通过" : "动态扭矩传感器：通信已断开");
        if (!quiet) setToast(action === "connect" ? "动态扭矩传感器已通过只读协议验证" : "传感器通信已断开");
      }
    } catch (error) {
      if (error.payload && typeof error.payload.connected === "boolean") updateDevice(id, error.payload);
      else updateDevice(id, { connected: false, error: error.message });
      setDevices((previous) => ({ ...previous, [id]: { ...previous[id], error: error.message } }));
    } finally {
      inFlight.current.delete(id);
      if (!quiet) setSensorBusy(false);
    }
  }

  function connectTorqueSensor() {
    return runTorqueSensorAction("connect", {
      payload: {
        device: sensorConfig.device,
        protocol: sensorConfig.protocol,
        torqueDecimals: Number(sensorConfig.torqueDecimals),
        speedDecimals: Number(sensorConfig.speedDecimals),
        baud: Number(sensorConfig.baud),
        address: Number(sensorConfig.address),
        stopbits: Number(sensorConfig.stopbits),
      },
    });
  }

  function disconnectTorqueSensor() {
    void runTorqueSensorAction("disconnect");
  }

  function serverConfigToForm(serverConfig, previous) {
    return {
      ...previous,
      ...serverConfig,
      device: serverConfig.device || "",
      baud: String(serverConfig.baud || 9600),
      address: String(serverConfig.address || 1),
      stopbits: String(serverConfig.stopbits || 1),
      voltageDecimals: String(serverConfig.voltageDecimals ?? 2),
      currentDecimals: String(serverConfig.currentDecimals ?? 2),
      ratedVoltage: serverConfig.ratedVoltage ?? "",
      ratedCurrent: serverConfig.ratedCurrent ?? "",
      maxVoltage: serverConfig.maxVoltage == null ? "" : String(serverConfig.maxVoltage),
      maxCurrent: serverConfig.maxCurrent == null ? "" : String(serverConfig.maxCurrent),
      tcpPort: serverConfig.tcpPort ? String(serverConfig.tcpPort) : "",
    };
  }

  useEffect(() => {
    for (const [id, config] of Object.entries(configs)) {
      try { localStorage.setItem(`motor-bench:${id}:config`, JSON.stringify(config)); } catch { /* storage is optional */ }
    }
  }, [configs]);

  useEffect(() => {
    try { localStorage.setItem("motor-bench:torque-sensor:config", JSON.stringify(sensorConfig)); } catch { /* storage is optional */ }
  }, [sensorConfig]);

  useEffect(() => {
    let active = true, syncing = false;
    async function syncHardware() {
      if (syncing) return;
      syncing = true;
      try {
        const health = await fetchJson("/api/health", {signal: AbortSignal.timeout(3000)});
        if (!active || health.app !== "motor-test-bench-hardware") return;
        token.current = health.token || "";
        setBridgeReady(true);
        for (const supply of SUPPLIES) {
          const status = health.channels?.[supply.id] || { connected: false };
          const wasConnected = devicesRef.current[supply.id]?.connected;
          updateDevice(supply.id, status);
          if (status.connected && status.snapshot) {
            const serverConfig = status.config || status.snapshot.config;
            if (serverConfig) setConfigs((previous) => ({ ...previous, [supply.id]: serverConfigToForm(serverConfig, previous[supply.id]) }));
            const values = status.snapshot.settings;
            if (values) {
              const config = serverConfig || configsRef.current[supply.id];
              setSetpoints((previous) => ({
                ...previous,
                [supply.id]: supplyReadbackPoint(supply.id, previous[supply.id], values, config),
              }));
            }
          }
          if (status.connected && !wasConnected) void readSupply(supply.id, true);
        }
        const sensorStatus = health.channels?.["torque-sensor"] || { connected: false };
        updateDevice("current-motor", health.channels?.["current-motor"] || { connected: false });
        const sensorWasConnected = devicesRef.current["torque-sensor"]?.connected;
        updateDevice("torque-sensor", sensorStatus);
        if (sensorStatus.config) setSensorConfig({
          ...DEFAULT_SENSOR_CONFIG,
          ...sensorStatus.config,
          protocol: ["hex6", "hex8"].includes(sensorStatus.config.protocol) ? sensorStatus.config.protocol : "modbus",
          baud: String(sensorStatus.config.baud || 19200),
          address: String(sensorStatus.config.address || 1),
          stopbits: String(sensorStatus.config.stopbits ?? 2),
        });
        if (sensorStatus.connected && !sensorWasConnected) void runTorqueSensorAction("read", { quiet: true });
      } catch {
        if (!active) return;
        token.current = "";
        setBridgeReady(false);
        for (const supply of SUPPLIES) updateDevice(supply.id, { connected: false });
        updateDevice("torque-sensor", { connected: false });
        updateDevice("current-motor", { connected: false });
      } finally { syncing = false; }
    }
    void syncHardware();
    void refreshPorts(false);
    const timer = window.setInterval(() => {
      void syncHardware();
      const run = devicesRef.current['current-motor']?.run;
      if (!(run?.running && run?.acquisitionManaged))
        for (const supply of SUPPLIES) if (devicesRef.current[supply.id]?.connected) void readSupply(supply.id, true);
    }, 1500);
    let frameRequest = 0, receiveTimer = 0, cursor = 0, lastPaint = 0, rateStarted = performance.now(), paintCount = 0;
    let pending = [], latestBatch = null, latestPoint = null, resetView = false;
    let recordedSequence = null, streamError = false;
    // Network receive is independent of display cadence, and never overlaps itself.
    async function receiveSensor() {
      const started = performance.now();
      const sensor = devicesRef.current["torque-sensor"];
      if (active && sensor?.connected && ["hex6", "hex8"].includes(sensor.config?.protocol)) {
        try {
          const batch = await fetchJson(`/api/sensors/torque-sensor/frames?since=${cursor}`,
            { signal: AbortSignal.timeout(2500), cache: "no-store" });
          if (!active) return;
          latestBatch = batch;
          streamError = false;
          if (batch.reset) { pending = []; resetView = true; }
          if (batch.frames?.length) {
            const config = batch.config;
            const base = hardwareSample(devicesRef.current);
            const torqueScale = 10 ** config.torqueDecimals, speedScale = 10 ** config.speedDecimals;
            // Old buffered frames precede this page session; do not clamp them all to t=0.
            for (const [seq, wall, rawTorque, rawSpeed] of batch.frames) {
              if (wall < pageWallStart.current) continue;
              const torque = rawTorque / torqueScale, speed = Math.abs(rawSpeed / speedScale);
              const power = calculateShaftPowerKw(torque, speed);
              const inputFresh = Number.isFinite(base.motorSupplyReceivedAt) && Math.abs(wall - base.motorSupplyReceivedAt) <= 2;
              const point = { ...base, sequence: seq, receivedAt: new Date(wall * 1000).toISOString(),
                t: wall - pageWallStart.current, torque, speed, shaftPowerKw: power,
                efficiencyPercent: inputFresh && base.motorSupplyPower > 0 ? power * 100000 / base.motorSupplyPower : null };
              pending.push(point);
              latestPoint = point;
            }
            cursor = batch.frames.at(-1)[0];
            if (pending.length > LIVE_SAMPLE_LIMIT) pending = pending.slice(-LIVE_SAMPLE_LIMIT);
            if (batch.dropped) setToast(`网页恢复时有 ${batch.dropped} 帧已超出高速缓存`);
          }
        } catch (error) {
          if (active && !streamError) setToast(`高速数据刷新失败：${error.message}`);
          streamError = true;
        }
      }
      if (active) receiveTimer = window.setTimeout(receiveSensor, Math.max(0, (sensor?.connected ? 20 : 250) - (performance.now() - started)));
    }
    // At most 30 display updates/s, aligned to screen frames; keep all received samples.
    function displaySensor(now) {
      if (!active) return;
      if (now - lastPaint >= 1000 / 30) {
        lastPaint = now - ((now - lastPaint) % (1000 / 30));
        if (latestBatch) { updateDevice("torque-sensor", latestBatch); latestBatch = null; }
        if (pending.length) {
          paintCount++;
          const points = pending; pending = [];
          appendTrialSamples(points);
          const reset = resetView; resetView = false;
          engine.current.t = points.at(-1).t;
          setSamples(previous => appendSamples(reset ? [] : previous, points, LIVE_SAMPLE_LIMIT));
        }
      }
      if (now - rateStarted >= 1000) {
        setDisplayRate(Math.round(paintCount * 1000 / (now - rateStarted)));
        paintCount = 0; rateStarted = now;
      }
      frameRequest = requestAnimationFrame(displaySensor);
    }
    void receiveSensor();
    frameRequest = requestAnimationFrame(displaySensor);
    // Keep read-only motor feedback and the existing page lease while viewing test results.
    let motorPolling = false;
    const motorTimer = window.setInterval(async () => {
      if (!active || viewRef.current === "bench" || motorPolling || !token.current) return;
      motorPolling = true;
      try {
        const result = await fetchJson('/api/motor-current/status', { signal: AbortSignal.timeout(2500), cache: 'no-store' });
        if (active) updateDevice('current-motor', result);
      } catch {
        if (active) updateDevice('current-motor', { connected: false });
      } finally { motorPolling = false; }
    }, 250);

    const sensorTimer = window.setInterval(() => {
      const sensor = devicesRef.current["torque-sensor"];
      if (!sensor?.connected) return;
      if (!["hex6", "hex8"].includes(sensor.config?.protocol)) void runTorqueSensorAction("read", { quiet: true });
      else if (recording.current && latestPoint && latestPoint.sequence !== recordedSequence) {
        recordedSequence = latestPoint.sequence;
        recording.current.samples.push({ ...latestPoint, t: Math.max(0, latestPoint.t - recording.current.engineOrigin) });
        setActiveRecord({ ...recording.current, count: recording.current.samples.length });
        if (recording.current.samples.length >= MAX_SAMPLES)
          recording.current.samples=recording.current.samples.filter((_,index)=>index%2===0);
      }
    }, SENSOR_POLL_MS);
    return () => { active = false; window.clearInterval(timer); window.clearInterval(sensorTimer);
      window.clearTimeout(receiveTimer); window.clearInterval(motorTimer); cancelAnimationFrame(frameRequest); };

  }, []);

  async function runAction(id, action, payload = {}, successText = "操作完成") {
    const stopping=action==='output'&&payload.on===false;
    if (!token.current || (!stopping&&inFlight.current.has(id))) return;
    const generation=(supplyGeneration.current[id]||0)+1;
    supplyGeneration.current[id]=generation;
    inFlight.current.add(id);
    setBusy((previous) => ({ ...previous, [id]: true }));
    try {
      // Queue the user's operation behind the current read instead of dropping the click.
      if(!stopping)await supplyReads.current.get(id);
      const result = await fetchJson(`/api/supplies/${id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-MotorBench-Token": token.current },
        body: JSON.stringify(payload),
        signal:AbortSignal.timeout(stopping?15000:12000),
      });
      if(generation!==supplyGeneration.current[id])return;
      if(stopping && ((result.snapshot||result).status&128))throw new Error('电源输出关闭尚未确认，请重试关闭');
      updateDevice(id, result, { capture: true });
      const resultSnapshot = result.snapshot || (Array.isArray(result.measured) ? result : null);
      if (result.connected && resultSnapshot?.settings && ['connect','set','output'].includes(action)) {
        const serverConfig = result.config || resultSnapshot.config || configsRef.current[id];
        setSetpoints((previous) => ({
          ...previous,
          [id]: supplyReadbackPoint(id, previous[id], resultSnapshot.settings, serverConfig, ['set','output'].includes(action)),
        }));
      }
      if (result.connected && ["connect","limits"].includes(action) && result.config) {
        setConfigs((previous) => ({ ...previous, [id]: serverConfigToForm(result.config, previous[id]) }));
      }
      if (action !== "read") {
        const name = SUPPLIES.find((item) => item.id === id)?.title || id;
        addEvent(`${name}：${successText}`);
      }
      setToast(successText);
    } catch (error) {
      if(generation!==supplyGeneration.current[id])return;
      if (error.payload && typeof error.payload.connected === "boolean") updateDevice(id, error.payload);
      else updateDevice(id, { connected: false, error: error.message });
      setDevices((previous) => ({ ...previous, [id]: { ...previous[id], error: error.message } }));
    } finally {
      if(generation===supplyGeneration.current[id]){
        inFlight.current.delete(id);
        setBusy((previous) => ({ ...previous, [id]: false }));
      }
    }
  }

  function connectionPayload(id) {
    const config = configs[id];
    const common = {
      transport: config.transport,
      protocol: config.protocol || (id === "motor-supply" ? "ru" : "ssj"),
      address: Number(config.address),
      voltageDecimals: Number(config.voltageDecimals),
      currentDecimals: Number(config.currentDecimals),
      ratedVoltage: config.ratedVoltage === "" ? null : Number(config.ratedVoltage),
      ratedCurrent: config.ratedCurrent === "" ? null : Number(config.ratedCurrent),
      maxVoltage: config.maxVoltage === "" ? null : Number(config.maxVoltage),
      maxCurrent: config.maxCurrent === "" ? null : Number(config.maxCurrent),
    };
    return config.transport === "serial"
      ? { ...common, device: config.device, baud: Number(config.baud), parity: config.parity, stopbits: Number(config.stopbits) }
      : { ...common, host: config.host, tcpPort: Number(config.tcpPort) };
  }

  function connectSupply(id) {
    return runAction(id, "connect", connectionPayload(id), `已通过 Modbus 握手连接${SUPPLIES.find((item) => item.id === id).title}`);
  }
  async function connectAll() {
    if (connectionSweep.current || !bridgeReady) return;
    connectionSweep.current = true;
    setConnectingAll(true);
    setConnectionReport([]);
    try {
      await refreshPorts(false);
      const hints = Object.fromEntries(SUPPLIES.map(supply => [supply.id, connectionPayload(supply.id)]));
      hints["torque-sensor"] = sensorConfig;
      let progress = await fetchJson('/api/connections/start', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MotorBench-Token': token.current },
        body: JSON.stringify(hints), signal: AbortSignal.timeout(5000),
      });
      setConnectionReport(progress.report || []);
      while (progress.running) {
        await new Promise(resolve => window.setTimeout(resolve, 400));
        progress = await fetchJson('/api/connections/status', { signal: AbortSignal.timeout(5000), cache: 'no-store' });
        setConnectionReport(progress.report || []);
      }
      const health = await fetchJson('/api/health', { signal: AbortSignal.timeout(5000) });
      for (const [id, device] of Object.entries(health.channels || {})) updateDevice(id, device);
      addEvent(`一键连接完成：${(progress.report || []).join("；")}`);
    } catch (error) {
      setConnectionReport(previous => [...previous, `连接检查中断：${error.message}；可再次点击查看/继续`]);
    } finally { connectionSweep.current = false; setConnectingAll(false); }
  }

  function disconnectSupply(id) {
    void runAction(id, "disconnect", {}, "通信已断开；电源输出状态不会因此改变");
  }
  function setSupplyValues(id) {
    const point = supplyPoint(id,setpoints[id]);
    void runAction(id, "set", { voltage: Number(point.voltage), current: Number(point.current) }, "V/A 设定已写入并读回");
  }
  function setSupplyOutput(id, on) {
    const point = supplyPoint(id,setpoints[id]);
    void runAction(id, "output", { on, voltage: Number(point.voltage), current: Number(point.current) }, on ? "电源输出开启已由设备状态确认" : "电源输出关闭已由设备状态确认");
  }

  const stopAll = () => {
    setSupplyOutput("brake-supply", false);
  };
  const changeRange = (value) => {
    if (recording.current || backendSessionRef.current?.running || engine.current.loadEnabled === true) return;
    update({ range: Number(value) === 100 ? 100 : 50 });
    setSamples([]);
    addEvent(`切换扭矩传感器量程配置至 ${value} N·m。`);
  };
  const startRecord = async () => {
    const connected = Object.values(devicesRef.current).some(device=>device.connected && device.snapshot);
    if (recording.current || backendSessionRef.current?.running || saving || recordBusyRef.current || !connected) return;
    recordBusyRef.current=true;setRecordBusy(true);
    try {
    const response=await fetchJson('/api/recordings/start',{method:'POST',headers:{'Content-Type':'application/json','X-MotorBench-Token':token.current},
      body:JSON.stringify({name:recordName.trim() || '未命名功率测试',range:state.range}),signal:AbortSignal.timeout(10000)});
    backendSessionRef.current=response.current;setBackendSession(response.current);
    const first = hardwareSample(devicesRef.current, 0);
    const record = {
      id: `hardware-${crypto.randomUUID()}`,
      name: recordName.trim() || "未命名功率测试",
      range: state.range,
      startedAt: Date.now(),
      engineOrigin: engine.current.t,
      samples: [first],
      fullRecordId:response.current.id,
      config: {
        fullRecordId:response.current.id,
        previewOnly:true,
        source: "hardware",
        sensorRangeNm: state.range,
        motorTargetSpeedRpm: null,
        brakeTargetCurrentA: Number.isFinite(state.targetCurrent) ? state.targetCurrent : null,
        motorConnected: state.motorConnected,
        sensorConnected: state.sensorConnected,
        torqueSensor: devicesRef.current["torque-sensor"]?.config || sensorConfig,
        motorSupply: configs["motor-supply"],
        brakeSupply: configs["brake-supply"],
      },
      events: [],
    };
    recording.current = record;
    setActiveRecord({ ...record, count: 1 });
    addEvent(`开始记录真实设备数据 · ${record.name}`);
    } catch(error) {setToast(`无法开始完整记录：${error.message}`);}
    finally {recordBusyRef.current=false;setRecordBusy(false);}
  };
  const downloadFull = (report,name='timeline.csv') => {
    const anchor=document.createElement('a');anchor.href=`/api/recordings/files/${encodeURIComponent(report.id)}/${name}`;
    anchor.download=name;anchor.click();
  };
  const download = (record, eventsOnly = false) => {
    if(record?.config?.fullRecordId && !eventsOnly){downloadFull({id:record.config.fullRecordId});return;}
    if (!record?.samples?.length || !Number.isFinite(new Date(record.startedAt).getTime())) {
      setToast("此记录无法导出，请检查本地记录提示。");
      return;
    }
    const blob = new Blob([eventsOnly ? eventCsv(record.events || []) : toCsv(record.samples)], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    const prefix = record.samples[0]?.source === "hardware" ? "HARDWARE" : "LEGACY";
    anchor.download = `${prefix}_${eventsOnly ? "EVENTS_" : ""}${record.range}Nm_${new Date(record.startedAt).toISOString().replaceAll(":", "-")}.csv`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setToast(`${prefix === "HARDWARE" ? "真实采集" : "历史演示"} CSV 已交给浏览器下载`);
  };

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 4500);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    const warn = (event) => {
      if (recording.current || saving || unsavedIds.current.size > 0) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [saving]);
  useEffect(() => {
    const syncRecords = (event) => {
      if (event.key !== STORE_KEY && event.key !== null) return;
      const latest = readRecords();
      recordsRef.current = mergeRecords(recordsRef.current, latest.records);
      const storedIds = new Set(latest.records.map((record) => record.id));
      unsavedIds.current = new Set(recordsRef.current.filter((record) => !storedIds.has(record.id)).map((record) => record.id));
      setRecords(recordsRef.current);
      setStorageError(latest.error || (unsavedIds.current.size ? "本地存储在其他页面发生变化，部分记录仅保留在当前页，请导出保存。" : ""));
    };
    window.addEventListener("storage", syncRecords);
    return () => window.removeEventListener("storage", syncRecords);
  }, []);

  function appendTrialSamples(points) {
    const trace=trial.current;
    if(trace?.append(points)&&trace.run.measurementEndedAt) {
      setFrozen(trace.snapshot());setFrozenRun(trace.run);
    }
  }
  function coupledState(run) {
    if (!run.id)return;
    if (run.running && trial.current?.run.id!==run.id) {
      trial.current=new LoadTrial(run);
      setFrozen(null);setFrozenRun(null);setCharacteristicView(true);
    }
    if (trial.current?.run.id!==run.id)return;
    const wasRunning=trial.current.run.running;
    const previousCutoff=trial.current.run.measurementEndedAt;
    trial.current.update(run);
    setTrialRun(run);
    if ((run.measurementEndedAt&&!previousCutoff)||(wasRunning && !run.running)) {
      const measured=trial.current.snapshot();
      // Keep an empty completed trace for delayed final batches; never replace
      // a stopped trial with live readings while waiting for those samples.
      setFrozen(measured);setFrozenRun(run);
    }
  }
  function resumeChart() {
    trial.current=null;setTrialRun(null);setFrozen(null);setFrozenRun(null);
  }
  const curveSamples=useMemo(()=>frozen || (trial.current?.run.running ? trial.current.snapshot():samples),[frozen,samples,trialRun]);
  const fullTrial=Boolean(frozenRun || trialRun?.running);
  const actions = useLiveActions({
    addEvent, connectSupply, disconnectSupply, readSupply, setSupplyValues, setSupplyOutput,
    refreshPorts: () => refreshPorts(true),
    limits: (id, limits) => runAction(id, "limits", limits, "电机电源最大限幅已应用"),
    updateDevice,
    showSupplies: () => document.getElementById("supply-controls")?.scrollIntoView({ behavior: "smooth", block: "start" }),
    motorState: payload => updateDevice("current-motor", payload),
    coupledStart: () => {setCharacteristicView(true);},
    coupledState,
  });
  const supplyActions = useMemo(() => Object.fromEntries(SUPPLIES.map(({id}) => [id, {
    onDeviceState: payload => actions.updateDevice(id, payload),
    setConfig: next => setConfigs(previous => ({...previous, [id]: next})),
    setPoint: next => setSetpoints(previous => ({...previous, [id]: next})),
  }])), [actions]);

  const sample = hardwareSample();
  const motorTemperature = motorTemperatureC(devices["current-motor"]);
  const liveCurrents = motorLiveCurrents(devices["current-motor"]);
  const lastRecord = records[0];
  const lastFullRecord = backendSession && !backendSession.running ? backendSession : fullHistory.find(row=>!row.running);
  const rangeLocked = Boolean(activeRecord || state.loadEnabled === true);
  const duration = backendSession?.running ? backendSession.elapsedS || 0 : activeRecord ? Math.max(0, state.t - activeRecord.engineOrigin) : 0;
  const disconnectedChannels = CHANNELS.filter((channel) => !state[channel.key]);
  const canRecord = recordServiceOnline && Object.values(devices).some(device=>device.connected && device.snapshot);
  const metricHasData = useMemo(() => samples.some(item => Number.isFinite(item[metric])), [samples, metric]);
  const historyValues = selected ? selected.samples.map((item) => item[historyMetric]).filter(Number.isFinite) : [];
  const historyUnit = { torque: "N·m", speed: "rpm", shaftPowerKw: "kW", current: "A" }[historyMetric];
  const display = (value, digits) => value == null ? "—" : value.toFixed(digits);

  return (
    <div className="app-shell hardware-app">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setView("bench");
          }}
          aria-label="Inntorque 扭矩工作台"
        >
          <span className="brand-mark">
            <i />
            <i />
            <i />
          </span>
          <div>
            Inntorque<span>TEST & MEASUREMENT</span>
          </div>
        </a>
        <div className="nav-label">工作空间</div>
        <nav aria-label="主导航">
          <button
            className={`nav-item ${view === "bench" ? "active" : ""}`}
            onClick={() => setView("bench")}
          >
            <Icon name="bench" />
            扭矩工作台
          </button>
          <button
            className={`nav-item ${view === "records" ? "active" : ""}`}
            onClick={() => setView("records")}
          >
            <Icon name="records" />
            试验记录<span className="nav-count">{records.length}</span>
          </button>
          <button className={`nav-item ${view === "specialized" ? "active" : ""}`} onClick={() => setView("specialized")}>
            <Icon name="bench" />专项测试
          </button>
        </nav>
        <div className="sidebar-bottom">
          <button className="nav-item" onClick={() => setSettings(true)}>
            <Icon name="settings" />
            设备与连接
          </button>
          <div className="version">
            <span>INTERFACE PREVIEW</span>
            <span>v0.2 · 真实设备模式</span>
          </div>
        </div>
      </aside>
      <main>
        <header className="page-header">
          <div>
            <div className="eyebrow">
              INNTORQUE LAB / {view === "bench" ? "WORKBENCH" : view === "specialized" ? "SPECIAL TESTS" : "RECORDINGS"}
            </div>
            <h1>{view === "bench" ? "扭矩工作台" : view === "specialized" ? "专项测试" : "试验记录"}</h1>
            <p>
              {view === "bench"
                ? "电机 · 减速箱 · 磁粉制动器"
                : view === "specialized" ? "独立测量 · 工况记录 · 原始数据与统计报告" : "回看加载过程，保留每一次试验。"}
            </p>
          </div>
          <div className="header-actions">
            <label className="profile-select">
              <span>设备量程</span>
              <select
                aria-label="设备量程配置"
                value={state.range}
                onChange={(e) => changeRange(e.target.value)}
                disabled={rangeLocked}
                title={
                  rangeLocked
                    ? "停止电机、关闭制动输出并结束记录后可切换"
                    : "设置扭矩传感器量程"
                }
              >
                <option value="50">50 N·m 配置</option>
                <option value="100">100 N·m 配置</option>
              </select>
            </label>
            <button
              className="button settings-button"
              onClick={() => setSettings(true)}
            >
              <Icon name="settings" size={17} />
              设备设置
            </button>
          </div>
        </header>
        <div className={`simulation-banner hardware-banner ${bridgeReady ? "ready" : ""}`}>
          <div>
            <Icon name="info" size={17} />
            <strong>{bridgeReady ? "真实设备模式" : "通信桥未启动"}</strong>
            <span>{bridgeReady ? "读数来自实际设备应答；未接入接口保持未连接" : "设备通道不可用；不会显示模拟连接或读数"}</span>
          </div>
          <button onClick={() => setSettings(true)}>
            查看接口状态
            <Icon name="info" size={15} />
          </button>
        </div>
        <section className="bench-status" aria-label="当前设备运行状态">
          <div className="bench-status-items">
            <span>
              <strong>电机</strong>
              {state.motorConnected ? (state.motorEnabled ? "运行中" : "未使能") : "未连接"}
            </span>
            <span>
              <strong>制动</strong>
              {state.loadEnabled == null ? "状态未知" : state.loadEnabled ? "输出开启" : "输出关闭"}
            </span>
            <span>
              <strong>记录</strong>
              {activeRecord
                ? `采集中 ${formatDuration(duration)}`
                : saving
                  ? "正在保存"
                  : "未记录"}
            </span>
          </div>
          <button
            className="button stop-all"
            onClick={stopAll}
            disabled={!bridgeReady}
            title="关闭制动电源输出；电机输出使用电流环面板停止"
          >
            <Icon name="stop" size={15} />
            关闭制动输出
          </button>
        </section>
        {disconnectedChannels.length > 0 && (
          <div role="alert" className="connection-alert">
            <p>
              <strong>
                {disconnectedChannels.map((ch) => ch.name).join("、")}
                未连接。
              </strong>
              {!state.sensorConnected
                ? "扭矩和转速不可用；"
                : "对应设备反馈不可用；"}
              {state.loadEnabled === true ? "制动电源输出仍开启。" : "未连接的接口不参与控制。"}
              {activeRecord
                ? "记录继续，未连接设备的读数留空。"
                : "双电源与传感器在下方配置，西格电机在电流环面板独立连接与步进控制。"}
            </p>
            <button className="button small" onClick={() => setSettings(true)}>
              检查连接
            </button>
          </div>
        )}
        <section className="connection-notice" aria-label="一键连接">
          <button className="button primary" onClick={connectAll}
            disabled={!bridgeReady || connectingAll || sensorBusy || Object.values(busy).some(Boolean) || devices["current-motor"]?.run?.running}>
            {connectingAll ? "正在轮询连接…" : "一键连接全部接口"}
          </button>
          <div role="status" aria-live="polite">
            {connectionReport.length ? connectionReport.map((line, index) => <p key={index}>{line}</p>)
              : <p>自动发现设备，按本机记住的 USB 标识恢复连接；首次使用的同型号电源需指定供电对象。连接不自动开启输出。</p>}
          </div>
        </section>
        {storageError && (
          <div role="alert" className="storage-error">
            {storageError}
          </div>
        )}
        {storageWarning && (
          <div role="status" className="storage-error">
            {storageWarning}
          </div>
        )}

        {view === "bench" ? (
          <>
            <section className="connections" aria-label="设备真实连接状态">
              {CHANNELS.map((ch) => (
                <button
                  key={ch.key}
                  className="connection"
                  onClick={() => setSettings(true)}
                >
                  <span className="device-icon">
                    <Icon name={ch.icon} />
                  </span>
                  <span className="connection-info">
                    <strong>{ch.name}</strong>
                    <span>{ch.detail}</span>
                  </span>
                  <span
                    className={`connection-status ${state[ch.key] ? "" : "disconnected"}`}
                  >
                    <i />
                    {state[ch.key] ? "已连接" : "未连接"}
                  </span>
                </button>
              ))}
            </section>
            <div className="hw-supply-grid" id="supply-controls">
              {SUPPLIES.map((supply) => (
                <SupplyPanel
                  key={supply.id}
                  supply={supply}
                  token={token}
                  {...supplyActions[supply.id]}
                  onEvent={actions.addEvent}
                  device={devices[supply.id]}
                  config={configs[supply.id]}
                  point={setpoints[supply.id]}
                  ports={ports}
                  bridgeReady={bridgeReady}
                  busy={busy[supply.id]}
                  onRefreshPorts={actions.refreshPorts}
                  onConnect={actions.connectSupply}
                  onDisconnect={actions.disconnectSupply}
                  onRead={actions.readSupply}
                  onSet={actions.setSupplyValues}
                  onOutput={actions.setSupplyOutput}
                  onLimits={actions.limits}
                />
              ))}
              <SensorPanel
                displayRate={displayRate}
                device={devices["torque-sensor"]}
                config={sensorConfig}
                setConfig={setSensorConfig}
                ports={ports}
                bridgeReady={bridgeReady}
                busy={sensorBusy}
                onRefreshPorts={actions.refreshPorts}
                onConnect={connectTorqueSensor}
                onDisconnect={disconnectTorqueSensor}
                onRead={() => runTorqueSensorAction("read")}
              />
            </div>
            <div className="workspace-grid">
              <section className="measurement-area" aria-label="测量与记录">
                <div className="readings">
                  <article
                    className={`reading torque-reading ${!state.sensorConnected ? "stale" : ""}`}
                  >
                    <div className="reading-label">
                      <span>减速箱输出扭矩</span>
                      <Icon name="sensor" size={20} />
                    </div>
                    <div className="value">
                      <span>
                        {sample.torque == null
                          ? "—"
                          : `${sample.torque >= 0 ? "+" : ""}${sample.torque.toFixed(Number(sample.torqueDecimals ?? 2))}`}
                      </span>
                      <small>N·m</small>
                    </div>
                    <div className="range-track">
                      <i
                        style={{
                          width: `${Math.min(100, (Math.abs(sample.torque || 0) / state.range) * 100)}%`,
                        }}
                      />
                    </div>
                    <div className="reading-foot">
                      <span>
                        {state.sensorConnected
                          ? "输出端 · 真实传感器读数"
                          : "传感器未连接 · 数据不可用"}
                      </span>
                      <span>量程 {state.range} N·m</span>
                    </div>
                  </article>
                  <article className="reading">
                    <div className="reading-label">
                      <span>输出转速</span>
                      <Icon name="pulse" size={19} />
                    </div>
                    <div className="value">
                      <span>{display(sample.speed, 0)}</span>
                      <small>rpm</small>
                    </div>
                    <div className="reading-foot">
                      <span>减速箱输出端</span>
                    </div>
                    <div className="secondary-line">
                      {state.sensorConnected
                        ? "来自扭矩传感器 · 实时读回"
                        : "传感器未连接 · 无转速读数"}
                    </div>
                  </article>
                  <article className="reading">
                    <div className="reading-label"><span>输出轴功率</span><Icon name="power" size={18} /></div>
                    <div className="value"><span>{display(Number.isFinite(sample.shaftPowerKw) ? sample.shaftPowerKw * 1000 : null, 1)}</span><small>W</small></div>
                    <div className="reading-foot"><span>{Number.isFinite(sample.shaftPowerKw) ? `${sample.shaftPowerKw.toFixed(6)} kW` : "传感器未连接 · 无功率读数"}</span></div>
                    <div className="secondary-line">手册公式：P = |T × n| / 9550</div>
                  </article>
                  <article className="reading current-reading" aria-label="电机目标与实时电流">
                    <div className="reading-label">
                      <span>电机电流</span>
                      <Icon name="power" size={18} />
                    </div>
                    <div className="current-line current-iq">
                      <span>实时 Iq</span><strong>{display(liveCurrents.iq, 3)} <small>A</small></strong>
                    </div>
                    <div className="current-line">
                      <span>目标 Iq</span><strong>{display(sample.current, 3)} <small>A</small></strong>
                    </div>
                    <div className="current-phases" aria-label="三相瞬时电流">
                      {['ia', 'ib', 'ic'].map((key, index) => <div className="current-line" key={key}>
                        <span>{['A', 'B', 'C'][index]} 相</span><strong>{display(liveCurrents[key], 3)} <small>A</small></strong>
                      </div>)}
                    </div>
                    <div className="reading-foot">
                      <span className={state.loadEnabled ? "running-text" : ""}>
                        {!state.motorConnected ? "电机未连接"
                          : state.motorEnabled ? "电机输出已开启" : "电机输出已关闭"}
                      </span>
                    </div>
                    <div className="secondary-line">
                      {liveCurrents.iq == null || [liveCurrents.ia, liveCurrents.ib, liveCurrents.ic].some(value => value == null)
                        ? "— 表示反馈缺失或已过期" : "三相为瞬时采样值"}
                    </div>
                  </article>
                  <article className="reading" aria-label="电机温度读数">
                    <div className="reading-label"><span>电机温度</span></div>
                    <div className="value"><span>{display(motorTemperature, 1)}</span><small>°C</small></div>
                    <div className="reading-foot"><span>{!state.motorConnected ? "电机未连接 · 无温度读数"
                      : devices["current-motor"]?.snapshot?.ntc?.fault ? "NTC 故障"
                      : motorTemperature == null ? "温度无效或已过期"
                      : devices["current-motor"]?.snapshot?.ntc?.tripped_latched || (Number.isFinite(devices["current-motor"]?.snapshot?.ntc?.limit_c) && motorTemperature >= devices["current-motor"].snapshot.ntc.limit_c) ? "过温保护"
                      : devices["current-motor"]?.snapshot?.ntc?.over ? "温度偏高 · 暂不可重新启动" : "NTC 实时反馈"}</span></div>
                  </article>
                </div>
                <section className="panel chart-panel">
                  <div className="panel-heading">
                    <div>
                      <h2>{characteristicView ? "扭矩特性曲线" : "实时曲线"}</h2>
                      <span className="panel-caption">
                        {frozen
                          ? (frozenRun ? `试验曲线已保留 · ${frozenRun.reason || '试验结束'}` : "视图已暂停 · 采集仍在继续")
                          : fullTrial ? "本次固定负载试验全过程 · 实测数据" : "真实设备读回 · 未接入的通道保留空值"}
                      </span>
                    </div>
                    <div className="chart-tools">
                      <select
                        aria-label="曲线时间范围"
                        value={windowSeconds}
                        disabled={fullTrial}
                        onChange={(e) =>
                          setWindowSeconds(Number(e.target.value))
                        }
                      >
                        <option value="30">最近 30 秒</option>
                        <option value="60">最近 60 秒</option>
                      </select>
                      <button
                        className={`button small ${frozen ? "selected" : ""}`}
                        onClick={() => {if(frozen)resumeChart();else {setFrozen([...curveSamples]);setFrozenRun(trialRun);}}}
                      >
                        <Icon name={frozen ? "play" : "pause"} size={15} />
                        {frozen ? "恢复实时" : "暂停曲线"}
                      </button>
                    </div>
                  </div>
                  <div className="chart-subhead">
                    <button className="button small" aria-pressed={characteristicView}
                      onClick={() => setCharacteristicView(!characteristicView)}>
                      {characteristicView ? "查看时间曲线" : "查看扭矩特性曲线"}
                    </button>
                  </div>
                  {!characteristicView && <>
                  <div className="chart-subhead">
                    <div className="metric-tabs" aria-label="曲线指标">
                      {[
                        ["torque", "扭矩", "N·m"],
                        ["speed", "转速", "rpm"],
                        ["shaftPowerKw", "轴功率", "kW"],
                        ["current", "目标电流", "A"],
                      ].map(([key, name, unit]) => (
                        <button
                          key={key}
                          className={metric === key ? `active ${key}` : ""}
                          aria-pressed={metric === key}
                          onClick={() => setMetric(key)}
                        >
                          <i />
                          {name}
                          <span>{unit}</span>
                        </button>
                      ))}
                    </div>
                    <span className="chart-live">
                      {activeRecord && (
                        <>
                          <i />
                          记录中
                        </>
                      )}
                    </span>
                  </div>
                  <Plot
                    samples={curveSamples}
                    metric={metric}
                    range={state.range}
                    windowSeconds={fullTrial?Math.max(windowSeconds,(curveSamples.at(-1)?.t||0)-(curveSamples[0]?.t||0)):windowSeconds}
                    paused={Boolean(frozen)}
                    empty={!metricHasData && !frozen}
                    onConnect={actions.showSupplies}
                  />
                  <div className="chart-footer">
                    <span>
                      <span className={`legend-line ${metric}`} />
                      {metric === "torque"
                        ? "减速箱输出扭矩"
                        : metric === "speed"
                          ? "输出端转速"
                          : metric === "shaftPowerKw"
                            ? "计算轴功率幅值 · |T×n|/9550"
                            : "电机控制器已应答的目标 Iq"}
                    </span>
                    <span>时间 / mm:ss · 断开处保留缺口</span>
                  </div>
                  </>}
                  {characteristicView && <TorqueCharacteristics samples={curveSamples} windowSeconds={fullTrial?Infinity:windowSeconds}
                    loadRun={frozenRun || (trialRun?.running?trialRun:null)} />}
                </section>
                <section
                  className={`panel recording-panel ${activeRecord ? "is-recording" : ""}`}
                  aria-label="试验记录操作"
                >
                  <div className="record-heading">
                    <div
                      className={`record-icon ${activeRecord ? "recording" : ""}`}
                    >
                      <Icon
                        name={activeRecord ? "pulse" : "records"}
                        size={21}
                      />
                    </div>
                    <div>
                      <h2>
                        {activeRecord ? "正在记录真实设备数据" : "记录这次试验"}
                      </h2>
                      <p>
                        {activeRecord
                          ? `${formatDuration(duration)} · ${activeRecord.count} 条完整记录 · 后台写入磁盘`
                          : lastFullRecord || lastRecord
                            ? "上次记录已结束，可导出或继续新试验"
                            : "调整好参数后，随时开始记录"}
                      </p>
                    </div>
                    <span className="record-badge">CSV</span>
                  </div>
                  <div className="record-controls">
                    <input
                      aria-label="试验名称"
                      value={recordName}
                      onChange={(e) => setRecordName(e.target.value)}
                      placeholder="试验名称"
                      maxLength="60"
                      disabled={Boolean(activeRecord)}
                    />
                    <button
                      className={`button ${activeRecord ? "record-stop" : "primary"}`}
                      onClick={() =>
                        activeRecord ? saveFinished() : startRecord()
                      }
                      disabled={recordBusy || (!activeRecord && (!canRecord || saving))}
                    >
                      <Icon name={activeRecord ? "stop" : "plus"} size={16} />
                      {activeRecord
                        ? "停止记录"
                        : saving || recordBusy
                          ? "正在保存"
                          : "开始记录"}
                    </button>
                    <button
                      className="button"
                      onClick={() => lastFullRecord ? downloadFull(lastFullRecord) : lastRecord && download(lastRecord)}
                      disabled={(!lastFullRecord && !lastRecord) || Boolean(activeRecord) || saving || recordBusy}
                    >
                      <Icon name="download" size={16} />
                      导出 CSV
                    </button>
                  </div>
                  {lastFullRecord && !activeRecord && <div className="hw-output-actions">
                    <button className="button" onClick={()=>downloadFull(lastFullRecord,'timeline.jsonl')}>导出完整字段 JSONL</button>
                    <button className="button" onClick={()=>downloadFull(lastFullRecord,'report.json')}>导出统计报告</button>
                  </div>}
                  {lastFullRecord?.state==='interrupted' && !activeRecord && <p className="hw-inline-error">记录中断，已有数据保留：{lastFullRecord.reason}</p>}
                  {!recordServiceOnline && <p className="hw-inline-error">完整记录服务暂不可用，请等待恢复后重试。</p>}
                  <div
                    className={`record-note ${!canRecord && !activeRecord ? "error-text" : ""}`}
                  >
                    {activeRecord
                      ? "完整参数与后台专用记录均已开启，扭矩逐帧保存。点击停止记录会同步结束两份记录，不会停止电机或改变电源输出。"
                      : !canRecord
                        ? "连接并读取至少一个真实设备后，才可开始记录完整参数。"
                        : '点击开始记录，同时开启完整参数与后台专用记录；未开始时不记录。停止记录后同步结束保存，导出 CSV 为完整时间序列。'}
                  </div>
                </section>
                <section className="event-section">
                  <button
                    className="event-toggle"
                    onClick={() => setShowEvents(!showEvents)}
                    aria-expanded={showEvents}
                  >
                    <Icon name="clock" size={16} />
                    <strong>操作事件</strong>
                    <time>
                      {events[0].at.toLocaleTimeString("zh-CN", {
                        hour12: false,
                      })}
                    </time>
                    <span>{events[0].text}</span>
                    <Icon
                      name="chevron"
                      size={16}
                      className={showEvents ? "rotated" : ""}
                    />
                  </button>
                  {showEvents && (
                    <ol className="event-list">
                      {events.map((event, i) => (
                        <li key={i}>
                          <time>
                            {event.at.toLocaleTimeString("zh-CN", {
                              hour12: false,
                            })}
                          </time>
                          <span>{event.text}</span>
                        </li>
                      ))}
                    </ol>
                  )}
                </section>
              </section>
              <aside className="control-area" aria-label="试验控制">
                <CoupledCurrentPanel token={token} bridgeReady={bridgeReady}
                  curveHeld={Boolean(frozenRun && !trialRun?.running)}
                  sensorConnected={Boolean(devices['torque-sensor']?.connected)}
                  outputSpeedRpm={devices['torque-sensor']?.connected && Number.isFinite(devices['torque-sensor']?.snapshot?.speed)
                    && Date.now()/1000-devices['torque-sensor'].snapshot.receivedAt<=1.5
                    ?Math.abs(devices['torque-sensor'].snapshot.speed):null}
                  motorSupplyOn={Boolean(devices['motor-supply']?.connected && (devices['motor-supply']?.snapshot?.status & 128))}
                  onMotorState={actions.motorState} onBrakeState={supplyActions['brake-supply'].onDeviceState}
                  onEvent={actions.addEvent} onStart={actions.coupledStart} onRunState={actions.coupledState} />
                <details className="motor-single-controls">
                  <summary>电机单独控制 / 详细反馈</summary>
                <MotorCurrentPanel token={token} bridgeReady={bridgeReady}
                  onState={actions.motorState} onEvent={actions.addEvent} />
                </details>
                <div className="control-hint"><Icon name="info" size={17} /><p>扭矩和转速必须来自真实传感器。制动电源的 V/A 设置与输出状态由设备应答确认。</p></div>
              </aside>
            </div>
            <EncoderCompareView devices={devices} />
          </>
        ) : view === "specialized" ? (
          <SpecializedTestsPanel token={token} bridgeReady={bridgeReady} onWorkbench={setView} />
        ) : (
          <section className="records-view">
            <div className="records-toolbar">
              <div>
                <h2>
                  本地记录 <span>{records.length}</span>
                </h2>
                <p>保留本地历史记录；新记录只保存设备实际读回的数据。</p>
              </div>
              <button
                className="button primary"
                onClick={() => setView("bench")}
              >
                <Icon name="plus" size={17} />
                {activeRecord ? "返回正在记录的试验" : "返回工作台"}
              </button>
            </div>
            {fullHistory.length>0 && <section className="panel" aria-label="完整参数记录历史"><h3>完整参数记录（磁盘保存）</h3>
              <div className="records-table-wrap"><table className="records-table"><thead><tr><th>试验名称</th><th>开始时间</th><th>状态</th><th>完整数据</th></tr></thead><tbody>
                {fullHistory.map(row=><tr key={row.id}><td>{row.name}</td><td>{new Date(row.startedAt*1000).toLocaleString()}</td><td>{row.running?'记录中':row.state==='interrupted'?`中断：${row.reason}`:'已保存'}</td><td>
                  {!row.running && <><button className="button small" onClick={()=>downloadFull(row)}>CSV</button> <button className="button small" onClick={()=>downloadFull(row,'timeline.jsonl')}>JSONL</button> <button className="button small" onClick={()=>downloadFull(row,'report.json')}>报告</button></>}
                </td></tr>)}
              </tbody></table></div></section>}
            {records.length === 0 && fullHistory.length===0 ? (
              <div className="empty-records">
                <Icon name="records" size={42} />
                <h3>暂无试验记录</h3>
                <p>接入真实电源或传感器并开始记录后，数据会保存在这里。</p>
                <button className="button" onClick={() => setView("bench")}>
                  前往工作台
                </button>
              </div>
            ) : (
              <div className="records-table-wrap">
                <table className="records-table">
                  <thead>
                    <tr>
                      <th>试验名称</th>
                      <th>配置 / 来源</th>
                      <th>开始时间</th>
                      <th>时长</th>
                      <th>数据点</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {records.map((record) => (
                      <tr
                        key={record.id}
                        className={
                          selected?.id === record.id ? "selected-row" : ""
                        }
                      >
                        <td>
                          <button
                            className="record-name"
                            onClick={() => {
                              setSelected(record);
                              setHistoryMetric("torque");
                            }}
                          >
                            {record.name}
                          </button>
                        </td>
                        <td>
                          {record.range} N·m{" "}
                          <span className="demo-tag">{record.samples[0]?.source === "hardware" ? "真实采集" : "历史演示数据"}</span>
                          {unsavedIds.current.has(record.id) && (
                            <span className="error-text"> · 未保存</span>
                          )}
                        </td>
                        <td>
                          {new Date(record.startedAt).toLocaleString("zh-CN", {
                            hour12: false,
                          })}
                        </td>
                        <td className="mono">
                          {formatDuration(record.duration)}
                        </td>
                        <td className="mono">{record.samples.length}</td>
                        <td>
                          <button
                            className="button small"
                            aria-label={`导出 ${record.name}`}
                            onClick={() => download(record)}
                          >
                            <Icon name="download" size={15} />
                            CSV
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {selected && (
              <section className="panel history-chart history-details">
                <div className="panel-heading">
                  <div>
                    <h2>{selected.name}</h2>
                    <span className="panel-caption">
                      {selected.config?.fullRecordId?'压缩预览 · 导出 CSV 为完整时间序列':'完整记录'} · 相对试验起点计时 · {selected.samples[0]?.source === "hardware" ? "真实设备采集" : "历史演示数据"}
                    </span>
                  </div>
                  <button
                    className="icon-button"
                    aria-label="关闭记录详情"
                    onClick={() => setSelected(null)}
                  >
                    <Icon name="close" />
                  </button>
                </div>
                <dl className="record-summary">
                  <div>
                    <dt>试验时长</dt>
                    <dd>{formatDuration(selected.duration)}</dd>
                  </div>
                  <div>
                    <dt>当前指标有效点</dt>
                    <dd>
                      {historyValues.length} / {selected.samples.length}
                    </dd>
                  </div>
                  <div>
                    <dt>缺失点</dt>
                    <dd>{selected.samples.length - historyValues.length}</dd>
                  </div>
                  <div>
                    <dt>峰值绝对值</dt>
                    <dd>
                      {historyValues.length
                        ? historyValues
                            .reduce(
                              (peak, value) => Math.max(peak, Math.abs(value)),
                              0,
                            )
                            .toFixed(historyMetric === "current" || historyMetric === "shaftPowerKw" ? 3 : 2)
                        : "—"}{" "}
                      {historyUnit}
                    </dd>
                  </div>
                </dl>
                <div className="chart-subhead">
                  <div className="metric-tabs" aria-label="历史曲线指标">
                    {[
                      ["torque", "扭矩", "N·m"],
                      ["speed", "转速", "rpm"],
                      ["shaftPowerKw", "轴功率", "kW"],
                      ["current", "目标电流", "A"],
                    ].map(([key, name, unit]) => (
                      <button
                        key={key}
                        className={historyMetric === key ? `active ${key}` : ""}
                        aria-pressed={historyMetric === key}
                        onClick={() => setHistoryMetric(key)}
                      >
                        <i />
                        {name}
                        <span>{unit}</span>
                      </button>
                    ))}
                  </div>
                </div>
                <Plot
                  samples={relativeSamples(selected.samples)}
                  metric={historyMetric}
                  range={selected.range}
                  windowSeconds={Math.max(
                    1,
                    selected.samples.at(-1).t - selected.samples[0].t,
                  )}
                />
                <div className="record-config">
                  <strong>开始时的配置</strong>
                  {selected.config ? (
                    <dl>
                      <div>
                        <dt>传感器量程</dt>
                        <dd>{selected.range} N·m</dd>
                      </div>
                      <div>
                        <dt>电机目标转速</dt>
                        <dd>
                          {Number.isFinite(selected.config.motorTargetSpeedRpm)
                            ? selected.config.motorTargetSpeedRpm
                            : "—"}{" "}
                          rpm
                        </dd>
                      </div>
                      <div>
                        <dt>制动目标电流</dt>
                        <dd>
                          {Number.isFinite(selected.config.brakeTargetCurrentA)
                            ? selected.config.brakeTargetCurrentA.toFixed(2)
                            : "—"}{" "}
                          A
                        </dd>
                      </div>
                      <div>
                        <dt>电机电源</dt>
                        <dd>待接入</dd>
                      </div>
                    </dl>
                  ) : (
                    <p>此旧记录未保存配置快照。</p>
                  )}
                </div>
                <details className="record-events">
                  <summary>
                    操作事件 · {selected.events?.length || 0} 条
                  </summary>
                  {selected.events?.length ? (
                    <>
                      <div className="record-events-head">
                        <span>设定、连接和输出变化均按试验时间排列。</span>
                        <button
                          className="button small"
                          onClick={() => download(selected, true)}
                        >
                          导出事件 CSV
                        </button>
                      </div>
                      <ol className="event-list">
                        {selected.events.map((event, index) => (
                          <li key={index}>
                            <time title={event.at}>
                              {formatDuration(event.t)}
                            </time>
                            <span>{event.text}</span>
                          </li>
                        ))}
                      </ol>
                    </>
                  ) : (
                    <p>此旧记录未保存操作事件。</p>
                  )}
                </details>
              </section>
            )}
          </section>
        )}
        <footer className="page-footer">
          <span>
            <i />
            INNTORQUE ENGINEERING
          </span>
          <span>未接入设备的读数保持空值</span>
        </footer>
      </main>
      {settings && (
        <Settings
          onClose={() => setSettings(false)}
          state={state}
          devices={devices}
          bridgeReady={bridgeReady}
          stopAll={stopAll}
        />
      )}
      <div className={`toast ${toast ? "visible" : ""}`} role="status">
        <Icon name="check" size={17} />
        {toast}
      </div>
    </div>
  );
}
