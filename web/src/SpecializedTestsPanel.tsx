import type {Capture} from '../../browser/capture.ts';
import type {Specialized} from '../../browser/specialized.ts';
import type {CoupledCurrent} from '../../browser/control.ts';
import React,{useEffect,useRef,useState} from 'react';
import {apiFetch} from './apiClient.ts';
import {subjectText,setupText} from './testSubjects.ts';
import {referencesCsv,saveFile,referenceAnalysis,loadAnalysis,saveAnalysis} from './specialized/data.ts';
import './specialized/specialized.css';

const SHOW=(value,digits=3)=>Number.isFinite(value)?value.toFixed(digits):'—';
const CATEGORIES={performance:'性能测试',position:'定位精度',thermal:'温升曲线'};
const DEFAULTS={name:'',ratedSpeedRpm:'',ratedTorqueNm:'',
  durationS:'600',windowS:'1',settleS:'1',dwellS:'2',coolingS:'60',ambientC:'',thermalWindowS:'600',thermalSlopeCPerMin:'0.1',stopWhenStable:false,
  operation:'observe',test:'capture',controlMode:'IQ',target:'1',iqLevels:'0.5,1,1.5,2',ktMaxSpeedRpm:'5',ktMaxIdA:'0.5',
  positions:'0,45,90,135,180,225,270,315',cycles:'5',approachDeg:'2',positionReference:'relative',motionConfirmed:false,
  startNm:'0.1',stepNm:'0.1',maxNm:'30',intervalS:'5',bandwidthVerified:false,sensorInfo:'',coolingInfo:''};

function Curve({title,points,x,y,xLabel,yLabel,series}: {title: string; points?: Capture['preview']; x?: string; y?: string; xLabel: string; yLabel: string; series?: {points: Capture['preview']; x: string; y: string; color: string}[]}) {
  const lines=series||[{points,x,y,color:'#2265bd'}];
  const valid=lines.map(line=>({...line,values:(line.points||[]).filter(p=>Number.isFinite(p[line.x])&&Number.isFinite(p[line.y]))}));
  const all=valid.flatMap(line=>line.values.map(p=>[p[line.x],p[line.y]]));
  if(!all.length)return <section className="sx-chart"><h3>{title}</h3><p>尚无有效数据，缺失量不会填零。</p></section>;
  let minX=Math.min(...all.map(p=>p[0])),maxX=Math.max(...all.map(p=>p[0]));
  let minY=Math.min(0,...all.map(p=>p[1])),maxY=Math.max(...all.map(p=>p[1]));
  if(maxX===minX)maxX=minX+1;if(maxY===minY)maxY=minY+1;
  const pad=(maxY-minY)*.08;minY-=pad;maxY+=pad;
  return <section className="sx-chart"><h3>{title}</h3><svg viewBox="0 0 760 270" role="img" aria-label={title}>
    {[0,.25,.5,.75,1].map(t=><g key={t}><line x1="75" x2="720" y1={25+t*190} y2={25+t*190} stroke="#dce4ee"/><text x="65" y={29+t*190} textAnchor="end">{SHOW(maxY-t*(maxY-minY),2)}</text></g>)}
    {valid.map((line,i)=><g key={i}><polyline points={line.values.map(p=>`${75+(p[line.x]-minX)/(maxX-minX)*645},${215-(p[line.y]-minY)/(maxY-minY)*190}`).join(' ')} fill="none" stroke={line.color} strokeWidth="2"/>
      {line.values.length<=150&&line.values.map((p,j)=><circle key={j} cx={75+(p[line.x]-minX)/(maxX-minX)*645} cy={215-(p[line.y]-minY)/(maxY-minY)*190} r="2.5" fill={line.color}/>)}</g>)}
    <text x="75" y="238">{SHOW(minX,1)}</text><text x="720" y="238" textAnchor="end">{SHOW(maxX,1)}</text><text x="400" y="263" textAnchor="middle">{xLabel}</text><text x="8" y="16">{yLabel}</text>
  </svg></section>;
}

export default function SpecializedTestsPanel({token,bridgeReady}) {
  const [category,setCategory]=useState('performance'),[form,setForm]=useState(DEFAULTS);
  const [state,setState]=useState<{automatic: Partial<ReturnType<Specialized['status']>>; specialized: Partial<ReturnType<Specialized['status']>>; coupled: Partial<ReturnType<CoupledCurrent['status']>>}>({automatic:{records:[]},specialized:{records:[]},coupled:{run:{}}});
  const [catalog,setCatalog]=useState(null);
  const [chosen,setChosen]=useState(null),[detail,setDetail]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [instrument,setInstrument]=useState({name:'',zeroDeg:'0',aligned:false});
  const inFlight=useRef(false);const patch=value=>setForm(old=>({...old,...value}));
  async function request(path,body = undefined) {
    const response=await apiFetch(path,body?{method:'POST',headers:{'Content-Type':'application/json','X-MotorBench-Token':token.current},body:JSON.stringify(body)}:{cache:'no-store',forceNetwork:true,signal:AbortSignal.timeout(5000)});
    const data=await response.json();if(!response.ok)throw Error(data.error||'专项服务请求失败');return data;
  }
  async function refresh() {
    if(inFlight.current)return;inFlight.current=true;
    try {
      const [automatic,specialized,coupled,subjects]=await Promise.all(['/api/automatic/status','/api/specialized/status','/api/coupled-current/status','/api/test-subjects'].map(path=>request(path)));
      setCatalog(subjects);
      setState({automatic,specialized,coupled});
    } catch(failure){setError(failure.message);}finally{inFlight.current=false;}
  }
  useEffect(()=>{void refresh();const timer=setInterval(refresh,1000);return()=>clearInterval(timer);},[]);
  const reports=[...(state.automatic.records||[]).map(report=>({...report,origin:'automatic'})),...(state.specialized.records||[]).map(report=>({...report,origin:'specialized'}))].sort((a,b)=>b.startedAt-a.startedAt);
  const live=[{...state.automatic.current,origin:'automatic'},{...state.specialized.current,origin:'specialized'}].find(report=>report.running&&report.specializedSchema===2);
  const report=detail||(live&&(!chosen||chosen===live.id)?live:null)||reports.find(row=>row.id===chosen)||reports.find(row=>row.category===category&&row.specializedSchema===2);
  const anyActive=!!state.automatic.current?.running||['running','settling'].includes(state.specialized.current?.state)||!!state.coupled.run?.running;
  const setup=(catalog?.testSetups||[]).find(item=>item.id===catalog?.selected?.testSetupId&&item.motorId===catalog?.selected?.motorId);
  useEffect(()=>patch({motionConfirmed:false}),[setup?.id,setup?.revision]);
  const count=(report?.positionVisitCount??report?.positionVisits?.length)||0;
  function recipe() {
    if(!setup)throw Error('请先在右上角选择电机及其测试配置');
    const result={...form,suite:category,testSetupId:setup.id,testSetupRevision:setup.revision??0,iqLevelsA:form.iqLevels.split(/[,，\s]+/).filter(Boolean).map(Number),positionsDeg:form.positions.split(/[,，\s]+/).filter(Boolean).map(Number)};
    for(const key of ['positions','iqLevels','startNm','stepNm','maxNm','intervalS'])delete result[key];
    for(const key of ['durationS','windowS','settleS','dwellS','coolingS','thermalWindowS','thermalSlopeCPerMin','target','cycles','approachDeg','ktMaxSpeedRpm','ktMaxIdA']) {
      if(form[key].trim()===''||!Number.isFinite(Number(form[key])))throw Error(`${key} 必须填写有效数值`);result[key]=Number(form[key]);
    }
    for(const key of ['ratedSpeedRpm','ratedTorqueNm','ambientC']) {
      if(form[key]!==''&&!Number.isFinite(Number(form[key])))throw Error(`${key} 必须为有效数值或留空`);
      result[key]=form[key]===''?null:Number(form[key]);
    }
    return result;
  }
  async function act(action) {
    setBusy(true);setError('');
    let captureId;
    try {
      if(action==='start') {
        const body=recipe();let response;
        if(category==='performance'&&(form.test==='capture'||form.test==='envelope')) {
          response=await request('/api/specialized/start',{...body,test:'capture'});captureId=response.current.id;
          if(form.test==='envelope') {
            if(['startNm','stepNm','maxNm','intervalS'].some(key=>form[key].trim()===''||!Number.isFinite(Number(form[key]))))throw Error('加载参数必须填写有效数值');
            await request('/api/coupled-current/start',{mode:'torque_sweep',motorMode:'maximum_output',busLimitA:20,busVoltageV:24,
            brake:{startNm:Number(form.startNm),stepNm:Number(form.stepNm),maxNm:Number(form.maxNm),intervalS:Number(form.intervalS)}});
          }
        }else if(category==='thermal'&&form.operation==='observe')response=await request('/api/specialized/start',body);
        else response=await request('/api/automatic/start',body);
        setChosen(response.current.id);setDetail(null);
      }else if(action==='load')await request('/api/coupled-current/load',{id:state.coupled.run.id});
      else if(action==='stopDrive')await request('/api/automatic/stop',{id:state.automatic.current?.id});
      else if(action==='stopEnvelope')await request('/api/coupled-current/stop',{});
      else await request('/api/specialized/stop',{id:state.specialized.current?.id});
      await refresh();
    } catch(failure) {
      setError(failure.message);
      if(captureId)try{await request('/api/specialized/stop',{id:captureId});}catch{}
    }finally{setBusy(false);}
  }
  async function showReport(row) {
    setBusy(true);setError('');
    try {const data=await request(`/api/${row.origin}/files/${row.id}/report.json`);setDetail(loadAnalysis({...data,origin:row.origin}));setChosen(row.id);}
    catch(failure){setError(failure.message);}finally{setBusy(false);}
  }
  async function importReferences(file) {
    if(!file||!report)return;setBusy(true);setError('');
    try {
      const rows=referencesCsv(await file.text());if(!rows.length)throw Error('CSV 没有参考数据');
      if(!instrument.name.trim()||!instrument.aligned||instrument.zeroDeg.trim()===''||!Number.isFinite(Number(instrument.zeroDeg)))throw Error('请填写仪器/参考零位并确认坐标已对齐');
      const analysis=referenceAnalysis(report,rows,{name:instrument.name.trim(),zeroDeg:Number(instrument.zeroDeg),aligned:true});
      setDetail({...report,...analysis});
      try {saveAnalysis(report,analysis);}catch{setError('分析已生成，但浏览器保存失败，请立即导出专项报告 JSON。');}
    }catch(failure){setError(failure.message);}finally{setBusy(false);}
  }
  const field=(key,label,options: {text?: boolean}={})=><label key={key}>{label}<input aria-label={label} value={form[key]} type={options.text?'text':'number'} step="any" disabled={anyActive||busy} onChange={event=>patch({[key]:event.target.value})}/></label>;
  const select=(key,label,choices)=><label>{label}<select aria-label={label} value={form[key]} disabled={anyActive||busy} onChange={event=>patch({[key]:event.target.value})}>{choices.map(([value,text])=><option key={value} value={value}>{text}</option>)}</select></label>;
  const metrics=report?.performance||{},position=report?.positionMetrics||{},temperature=report?.thermal||{};
  return <section className="specialized-tests sx-suite" aria-label="专项测试">
    <header className="sx-heading"><div><h2>专项测试</h2><p>性能、定位精度与温升测试，保存实测曲线和报告。</p></div><span>V2 · {anyActive?'任务进行中':'待测试'}</span></header>
    <nav className="sx-tabs" aria-label="专项测试分类">{Object.entries(CATEGORIES).map(([key,label])=><button className={category===key?'active':''} key={key} onClick={()=>{setCategory(key);setChosen(null);setDetail(null);patch({test:key==='performance'?'capture':key});}}>{label}</button>)}</nav>
    <div className="sx-columns"><section className="sx-card"><h3>{CATEGORIES[category]}配方</h3>
      <p className="sx-muted">先完成设备连接。电机、驱动板和测试配置在本次开始时冻结。</p>
      <p className="sx-note" aria-label="当前测试配置">{setupText(setup)}{setup?` · 质量 ${setup.massKg??'未填'} kg · 体积 ${setup.volumeL??'未填'} L`:'，请在右上角选择电机及其测试配置'}</p>
      <div className="sx-fields">{field('name','记录名称',{text:true})}
        {category==='performance'&&select('test','性能配方',[['capture','T-N：记录现有工况'],['envelope','T-N：最大输出＋阶梯加载'],['kt','转矩常数：低速电流分级'],['point','指定工况测量']])}
        {category==='thermal'&&select('operation','温升运行方式',[['observe','只记录现有工况'],['active','专项保持指定工况']])}
        {(category==='thermal'&&form.operation==='active'||category==='performance'&&form.test==='point')&&<>{select('controlMode','工况控制方式',[['IQ','指定 Iq'],['VELOCITY','指定输出转速']])}{field('target',form.controlMode==='IQ'?'目标 Iq A':'目标输出转速 rpm')}</>}
        {category!=='position'&&field('durationS','采集/运行时长 s')}{field('windowS','统计窗口 s')}
        {category!=='position'&&<>{field('ratedSpeedRpm','铭牌额定转速 rpm（可选）')}{field('ratedTorqueNm','铭牌额定转矩 N·m（可选）')}</>}
        {category==='performance'&&<>
          {form.test==='kt'&&<>{field('iqLevels','Iq 电流级 A（逗号分隔）',{text:true})}{field('ktMaxSpeedRpm','Kt 拟合转速范围 ±rpm')}{field('ktMaxIdA','Kt 拟合 Id 均值/波动范围 A')}{field('settleS','每级稳定等待 s')}{field('dwellS','每级有效采样 s')}</>}
          {form.test==='envelope'&&<>{field('startNm','起始负载 N·m')}{field('stepNm','负载步长 N·m')}{field('maxNm','结束负载 N·m')}{field('intervalS','每级负载保持 s')}</>}
          {field('sensorInfo','转矩传感器/标定与带宽说明',{text:true})}</>}
        {category==='position'&&<>{field('positions','目标位置 °（逗号分隔）',{text:true})}{select('positionReference','位置坐标',[['relative','相对测试起点'],['absolute','现有多圈坐标']])}{field('cycles','正反向重复轮数')}{field('approachDeg','换向接近距离 °')}{field('settleS','到位后等待 s')}{field('dwellS','到位采样窗口 s')}</>}
        {category==='thermal'&&<>{field('ambientC','环境温度 °C（未知留空）')}{field('thermalWindowS','热稳定观察窗口 s')}{field('thermalSlopeCPerMin','热稳定斜率范围 ±°C/min')}{field('coolingInfo','测点、环境和散热条件',{text:true})}</>}
        {category!=='position'&&field('coolingS','停机后继续采集 s')}
      </div>
      {category==='performance'&&<><p className="sx-muted">T-N 保留全部轨迹；稳定窗口单列。额定值与连续能力分开。输出端密度采用电机＋减速箱总成质量/体积；电机轴直测采用电机本体。窗口峰值按本配方统计窗口计算，Kt 使用实际 Iq。</p><label className="sx-check"><input type="checkbox" checked={form.bandwidthVerified} onChange={event=>patch({bandwidthVerified:event.target.checked})}/>已核对传感器动态带宽；原始峰值与窗口峰值仍分别记录</label></>}
      {category==='position'&&<p className="sx-note">驱动反馈只评估跟随误差。输出端绝对定位及重复性需独立参考角度；正反向接近路径包含两端各 {form.approachDeg}°。</p>}
      {category==='thermal'&&<><p className="sx-note">现有 NTC 测点不自动等同绕组温度。无环境温度时只显示温度及相对初温变化。</p><label className="sx-check"><input type="checkbox" checked={form.stopWhenStable} onChange={event=>patch({stopWhenStable:event.target.checked})}/>满足观察窗口和工况稳定条件后结束（否则按配方时长）</label></>}
      <label className="sx-check"><input type="checkbox" checked={form.motionConfirmed} disabled={anyActive} onChange={event=>patch({motionConfirmed:event.target.checked})}/>主动测试：已确认电源、负载、散热及本次目标和换向路径允许运动</label>
      <div className="sx-actions"><button className="button primary" disabled={!bridgeReady||busy||anyActive||!setup||((category==='position'||category==='thermal'&&form.operation==='active'||category==='performance'&&form.test!=='capture')&&!form.motionConfirmed)} onClick={()=>act('start')}>{category==='performance'&&form.test==='envelope'?'① 最大输出空载启动':'开始专项测试'}</button>
        {state.coupled.run?.running&&<><button className="button" disabled={busy||state.coupled.run.phase!=='awaiting_load'} onClick={()=>act('load')}>② 开始阶梯加载</button><button className="button danger" disabled={busy} onClick={()=>act('stopEnvelope')}>停止加载并停机</button></>}
        {state.automatic.current?.running&&<button className="button danger" disabled={busy} onClick={()=>act('stopDrive')}>停止专项并停机</button>}
        {['running','settling'].includes(state.specialized.current?.state)&&<button className="button" disabled={busy} onClick={()=>act('stopCapture')}>仅结束专项采集</button>}
      </div><p className="sx-muted">观察配方只记录；主动配方会控制电机，并沿用现有控制权、设备保护和停止确认。不会写入调参或校准。</p>
      {error&&<p className="sx-error" role="alert">{error}</p>}
    </section><section className="sx-card" aria-label="专项结果"><h3>{report?.name||'测试结果'}</h3>{report?<>
      <p>{subjectText(report.testSubject)}</p><p role="status">{report.running?report.phase:report.state} · {SHOW(report.elapsedS,1)} s · {report.reason||'采集中'}</p>
      {report.specializedSchema!==2?<p className="sx-note">旧版记录保留原报告，可导出查看；不会套用新版指标。</p>:<>
        {report.category==='performance'&&<><div className="sx-metrics">{[['最高稳定窗口转速 rpm',metrics.highestObservedRpm],['瞬时转速采样峰值 rpm',Math.abs(metrics.rawSpeedPeak?.speedRpm)],['原始转矩采样峰值 N·m',Math.abs(metrics.rawTorquePeak?.torqueNm)],['有效窗口最大转矩 N·m',Math.abs(metrics.windowTorquePeak?.torque)],['同工况最大机械功率 W',Math.abs(metrics.maxObservedPowerPoint?.powerW)],[report.config?.axis==='motor'?'电机轴实测 Kt N·m/A':'输出端等效 Kt N·m/A',metrics.outputEquivalentKt?.slope],['铭牌额定转速 rpm',metrics.ratedSpeedRpm],['短时转矩密度 N·m/kg',metrics.densities?.peakTorquePerKg],['短时功率密度 W/kg',metrics.densities?.shortTimePowerWPerKg],['短时转矩密度 N·m/L',metrics.densities?.peakTorquePerL],['短时功率密度 W/L',metrics.densities?.shortTimePowerWPerL]].map(([label,value])=><div key={label}><span>{label}</span><strong>{SHOW(value)}</strong></div>)}</div>
          <p className="sx-note">短时扫描不能认定连续额定能力。{!metrics.outputEquivalentKt?'Kt 尚无足够的低速、Id 合适且独立的稳定电流级。':`Kt 截距 ${SHOW(metrics.outputEquivalentKt.intercept)} N·m，R² ${SHOW(metrics.outputEquivalentKt.rSquared)}。`}峰值和功率卡片为幅值，原始符号保留；瞬时采样峰值受测量带宽影响。</p>
          <p className="sx-muted">稳定窗口为蓝色，过渡窗口为橙色；仅用于数据分组，不增加停机条件。当前有效窗口 {metrics.validWindows??'—'}，其中稳定窗口 {metrics.steadyWindows??'—'}。</p>
          <Curve title="T-N 实测窗口" series={[{points:(report.preview||[]).filter(row=>row.valid&&row.steady),x:'n',y:'torque',color:'#2265bd'},{points:(report.preview||[]).filter(row=>row.valid&&!row.steady),x:'n',y:'torque',color:'#df7138'}]} xLabel="实测转速 rpm" yLabel="转矩 N·m"/>
          <Curve title="转矩—实际 Iq" points={(report.preview||[]).filter(row=>row.valid)} x="iq" y="torque" xLabel="实际 Iq A（相峰值等效）" yLabel="转矩 N·m"/></>}
        {report.category==='position'&&<><p>已保存 {count} 次正反向到位窗口。</p><div className="sx-metrics">{[['反馈最大均值误差 °',position.feedbackMaxAbsMeanErrorDeg],['反馈最差重复极差 °',position.feedbackWorstRepeatRangeDeg],['外部参考最大均值误差 °',position.externalMaxAbsMeanErrorDeg],['外部参考最差重复极差 °',position.externalWorstRepeatRangeDeg],['外部参考换向差 °',position.externalReversalDifferenceDeg]].map(([label,value])=><div key={label}><span>{label}</span><strong>{SHOW(value,6)}</strong></div>)}</div>
          {!position.externalCoverageComplete&&<p className="sx-note">外部参考尚未完整覆盖：当前只显示驱动反馈或部分参考统计，不能作为完整绝对精度验收。</p>}
          <Curve title="反馈定位偏差：正向蓝 / 反向橙" series={['forward','reverse'].map((direction,i)=>({points:(position.feedback||[]).filter(p=>p.direction===direction),x:'targetDeg',y:'mean',color:i?'#df7138':'#2265bd'}))} xLabel="目标角度 °" yLabel="偏差 °"/>
          <Curve title="外部参考定位偏差" series={['forward','reverse'].map((direction,i)=>({points:(position.external||[]).filter(p=>p.direction===direction),x:'targetDeg',y:'mean',color:i?'#df7138':'#2265bd'}))} xLabel="目标角度 °" yLabel="偏差 °"/>
          {!report.running&&<div className="sx-reference"><h4>补充独立角度测量</h4><p>先“查看完整结果”，下载模板；按访问编号填写外部仪器实测角度后导入。参考零位＋目标角度为期望参考坐标。分析另存在本浏览器并随报告导出，原始驱动记录保持只读。</p>
            <button className="button" disabled={!report.positionVisits?.length} onClick={()=>saveFile(`position-${report.id}-reference.csv`,'\uFEFFvisit_id,target_deg,reference_deg\r\n'+report.positionVisits.map(v=>`${v.visitId},${v.nominalDeg},`).join('\r\n'))}>下载参考角度模板</button>
            <div className="sx-fields"><label>参考仪器<input aria-label="参考仪器" value={instrument.name} onChange={e=>setInstrument(old=>({...old,name:e.target.value}))}/></label><label>外部参考零位 °<input aria-label="外部参考零位 °" type="number" value={instrument.zeroDeg} onChange={e=>setInstrument(old=>({...old,zeroDeg:e.target.value}))}/></label></div>
            <label className="sx-check"><input type="checkbox" checked={instrument.aligned} onChange={e=>setInstrument(old=>({...old,aligned:e.target.checked}))}/>参考坐标和零位已经对齐，角度单位为 °</label>
            <label>导入参考角度 CSV<input aria-label="导入参考角度 CSV" type="file" accept=".csv,text/csv" disabled={busy||anyActive} onChange={event=>{void importReferences(event.target.files?.[0]);event.target.value='';}}/></label>
          </div>}</>}
        {report.category==='thermal'&&<><div className="sx-metrics">{[['初温 °C',temperature.initialC],['当前/末温 °C',temperature.lastC],['最高温 °C',temperature.maxC],['相对环境温升 K',temperature.riseK],['相对初温变化 K',temperature.changeFromInitialK],['观察窗口斜率 °C/min',temperature.slopeCPerMin]].map(([label,value])=><div key={label}><span>{label}</span><strong>{SHOW(value,2)}</strong></div>)}</div>
          <p>测点：现有电机 NTC · 独立有效温度样本 {temperature.count||0} · {temperature.stable?'满足本配方热稳定观察条件':'尚未满足热稳定观察条件'}</p>
          {report.heatingResult&&<p>升温段结果：{report.heatingResult.stable?'满足本配方热稳定观察条件':'未完成热稳定验证'}</p>}
          {report.verifiedOperatingPoint&&<div className="sx-note">本配方热稳定工况：{SHOW(report.verifiedOperatingPoint.n)} rpm / {SHOW(report.verifiedOperatingPoint.torque)} N·m / {SHOW(report.verifiedOperatingPoint.powerW)} W；连续工况转矩密度 {SHOW(report.verifiedOperatingPoint.torquePerKg)} N·m/kg，功率密度 {SHOW(report.verifiedOperatingPoint.powerWPerKg)} W/kg。仅对记录的负载和散热条件成立。</div>}
          <Curve title="温度—时间曲线（含降温段）" points={report.thermalPreview||[]} x="t" y="temperatureC" xLabel="时间 s" yLabel="温度 °C"/>
          <Curve title="相对环境温升曲线" points={Number.isFinite(report.config?.ambientC)?(report.thermalPreview||[]).map(p=>({...p,rise:p.temperatureC-report.config.ambientC})):[]} x="t" y="rise" xLabel="时间 s" yLabel="温升 K"/></>}
        {!!report.qualityIssues?.length&&<p className="sx-error">数据质量：{report.qualityIssues.join('、')}；原始数据保留。</p>}
      </>}
      {report.recordingId&&<div className="sx-actions"><a className="button" href={`/api/v1/recordings/files/${report.recordingId}/timeline.csv`} download>完整参数 CSV</a><a className="button" href={`/api/v1/recordings/files/${report.recordingId}/timeline.jsonl`} download>完整参数 JSONL</a></div>}
      {!report.running&&<div className="sx-actions"><a className="button" href={`/api/v1/${report.origin}/files/${report.id}/data.csv`} download>导出原始 CSV</a><button className="button" onClick={()=>saveFile(`specialized-${report.id}-report.json`,JSON.stringify(report,null,2),'application/json')}>导出报告 JSON</button>{report.referenceData&&Object.keys(report.referenceData).length>0&&<button className="button" onClick={()=>saveFile(`specialized-${report.id}-reference.csv`,'\uFEFFvisit_id,reference_deg\r\n'+Object.entries(report.referenceData).map(([id,angle])=>`${id},${angle}`).join('\r\n'))}>导出外部参考 CSV</button>}{report.specializedSchema===2&&<a className="button" href={`/api/v1/${report.origin}/files/${report.id}/timeline.jsonl`} download>导出专项来源 JSONL</a>}</div>}
    </>:<p className="sx-muted">选择配方并采集真实数据后显示。没有质量、体积或外部参考时，对应指标保持空缺。</p>}</section></div>
    <section className="sx-card sx-history"><h3>专项历史记录</h3><div className="sx-table"><table><thead><tr><th>时间 / 对象</th><th>项目 / 名称</th><th>状态</th><th>结果</th></tr></thead><tbody>{reports.map(row=><tr key={`${row.origin}-${row.id}`}><td>{new Date(row.startedAt*1000).toLocaleString()}<small>{subjectText(row.testSubject)}</small></td><td>{CATEGORIES[row.category]||'旧版专项'} / {row.name||row.config?.group||row.kind}</td><td>{row.state}</td><td><button className="button" onClick={()=>showReport(row)} disabled={busy||row.running}>查看完整结果</button></td></tr>)}</tbody></table></div>{!reports.length&&<p>暂无专项记录，旧记录不会删除。</p>}</section>
  </section>;
}
