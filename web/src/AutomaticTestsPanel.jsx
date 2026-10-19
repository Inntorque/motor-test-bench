import React, { useEffect, useState } from 'react';
import {motorTemperatureC} from './model.js';

const show = (value, digits=3) => Number.isFinite(value) ? value.toFixed(digits) : '—';
const STATES = {starting:'准备中',completed:'脚本完成',stopped:'已停止',interrupted:'脚本中断'};
const initial = {group:'loaded',loadedMode:'hold',targetIqA:'',durationS:'30',settleS:'2',position:true,speed:true,positions:'-10, 0, 10',positionReference:'relative',cycles:'2',
  speedStepRpm:'1',currentStepA:'0.1',
  speedSearchDurationS:'120',currentMaxA:'',temperatureMaxC:'',dwellS:'2',
  manualBrakeOff:false,loadConfirmed:false};

export default function AutomaticTestsPanel({token,bridgeReady}) {
  const [form,setForm] = useState(initial);
  const [data,setData] = useState({current:null,records:[]});
  const [selectedId,setSelectedId] = useState('');
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  const [online,setOnline] = useState(false);
  const patch = values => setForm(old=>({...old,...values}));
  const active = !!data.current?.running;
  const loaded = form.group==='loaded';
  const fixed = loaded && form.loadedMode==='hold';
  const motor = data.motor || {};
  const snapshot = motor.connected ? motor.snapshot : null;
  const ratio = snapshot?.motorProfile?.nameplate?.reduction_ratio;
  const position = snapshot && Date.now()/1000-snapshot.receivedAt<=1.5 && snapshot.feedback?.position_stale===false && ratio>0 ? snapshot.feedback.position_deg/ratio : null;
  const report = selectedId && selectedId!==data.current?.id ? data.records.find(row=>row.id===selectedId) : data.current;
  useEffect(()=>{
    let alive=true,inFlight=false,lastHistory=0;
    async function poll() {
      if(inFlight)return;
      inFlight=true;
      try {
        const history=Date.now()-lastHistory>=5000;
        const response=await fetch(`/api/automatic/status?history=${history?1:0}`,{cache:'no-store',signal:AbortSignal.timeout(4000)});
        const result=await response.json();
        if(!response.ok)throw Error(result.error || '自动测试服务未就绪');
        if(history)lastHistory=Date.now();
        if(alive){setData(old=>({...old,...result}));setOnline(true);}
      } catch {if(alive)setOnline(false);}
      finally{inFlight=false;}
    }
    void poll();const timer=setInterval(poll,500);
    return ()=>{alive=false;clearInterval(timer);};
  },[]);
  const fields = [
    ...(!fixed ? [['dwellS','每级保持时间 s']] : []),
    ...(!loaded && form.position ? [['cycles','定位往返次数']] : []),
    ...(!loaded && form.speed ? [['speedStepRpm','输出转速步长 rpm'],['speedSearchDurationS','升速搜索最长时间 s']] : []),
    ...(loaded && !fixed ? [['currentStepA','目标 Iq 步长 A']] : []),
    ...(fixed ? [['targetIqA','指定目标 Iq A'],['settleS','开始记录前运行等待 s'],['durationS','全参数记录时长 s']] : []),
  ];
  const filled=fields.every(([key])=>form[key].trim()!=='' && Number.isFinite(Number(form[key]))) &&
    (loaded || !form.position || form.positions.trim()!=='');
  async function action(name) {
    setBusy(true);setError('');
    try {
      const payload=name==='stop' ? {id:data.current.id} : {...form,
        ...Object.fromEntries(fields.map(([key])=>[key,form[key].trim()===''?null:Number(form[key])])),
        currentMaxA:form.currentMaxA.trim()===''?null:Number(form.currentMaxA),
        dwellS:Number(form.dwellS),
        temperatureMaxC:form.temperatureMaxC.trim()===''?null:Number(form.temperatureMaxC),
        positionsDeg:form.positions.split(/[,，\s]+/).filter(Boolean).map(Number)};
      const response=await fetch(`/api/automatic/${name}`,{method:'POST',
        headers:{'Content-Type':'application/json','X-MotorBench-Token':token.current},
        body:JSON.stringify(payload),signal:AbortSignal.timeout(20000)});
      const result=await response.json();
      if(!response.ok)throw Error(result.error || '自动测试操作失败');
      setSelectedId('');setData(old=>({...old,current:result.current}));
    }catch(failure){setError(failure.message);}
    finally{setBusy(false);}
  }
  const download = name => `/api/automatic/files/${report.id}/${name}`;
  return <section className="automatic-tests" aria-label="空载和带载自动测试">
    <div className="special-layout">
      <section className="panel special-config"><h3>自动测试脚本</h3>
        <p className="hw-readback">当前输出角度 {show(position)} ° · NTC {show(motorTemperatureC(motor),1)} °C · 设备 Iq 上限 {show(snapshot?.limitA)} A · NTC 保护 {show(snapshot?.ntc?.limit_c,1)} °C</p>
        <p className="setpoint-hint">{loaded?'先在工作台连接四个接口，设置并开启双电源，电机驱动保持停止，再填写本次电流与记录时长启动。':'台架采用连续旋转模式，无软件机械角度限位。定位往返使用上述上电多圈坐标，启动前不自动清零。'}未显示读数时请先连接电机。</p>
        <label>测试工况<select aria-label="自动测试工况" disabled={active || busy} value={form.group} onChange={e=>patch({group:e.target.value})}>
          <option value="loaded">带载测试</option><option value="no_load">空载测试</option>
        </select></label>
        {loaded && <label>带载方式<select aria-label="带载测试方式" disabled={active || busy} value={form.loadedMode} onChange={e=>patch({loadedMode:e.target.value})}>
          <option value="hold">指定 Iq 恒流运行 · 全参数记录</option><option value="ramp">Iq 递增 · 最大转矩</option>
        </select></label>}
        {!loaded && <div className="auto-stages">
          <label className="special-confirm"><input type="checkbox" checked={form.position} disabled={active || busy} onChange={e=>patch({position:e.target.checked})}/>编码器闭环定位往返（MODE 3）</label>
          <label className="special-confirm"><input type="checkbox" checked={form.speed} disabled={active || busy} onChange={e=>patch({speed:e.target.checked})}/>空载最高转速自动搜索（MODE 2）</label>
        </div>}
        <div className="hw-form-grid">
          {fields.map(([key,label])=><label key={key}>{label}<input aria-label={label} type="number" step={key==='cycles'?'1':'any'} value={form[key]} disabled={active || busy} placeholder="按本次台架填写" onChange={e=>patch({[key]:e.target.value})}/></label>)}
        </div>
        {!loaded && form.position && <>
          <label>位置坐标方式<select aria-label="位置坐标方式" value={form.positionReference} disabled={active || busy} onChange={e=>patch({positionReference:e.target.value})}><option value="relative">相对启动位置的位移</option><option value="absolute">现有上电多圈绝对坐标</option></select></label>
          <label>{form.positionReference==='relative'?'测试位移点':'输出位置目标点'} °（逗号分隔，2～12 点）<input aria-label="输出位置目标点" value={form.positions} disabled={active || busy} onChange={e=>patch({positions:e.target.value})}/></label>
          <p className="setpoint-hint">默认在启动位置附近往返 −10°、0°、+10°。每点按保持时间记录实际位置与误差，误差大小不触发验收停机。</p>
        </>}
        <details><summary>停机保护设置（默认读取设备，可选更低停止值）</summary><div className="hw-form-grid">
          {[['currentMaxA',loaded?'本次 Iq 停止值 A':'实际 Iq 停止值 A'],['temperatureMaxC','NTC 停止温度 °C']].map(([key,label])=><label key={key}>{label}<input type="number" step="any" value={form[key]} placeholder="留空：启动时读取设备" disabled={active || busy} onChange={e=>patch({[key]:e.target.value})}/></label>)}
        </div><p className="setpoint-hint">保护用于停止输出，不作测试结果的合格标准。默认使用设备电流限值与 NTC 保护温度；不会改写设备参数。</p></details>
        {loaded ? <label className="special-confirm"><input type="checkbox" disabled={active || busy} checked={form.loadConfirmed} onChange={e=>patch({loadConfirmed:e.target.checked})}/>已接好测功台，设置并开启电机电源和制动电源，确认允许本次{fixed?'指定电流带载运行':'带载电流递增'}</label>
          : <label className="special-confirm"><input type="checkbox" disabled={active || busy} checked={form.manualBrakeOff} onChange={e=>patch({manualBrakeOff:e.target.checked})}/>制动状态无法读取时，我已在现场确认制动励磁关闭或断开</label>}
        <div className="auto-script-preview"><strong>执行顺序</strong><ol>
          <li>核对真实反馈、编码器、电流及转速限值、NTC 与制动状态</li>
          {!loaded && form.position && <li>按目标点往返定位 → 记录末段误差及同向重复位置离散值 → 停机</li>}
          {!loaded && form.speed && <li>自动逐级升速 → 记录实际均值、偏差与波动 → 重复观测转速平台及停止原因</li>}
          {fixed && <li>四接口就绪 → MODE 5 启动并设置指定 Iq → 等待设定秒数 → 自动开始全参数记录 → 到时停机</li>}
          {loaded && !fixed && <li>Iq 从 0 A 逐级提高 → 逐帧记录转矩、转速和功率 → 保护或设定上限后停机</li>}
          <li>确认输出关闭，保存报告{loaded?'及停机后 1 秒扭矩记录':''}</li>
        </ol></div>
        {!loaded && form.speed && <p className="setpoint-hint">最高转速、速度偏差和波动由实际数据得出，不填写允许误差或稳态容差。平台识别根据实测波动自动比较重复采样，不作合格判定；控制器限幅、超时或保护分别记录。</p>}
        <p className="setpoint-hint">沿用参考工程现有控制参数与保护。电流阈值为后台监测值；定位/带载阶段累计保持最多 240 s，命令开销另限 10 s。升速搜索最长 240 s；设备速度边界自动读取，不会改写保护参数。</p>
        {loaded && <p className="setpoint-hint">本次使用预设制动负载，脚本不调制动电压/电流。{fixed?'指定 Iq 为电机电流环目标，实际 Iq 单独记录；恒流模式不保证恒定转速。记录时长 1～240 s，运行等待 0～10 s。':'报告记录本次工况测得的最大转矩。'}</p>}
        {fixed && <p className="setpoint-hint">全参数时间序列直接写入磁盘，不受网页刷新率或 1500 点记录限制：扭矩每个有效帧、电机全部已接入反馈、双电源设定与读回、限幅、输出和保护状态、命令及应答均保存。CSV 按来源分行，其他接口列留空，各来源保留独立时间戳；JSONL 保留完整字段。结束后自动生成统计与报告，停机后续采 1 s。</p>}
        {(!bridgeReady || !online) && <p className="hw-inline-error">自动测试通信桥未就绪</p>}
        {error && <p className="hw-inline-error" role="alert">{error}</p>}
        <div className="hw-output-actions"><button className="button primary" disabled={busy || active || !bridgeReady || !online || !filled || (loaded && !form.loadConfirmed) || (!loaded && !form.position && !form.speed)} onClick={()=>action('start')}>{fixed?'启动恒流带载并自动记录':'启动顺序测试'}</button>
          <button className="button danger" disabled={busy || !active} onClick={()=>action('stop')}>停止脚本并停机</button></div>
        {active && <p role="status">{data.current.phase} · {show(data.current.elapsedS,1)} s · 已完成 {data.current.steps?.length || 0} {data.current.config.loadedMode==='hold'?'次运行':'级'}</p>}
      </section>
      <section className="panel special-result"><h3>自动测试结果</h3>
        {!report ? <p>测试步骤已提供默认值，可调整后启动。实测数值如实记录，不预设合格误差；设备保护在启动时读取。</p> : <>
          <h4>{report.config.loadedMode==='hold'?'指定 Iq 恒流带载 · 全参数记录':report.config.group==='loaded'?'带载最大转矩':'空载定位 / 速度'}</h4>
          <p>{report.running?report.phase:STATES[report.state]} · {show(report.elapsedS,1)} s</p>
          <p>{report.reason || '正在按脚本运行'}</p>
          {report.config.loadedMode==='hold' && <section className="auto-script-preview" aria-label="全参数时间序列记录状态">
            <p>指定 Iq {show(report.config.targetIqA)} A · 运行等待 {show(report.config.settleS,1)} s · 记录时长 {show(report.config.durationS,1)} s</p>
            <p>记录起点：{report.timeline?.recordingStartedAt?new Date(report.timeline.recordingStartedAt*1000).toLocaleString():'等待电机按指定电流运行'}</p>
            <p>已保存扭矩帧 {report.timeline?.counts?.sensor_frame || 0} · 电机快照 {report.timeline?.counts?.motor || 0} · 电机电源 {report.timeline?.counts?.motorSupply || 0} · 制动电源 {report.timeline?.counts?.brake || 0}</p>
            {report.timeline?.error && <p className="hw-inline-error">记录异常：{report.timeline.error}</p>}
          </section>}
          <div className="auto-summary">
            {report.speedSearch && <div><span>最高实测转速（末段均值）</span><strong>{show(report.speedSearch.maxMeanRpm ?? report.speedSearch.maxStableRpm,2)} rpm</strong></div>}
            <div><span>实测输出转速峰值</span><strong>{show(report.peakOutputSpeedRpm,2)} rpm</strong></div>
            <div><span>实测 Iq 峰值</span><strong>{show(report.peakActualIqA)} A</strong></div>
            <div><span>NTC 最高温</span><strong>{show(report.peakNtcC,1)} °C</strong></div>
            <div><span>NTC 相对初温变化</span><strong>{show(report.temperatureChangeC,1)} °C</strong></div>
            <div><span>设备保护反馈</span><strong>{report.protectionSeen?'已触发':'未观察到'}</strong></div>
            {report.config.group==='loaded' && <div><span>本次实测最大转矩（幅值）</span><strong>{show(report.measuredPeakTorqueNm)} N·m</strong></div>}
          </div>
          {report.speedSearch && <section className="auto-script-preview" aria-label="最高转速搜索结果"><strong>最高转速搜索</strong>
            <p>{report.speedSearch.limitFound?'重复采样观察到当前工况的转速平台':report.speedSearch.state==='running'?'正在自动升速搜索':report.speedSearch.state==='pending'?'等待前序阶段完成':'尚未确认电机自身转速上限'}</p>
            <p>最高均值对应目标：{show(report.speedSearch.maxMeanTargetRpm)} rpm · 平台观测目标：{show(report.speedSearch.platformTargetRpm ?? report.speedSearch.firstUnreachedTargetRpm)} rpm</p>
            <p>控制器可用范围：0～{show(report.speedSearch.controllerCeilingRpm)} rpm（自动读取，控制器限幅不作为电机极限结论）。瞬时峰值、末段均值和波动分别记录。</p>
          </section>}
          {report.positionResults?.length>0 && <div className="records-table-wrap"><table><thead><tr><th>目标 °</th><th>方向</th><th>次数</th><th>均值误差 °</th><th>重复位置极差 °</th></tr></thead><tbody>
            {report.positionResults.map((row,index)=><tr key={index}><td>{show(row.targetDeg)}</td><td>{row.direction==='forward'?'正序':'逆序'}</td><td>{row.repeats}</td><td>{show(row.statistics?.mean-row.targetDeg)}</td><td>{row.repeats>1?show(row.statistics?.peakToPeak):'次数不足'}</td></tr>)}
          </tbody></table></div>}
          {report.timeline?.statistics && <div className="records-table-wrap"><table><thead><tr><th>记录窗口指标</th><th>有效点</th><th>均值</th><th>最小值</th><th>最大值</th><th>峰峰值</th></tr></thead><tbody>
            {[['torqueNm','扭矩 N·m'],['speedRpm','传感器转速 rpm'],['shaftPowerW','轴功率 W'],['actualIqA','实际 Iq A'],['temperatureC','NTC °C']].map(([key,label])=>{const stat=report.timeline.statistics[key] || {};return <tr key={key}><td>{label}</td><td>{stat.count || 0}</td><td>{show(stat.mean)}</td><td>{show(stat.min)}</td><td>{show(stat.max)}</td><td>{show(stat.peakToPeak)}</td></tr>;})}
          </tbody></table></div>}
          <h4>{report.config.loadedMode==='hold'?'本次运行':'逐级实测记录'}</h4><p className="setpoint-hint">定位统计取每级最后 0.4 s；速度统计取最后半段。恒流统计覆盖实际记录窗口，停机尾段单独保留。无通过/不通过判定。</p>
          <div className="records-table-wrap"><table><thead><tr><th>目标</th><th>实际 / 峰值</th><th>均值误差</th><th>误差 RMS</th><th>末段峰峰值</th><th>末次定位误差</th></tr></thead><tbody>
            {(report.steps || []).map((row,index)=><tr key={index}><td>{Number.isFinite(row.targetDeg)?`${show(row.targetDeg)} ° · 第 ${row.cycle} 次${row.direction==='forward'?'正序':'逆序'}`:Number.isFinite(row.targetRpm)?`${show(row.targetRpm)} rpm${row.attempt===2?' · 重复采样':''}`:`${show(row.targetIqA)} A`}</td><td>{Number.isFinite(row.meanOutputDeg)?`${show(row.meanOutputDeg)} °`:Number.isFinite(row.meanOutputRpm)?`${show(row.meanOutputRpm)} rpm`:`累计 ${show(row.peakObservedTorqueNm)} N·m`}</td><td>{show(row.errorDeg ?? row.errorRpm)}</td><td>{show(row.errorRmsDeg ?? row.errorRmsRpm)}</td><td>{show(row.positionPeakToPeakDeg ?? row.peakToPeakRpm)}</td><td>{show(row.endErrorDeg)}</td></tr>)}
          </tbody></table></div>
          <p>本次停机保护：Iq {show(report.config.currentMaxA)} A · NTC {show(report.config.temperatureMaxC,1)} °C（独立于实测误差统计）。</p>
          <p>输出关闭：{report.stopConfirmed===true?'已由设备确认':report.stopConfirmed===false?'未确认，请立即检查现场输出':'待确认'}</p>
          {report.config.group==='no_load' && <p>角度按现有编码器和减速比 {show(report.ratio,2)} 换算，为上电相对坐标；定位结果表示现有反馈的跟随误差与重复离散值。未使用独立角度基准。</p>}
          {report.rawTorqueAnalysisError && <p className="hw-inline-error">{report.rawTorqueAnalysisError}</p>}
          {!report.running && <div className="hw-output-actions">{report.timeline && <><a className="button primary" href={download('timeline.csv')} download>导出全参数时间序列 CSV</a><a className="button" href={download('timeline.jsonl')} download>导出完整字段 JSONL</a></>}<a className="button" href={download('data.csv')} download>导出电机 CSV</a><a className="button" href={download('report.json')} download>导出脚本报告</a>
            {report.rawTorqueRecordId && <a className="button" href={`/api/specialized/files/${report.rawTorqueRecordId}/data.csv`} download>导出逐帧扭矩 CSV</a>}</div>}
        </>}
      </section>
    </div>
    <section className="panel special-history"><h3>自动测试历史</h3><div className="records-table-wrap"><table><thead><tr><th>时间</th><th>工况</th><th>结果</th><th/></tr></thead><tbody>
      {data.records.map(row=><tr key={row.id}><td>{new Date(row.startedAt*1000).toLocaleString()}</td><td>{row.config.group==='loaded'?'带载':'空载'}</td><td>{row.running?row.phase:STATES[row.state]}</td><td><button className="button small" onClick={()=>setSelectedId(row.id)}>查看脚本结果</button></td></tr>)}
    </tbody></table></div>{!data.records.length && <p>暂无自动测试记录。</p>}</section>
  </section>;
}
