import React, { useEffect, useRef, useState } from "react";
import {
  createDemoState,
  stepDemo,
  sampleFromState,
  validateSetpoint,
  toCsv,
  formatDuration,
} from "./model.js";

const STORE_KEY = "inntorque.demo-recordings.v1";
const MAX_SAMPLES = 1500;
const CHANNELS = [
  {
    key: "motorConnected",
    name: "电机驱动板",
    icon: "motor",
    detail: "LC_CANABLE_V2.1 · CAN",
  },
  {
    key: "sensorConnected",
    name: "动态扭矩传感器",
    icon: "sensor",
    detail: "EVG-D200 · RS485",
  },
  {
    key: "supplyConnected",
    name: "制动电源",
    icon: "power",
    detail: "电流控制 · 网口转 RS485",
  },
  {
    key: "motorSupply",
    name: "电机电源",
    icon: "power",
    detail: "电机供电 · 暂按 RS485",
    pending: true,
  },
];

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

function loadSaved() {
  try {
    const data = JSON.parse(localStorage.getItem(STORE_KEY) || "[]");
    if (!Array.isArray(data))
      return { records: [], error: "本地记录格式无法识别，原始数据未修改。" };
    const records = data.filter(
      (r) =>
        r &&
        typeof r.id === "string" &&
        Array.isArray(r.samples) &&
        r.samples.every((s) => s?.source === "simulation"),
    );
    return {
      records,
      error:
        records.length !== data.length
          ? "部分本地记录无法读取，原始数据未修改。"
          : "",
    };
  } catch {
    return { records: [], error: "无法读取浏览器本地记录。新记录仍可导出。" };
  }
}

function Plot({
  samples,
  metric,
  range,
  paused,
  empty,
  onDemo,
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
    speed: { label: "输出转速", unit: "rpm", color: "#318978", max: 1500 },
    current: { label: "制动加载电流", unit: "A", color: "#d28131", max: 2 },
  }[metric];
  const { width, height } = plotSize;
  const tickCount = width < 500 ? 3 : 6;
  const left = 48,
    right = 24,
    top = 20,
    bottom = 36;
  const end = Math.max(windowSeconds, samples.at(-1)?.t || 0),
    start = end - windowSeconds;
  const visible = samples.filter((s) => s.t >= start);
  let path = "",
    drawing = false;
  for (const sample of visible) {
    const value = sample[metric];
    if (!Number.isFinite(value)) {
      drawing = false;
      continue;
    }
    const x =
      left + ((sample.t - start) / windowSeconds) * (width - left - right);
    const y =
      top +
      (1 - Math.min(1, Math.max(0, value / meta.max))) *
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
        aria-label={`${meta.label}模拟曲线，单位${meta.unit}${paused ? "，视图已暂停" : ""}`}
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
                {Number(((1 - v) * meta.max).toFixed(1))}
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
                {formatDuration(start + (v / tickCount) * windowSeconds)}
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
          <span>让第一条加载曲线跑起来</span>
          <p>从启动电机到调节负载，先体验一次模拟试验。</p>
          <button className="button primary" onClick={onDemo}>
            <Icon name="play" size={16} />
            运行加载演示
          </button>
        </div>
      )}
    </div>
  );
}

function Setpoint({ kind, value, onChange, applied, connected, onApply }) {
  const current = kind === "current",
    step = current ? 0.05 : 50,
    max = current ? 2 : 1500;
  const validation = validateSetpoint(value, kind);
  const changed = validation.ok && validation.value !== applied;
  const adjust = (direction) =>
    onChange(
      String(
        Number(
          Math.min(
            max,
            Math.max(0, (Number(value) || 0) + direction * step),
          ).toFixed(2),
        ),
      ),
    );
  return (
    <>
      <label className="field-label" htmlFor={`set-${kind}`}>
        {current ? "目标电流" : "电机目标转速"}
        <span>{current ? "A" : "rpm"}</span>
      </label>
      <div className="stepper">
        <button
          aria-label={current ? "减小电流" : "降低转速"}
          onClick={() => adjust(-1)}
          disabled={!connected}
        >
          <Icon name="minus" size={16} />
        </button>
        <input
          id={`set-${kind}`}
          type="number"
          min="0"
          max={max}
          step={current ? 0.01 : "any"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={!connected}
          aria-invalid={!validation.ok}
          aria-describedby={`${kind}-hint`}
        />
        <button
          aria-label={current ? "增大电流" : "提高转速"}
          onClick={() => adjust(1)}
          disabled={!connected}
        >
          <Icon name="plus" size={16} />
        </button>
      </div>
      <div
        id={`${kind}-hint`}
        className={`setpoint-hint ${!validation.ok ? "error-text" : ""}`}
      >
        {!validation.ok ? (
          validation.error
        ) : (
          <>
            <span>
              已应用 {current ? applied.toFixed(2) : applied}{" "}
              {current ? "A" : "rpm"}
            </span>
            {changed && <span className="pending">待应用</span>}
          </>
        )}
      </div>
      <button
        className="button apply"
        disabled={!connected || !validation.ok || !changed}
        onClick={() => onApply(validation.value)}
      >
        应用{current ? "电流" : "转速"}
      </button>
    </>
  );
}

function Settings({ onClose, state, toggleConnection }) {
  const dialog = useRef(null);
  useEffect(() => {
    dialog.current.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="settings-dialog"
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      aria-labelledby="settings-title"
    >
      <div className="dialog-head">
        <div>
          <div className="eyebrow">DEVICE SETUP</div>
          <h2 id="settings-title">设备与连接</h2>
        </div>
        <button
          className="icon-button"
          aria-label="关闭设备设置"
          onClick={onClose}
        >
          <Icon name="close" />
        </button>
      </div>
      <div className="dialog-body">
        <div className="notice">
          <Icon name="flask" />
          <div>
            <strong>当前为交互原型</strong>
            <p>
              此页面不打开串口、不连接网关，也不发送控制命令。可以模拟断开设备，检查界面的反馈。
            </p>
          </div>
        </div>
        {CHANNELS.map((ch) => (
          <section className="device-setup" key={ch.key}>
            <div className="setup-title">
              <span className="device-icon">
                <Icon name={ch.icon} />
              </span>
              <div>
                <h3>{ch.name}</h3>
                <p>{ch.detail}</p>
              </div>
              <button
                className="button small"
                onClick={() => toggleConnection(ch.key)}
                disabled={ch.pending}
              >
                {ch.pending
                  ? "等待协议"
                  : state[ch.key]
                    ? "模拟断开"
                    : "模拟连接"}
              </button>
            </div>
            <dl>
              {ch.key === "motorConnected" ? (
                <>
                  <div>
                    <dt>接入计划</dt>
                    <dd>USB CDC / SLCAN · Web Serial</dd>
                  </div>
                  <div>
                    <dt>待确认</dt>
                    <dd>驱动板 CAN 报文、速率、减速比</dd>
                  </div>
                </>
              ) : ch.key === "sensorConnected" ? (
                <>
                  <div>
                    <dt>设备量程</dt>
                    <dd>{state.range} N·m（演示配置）</dd>
                  </div>
                  <div>
                    <dt>接入计划</dt>
                    <dd>USB-RS485 · Modbus RTU</dd>
                  </div>
                  <div>
                    <dt>待验证</dt>
                    <dd>19200 baud、地址 1、停止位与缩放</dd>
                  </div>
                </>
              ) : ch.key === "motorSupply" ? (
                <>
                  <div>
                    <dt>作用</dt>
                    <dd>给电机驱动板供电</dd>
                  </div>
                  <div>
                    <dt>供电电压读回</dt>
                    <dd>— V</dd>
                  </div>
                  <div>
                    <dt>供电电流读回</dt>
                    <dd>— A</dd>
                  </div>
                  <div>
                    <dt>反馈状态</dt>
                    <dd>待接入，不代表电源已关闭</dd>
                  </div>
                  <div>
                    <dt>待确认</dt>
                    <dd>电源型号、协议与网页控制范围</dd>
                  </div>
                </>
              ) : (
                <>
                  <div>
                    <dt>接入计划</dt>
                    <dd>网口转 RS485 · 按网关协议适配</dd>
                  </div>
                  <div>
                    <dt>待确认</dt>
                    <dd>电源、网关型号，电流范围与反馈</dd>
                  </div>
                </>
              )}
            </dl>
          </section>
        ))}
        <div className="demo-note">
          <strong>机械链路与测量位置</strong>
          <p>
            电机 → 减速箱 → 动态扭矩传感器 →
            磁粉制动器。制动器只做负载；主读数是减速箱输出扭矩，未换算为电机轴扭矩。电机供电电流来自电源端，不是电机相电流。
          </p>
        </div>
        <div className="demo-note">
          <strong>演示参数</strong>
          <p>
            转速 0–1500 rpm，制动电流 0–2 A，演示减速比
            1:1。这些数值和电流—扭矩关系仅用于体验界面，不代表实际设备规格。电机电源读回尚未模拟。切换
            50/100 N·m 只切换演示配置。
          </p>
        </div>
      </div>
      <div className="dialog-foot">
        <button className="button primary" onClick={onClose}>
          完成
        </button>
      </div>
    </dialog>
  );
}

export default function App() {
  const [initial] = useState(loadSaved);
  const [records, setRecords] = useState(initial.records);
  const recordsRef = useRef(initial.records);
  const engine = useRef(createDemoState());
  const [state, setState] = useState(engine.current);
  const [samples, setSamples] = useState([]);
  const [view, setView] = useState("bench");
  const [settings, setSettings] = useState(false);
  const [metric, setMetric] = useState("torque");
  const [windowSeconds, setWindowSeconds] = useState(30);
  const [frozen, setFrozen] = useState(null);
  const [speedDraft, setSpeedDraft] = useState("300");
  const [currentDraft, setCurrentDraft] = useState("0.5");
  const [events, setEvents] = useState([
    { at: new Date(), text: "模拟设备已就绪，电机与制动输出均关闭。" },
  ]);
  const [toast, setToast] = useState("");
  const [storageError, setStorageError] = useState(initial.error);
  const recording = useRef(null);
  const [activeRecord, setActiveRecord] = useState(null);
  const [recordName, setRecordName] = useState("减速箱加载试验");
  const [selected, setSelected] = useState(null);
  const [hasRun, setHasRun] = useState(false);
  const [showEvents, setShowEvents] = useState(false);

  const addEvent = (text) =>
    setEvents((prev) => [{ at: new Date(), text }, ...prev].slice(0, 100));
  const update = (patch) => {
    engine.current = { ...engine.current, ...patch };
    setState(engine.current);
  };
  const saveFinished = (message = "模拟记录已完成") => {
    const record = recording.current;
    if (!record) return;
    recording.current = null;
    const complete = {
      ...record,
      duration: Math.max(0, (Date.now() - record.startedAt) / 1000),
      finishedAt: Date.now(),
    };
    const next = [complete, ...recordsRef.current];
    recordsRef.current = next;
    setRecords(next);
    setActiveRecord(null);
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(next));
      setStorageError("");
    } catch {
      setStorageError(
        "浏览器本地保存失败，请及时导出；当前记录仍保留在此页面。",
      );
    }
    addEvent(`${message} · ${complete.samples.length} 个数据点`);
    setToast(`${message}，可导出 CSV`);
  };
  const finishRef = useRef(saveFinished);
  finishRef.current = saveFinished;

  useEffect(() => {
    let last = performance.now();
    const timer = setInterval(() => {
      const now = performance.now(),
        dt = (now - last) / 1000;
      last = now;
      engine.current = stepDemo(engine.current, dt);
      setState(engine.current);
      const sample = sampleFromState(engine.current);
      setSamples((prev) => [...prev, sample].slice(-310));
      if (recording.current) {
        recording.current.samples.push(sample);
        setActiveRecord({
          ...recording.current,
          count: recording.current.samples.length,
        });
        if (recording.current.samples.length >= MAX_SAMPLES)
          finishRef.current("本次演示已达 1500 个数据点，记录自动完成");
      }
    }, 200);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 4500);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    const warn = (event) => {
      if (recording.current || storageError) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [storageError]);

  const toggleConnection = (key) => {
    const connected = !engine.current[key];
    const patch = { [key]: connected };
    if (!connected && key === "motorConnected") patch.motorEnabled = false;
    if (!connected && key === "supplyConnected") patch.loadEnabled = false;
    update(patch);
    addEvent(
      `${CHANNELS.find((ch) => ch.key === key).name}已模拟${connected ? "连接" : "断开"}${key === "sensorConnected" ? "" : "，输出保持关闭"}`,
    );
  };
  const changeRange = (value) => {
    if (recording.current || state.motorEnabled || state.loadEnabled) return;
    const connections = Object.fromEntries(
      CHANNELS.filter((ch) => !ch.pending).map((ch) => [
        ch.key,
        engine.current[ch.key],
      ]),
    );
    engine.current = { ...createDemoState(Number(value)), ...connections };
    setState(engine.current);
    setSamples([]);
    setFrozen(null);
    setHasRun(false);
    setSpeedDraft("300");
    setCurrentDraft("0.5");
    addEvent(`切换至 ${value} N·m 演示配置，输出保持关闭。`);
  };
  const runDemo = () => {
    update({
      targetSpeed: 600,
      targetCurrent: 1.2,
      motorEnabled: true,
      loadEnabled: true,
      sensorConnected: true,
      motorConnected: true,
      supplyConnected: true,
    });
    setSpeedDraft("600");
    setCurrentDraft("1.2");
    setHasRun(true);
    addEvent("启动加载演示 · 电机 600 rpm，制动电流 1.20 A。");
  };
  const apply = (kind, value) => {
    update({ [kind === "speed" ? "targetSpeed" : "targetCurrent"]: value });
    addEvent(
      `模拟${kind === "speed" ? "电机转速" : "加载电流"}已设定为 ${value} ${kind === "speed" ? "rpm" : "A"}。`,
    );
  };
  const toggleOutput = (key) => {
    const enabled = !state[key];
    update({ [key]: enabled });
    if (enabled) setHasRun(true);
    addEvent(
      key === "motorEnabled"
        ? enabled
          ? "模拟电机已启动。"
          : "模拟电机已停止使能，转速逐步回落。"
        : enabled
          ? "模拟制动输出已开启。"
          : "模拟制动电源输出已关闭。",
    );
  };
  const startRecord = () => {
    const record = {
      id: `demo-${Date.now()}`,
      name: recordName.trim() || "未命名加载试验",
      range: state.range,
      startedAt: Date.now(),
      samples: [sampleFromState(engine.current)],
    };
    recording.current = record;
    setActiveRecord({ ...record, count: 1 });
    addEvent(`开始模拟记录 · ${record.name}`);
  };
  const download = (record) => {
    const blob = new Blob([toCsv(record.samples)], {
      type: "text/csv;charset=utf-8;",
    });
    const url = URL.createObjectURL(blob),
      a = document.createElement("a");
    a.href = url;
    a.download = `SIMULATION_${record.range}Nm_${new Date(record.startedAt).toISOString().replaceAll(":", "-")}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setToast("模拟 CSV 已交给浏览器下载");
  };
  const sample = sampleFromState(state);
  const lastRecord = records[0];
  const rangeLocked = Boolean(
    activeRecord || state.motorEnabled || state.loadEnabled,
  );
  const duration = activeRecord
    ? (Date.now() - activeRecord.startedAt) / 1000
    : 0;
  const display = (value, digits) =>
    value == null ? "—" : value.toFixed(digits);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setView("bench");
          }}
          aria-label="Inntorque 试验工作台"
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
            试验工作台
          </button>
          <button
            className={`nav-item ${view === "records" ? "active" : ""}`}
            onClick={() => setView("records")}
          >
            <Icon name="records" />
            试验记录<span className="nav-count">{records.length}</span>
          </button>
        </nav>
        <div className="sidebar-bottom">
          <button className="nav-item" onClick={() => setSettings(true)}>
            <Icon name="settings" />
            设备与连接
          </button>
          <div className="version">
            <span>INTERFACE PREVIEW</span>
            <span>v0.1 · 模拟原型</span>
          </div>
        </div>
      </aside>
      <main>
        <header className="page-header">
          <div>
            <div className="eyebrow">
              INNTORQUE LAB / {view === "bench" ? "WORKBENCH" : "RECORDINGS"}
            </div>
            <h1>{view === "bench" ? "试验工作台" : "试验记录"}</h1>
            <p>
              {view === "bench"
                ? "电机 · 减速箱 · 磁粉制动器"
                : "回看加载过程，保留每一次试验。"}
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
                    : "切换演示配置"
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
        <div className="simulation-banner">
          <div>
            <Icon name="flask" size={17} />
            <strong>模拟演示</strong>
            <span>所有读数与操作均为模拟，未连接真实设备</span>
          </div>
          <button onClick={() => setSettings(true)}>
            查看接入方案
            <Icon name="info" size={15} />
          </button>
        </div>
        {storageError && (
          <div role="alert" className="storage-error">
            {storageError}
          </div>
        )}

        {view === "bench" ? (
          <>
            <section className="connections" aria-label="模拟设备连接状态">
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
                    {ch.pending
                      ? "待接入"
                      : state[ch.key]
                        ? "模拟已连接"
                        : "已模拟断开"}
                  </span>
                </button>
              ))}
            </section>
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
                          : `${sample.torque >= 0 ? "+" : ""}${sample.torque.toFixed(2)}`}
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
                          ? "输出端 · 模拟读数"
                          : "连接已断开 · 数据不可用"}
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
                      <span>{display(sample.speed, 1)}</span>
                      <small>rpm</small>
                    </div>
                    <div className="reading-foot">
                      <span>减速箱输出端</span>
                    </div>
                    <div className="secondary-line">
                      {state.sensorConnected
                        ? "来自扭矩传感器 · 模拟"
                        : "传感器已模拟断开"}
                    </div>
                  </article>
                  <article className="reading">
                    <div className="reading-label">
                      <span>制动加载电流</span>
                      <Icon name="power" size={18} />
                    </div>
                    <div className="value">
                      <span>{display(sample.current, 3)}</span>
                      <small>A</small>
                    </div>
                    <div className="reading-foot">
                      <span className={state.loadEnabled ? "running-text" : ""}>
                        {!state.supplyConnected
                          ? "制动电源已模拟断开"
                          : state.loadEnabled
                            ? "制动输出已开启"
                            : "制动输出关闭"}
                      </span>
                    </div>
                    <div className="secondary-line">
                      已设定 {state.targetCurrent.toFixed(2)} A · 模拟
                    </div>
                  </article>
                </div>
                <section className="panel chart-panel">
                  <div className="panel-heading">
                    <div>
                      <h2>实时曲线</h2>
                      <span className="panel-caption">
                        {frozen
                          ? "视图已暂停 · 采集仍在继续"
                          : "模拟数据 · 约 5 Hz 更新"}
                      </span>
                    </div>
                    <div className="chart-tools">
                      <select
                        aria-label="曲线时间范围"
                        value={windowSeconds}
                        onChange={(e) =>
                          setWindowSeconds(Number(e.target.value))
                        }
                      >
                        <option value="30">最近 30 秒</option>
                        <option value="60">最近 60 秒</option>
                      </select>
                      <button
                        className={`button small ${frozen ? "selected" : ""}`}
                        onClick={() => setFrozen(frozen ? null : [...samples])}
                      >
                        <Icon name={frozen ? "play" : "pause"} size={15} />
                        {frozen ? "恢复实时" : "暂停曲线"}
                      </button>
                    </div>
                  </div>
                  <div className="chart-subhead">
                    <div className="metric-tabs" aria-label="曲线指标">
                      {[
                        ["torque", "扭矩", "N·m"],
                        ["speed", "转速", "rpm"],
                        ["current", "制动电流", "A"],
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
                    samples={frozen || samples}
                    metric={metric}
                    range={state.range}
                    windowSeconds={windowSeconds}
                    paused={Boolean(frozen)}
                    empty={!hasRun && !frozen}
                    onDemo={runDemo}
                  />
                  <div className="chart-footer">
                    <span>
                      <span className={`legend-line ${metric}`} />
                      {metric === "torque"
                        ? "减速箱输出扭矩"
                        : metric === "speed"
                          ? "输出端转速"
                          : "制动电源读回电流"}
                    </span>
                    <span>时间 / mm:ss · 断开处保留缺口</span>
                  </div>
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
                        {activeRecord ? "正在记录模拟试验" : "记录这次试验"}
                      </h2>
                      <p>
                        {activeRecord
                          ? `${formatDuration(duration)} · ${activeRecord.count} 个数据点 · 暂存内存`
                          : lastRecord
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
                      disabled={!activeRecord && !state.sensorConnected}
                    >
                      <Icon name={activeRecord ? "stop" : "plus"} size={16} />
                      {activeRecord ? "停止记录" : "开始记录"}
                    </button>
                    <button
                      className="button"
                      onClick={() => lastRecord && download(lastRecord)}
                      disabled={!lastRecord || Boolean(activeRecord)}
                    >
                      <Icon name="download" size={16} />
                      导出 CSV
                    </button>
                  </div>
                  <div className="record-note">
                    {activeRecord
                      ? "停止记录不会停止电机或关闭制动输出；每次演示最多 1500 点。"
                      : "完成的记录保存在本浏览器；CSV 中始终标明 simulation。"}
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
                <section className="panel control-panel">
                  <div className="control-title">
                    <span className="control-icon">
                      <Icon name="motor" />
                    </span>
                    <div>
                      <h2>电机控制</h2>
                      <span>DRIVE CONTROL</span>
                    </div>
                    <span
                      className={`mode-badge ${state.motorEnabled ? "on" : ""}`}
                    >
                      {state.motorEnabled ? "模拟运行" : "未使能"}
                    </span>
                  </div>
                  <Setpoint
                    kind="speed"
                    value={speedDraft}
                    onChange={setSpeedDraft}
                    applied={state.targetSpeed}
                    connected={state.motorConnected}
                    onApply={(value) => apply("speed", value)}
                  />
                  <button
                    className={`button output-button ${state.motorEnabled ? "motor-stop" : ""}`}
                    disabled={!state.motorConnected}
                    onClick={() => toggleOutput("motorEnabled")}
                  >
                    <Icon
                      name={state.motorEnabled ? "stop" : "play"}
                      size={16}
                    />
                    {state.motorEnabled ? "停止电机" : "启动模拟电机"}
                  </button>
                  <div className="control-foot">
                    <span>演示减速比</span>
                    <strong>1 : 1</strong>
                  </div>
                </section>
                <section className="panel control-panel load-panel">
                  <div className="control-title">
                    <span className="control-icon">
                      <Icon name="power" />
                    </span>
                    <div>
                      <h2>制动加载</h2>
                      <span>LOAD CONTROL</span>
                    </div>
                    <span
                      className={`mode-badge ${state.loadEnabled ? "on" : ""}`}
                    >
                      {state.loadEnabled ? "输出开启" : "输出关闭"}
                    </span>
                  </div>
                  <Setpoint
                    kind="current"
                    value={currentDraft}
                    onChange={setCurrentDraft}
                    applied={state.targetCurrent}
                    connected={state.supplyConnected}
                    onApply={(value) => apply("current", value)}
                  />
                  <button
                    className={`button output-button ${state.loadEnabled ? "load-stop" : ""}`}
                    disabled={!state.supplyConnected}
                    onClick={() => toggleOutput("loadEnabled")}
                  >
                    <Icon name="power" size={16} />
                    {state.loadEnabled ? "关闭制动输出" : "开启模拟制动输出"}
                  </button>
                  <div className="control-foot">
                    <span>磁粉制动器</span>
                    <strong>仅作为负载</strong>
                  </div>
                </section>
                <div className="control-hint">
                  <Icon name="info" size={17} />
                  <p>制动电流用于调节负载。传感器测量减速箱输出端扭矩。</p>
                </div>
              </aside>
            </div>
          </>
        ) : (
          <section className="records-view">
            <div className="records-toolbar">
              <div>
                <h2>
                  本地记录 <span>{records.length}</span>
                </h2>
                <p>模拟记录保存在当前浏览器，导出后可离线分析。</p>
              </div>
              <button
                className="button primary"
                onClick={() => setView("bench")}
              >
                <Icon name="plus" size={17} />
                {activeRecord ? "返回正在记录的试验" : "返回工作台"}
              </button>
            </div>
            {records.length === 0 ? (
              <div className="empty-records">
                <Icon name="records" size={42} />
                <h3>从第一场试验开始</h3>
                <p>在工作台开始并停止记录，这里就会出现完整的模拟数据。</p>
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
                            onClick={() => setSelected(record)}
                          >
                            {record.name}
                          </button>
                        </td>
                        <td>
                          {record.range} N·m{" "}
                          <span className="demo-tag">模拟</span>
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
              <section className="panel history-chart">
                <div className="panel-heading">
                  <div>
                    <h2>{selected.name}</h2>
                    <span className="panel-caption">
                      完整记录 · 扭矩 N·m · 模拟数据
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
                <Plot
                  samples={selected.samples.map((s) => ({
                    ...s,
                    t: s.t - selected.samples[0].t,
                  }))}
                  metric="torque"
                  range={selected.range}
                  windowSeconds={Math.max(
                    1,
                    selected.samples.at(-1).t - selected.samples[0].t,
                  )}
                />
              </section>
            )}
          </section>
        )}
        <footer className="page-footer">
          <span>
            <i />
            INNTORQUE ENGINEERING
          </span>
          <span>界面原型 · 数据仅用于演示</span>
        </footer>
      </main>
      {settings && (
        <Settings
          onClose={() => setSettings(false)}
          state={state}
          toggleConnection={toggleConnection}
        />
      )}
      <div className={`toast ${toast ? "visible" : ""}`} role="status">
        <Icon name="check" size={17} />
        {toast}
      </div>
    </div>
  );
}
