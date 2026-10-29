import React, {useEffect, useRef, useState} from 'react';
import {apiFetch} from './apiClient.js';
import {setupText} from './testSubjects.js';
import './testSubjects.css';
export {subjectText} from './testSubjects.js';

const EMPTY = {motors: [], driverBoards: [], testSetups: [], selected: {motorId: '', driverBoardId: '', testSetupId: ''}};
const GROUPS = [
  {kind: 'motor', list: 'motors', label: '电机'},
  {kind: 'driverBoard', list: 'driverBoards', label: '驱动板'},
];
const NEW_SETUP = {id:'',name:'',hasGearbox:false,gearboxName:'',ratio:'1',massKg:'',volumeL:''};

export default function TestSubjectPicker() {
  const details = useRef(null);
  const [catalog, setCatalog] = useState(EMPTY);
  const [selected, setSelected] = useState(EMPTY.selected);
  const [names, setNames] = useState({motor: '', driverBoard: ''});
  const [editor, setEditor] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const locked = busy || catalog.configurationLocked;

  async function request(action = '', body) {
    const options = body ? {} : {cache: 'no-store', forceNetwork: true, signal: AbortSignal.timeout(5000)};
    if (body) {
      const response = await apiFetch('/api/health');
      if (!response.ok) throw new Error('通信服务未就绪，无法保存选择');
      const health = await response.json();
      Object.assign(options, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-MotorBench-Token': health.token}, body: JSON.stringify(body)});
    }
    const response = await apiFetch('/api/test-subjects'+action, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '无法读取电机和驱动板列表');
    return data;
  }

  async function load() {
    setBusy(true);
    try {
      const data = await request();
      if (!Array.isArray(data.motors) || !Array.isArray(data.driverBoards) || !data.selected) throw new Error('电机和驱动板列表格式无效');
      setCatalog(data); setSelected(data.selected); setEditor(null); setError('');
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  useEffect(() => {
    void load();
    const timer = setInterval(() => {
      request().then(data => {
        setCatalog(data);
        if (!details.current?.open) setSelected(data.selected);
      }).catch(failure => setError(failure.message));
    }, 1000);
    const outside = event => {if (!details.current?.contains(event.target)) details.current.open = false;};
    const escape = event => {if (event.key === 'Escape') details.current.open = false;};
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      clearInterval(timer);
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, []);

  async function create(kind) {
    setBusy(true); setError('');
    try {
      const data = await request('/create', {kind, name: names[kind]});
      setCatalog(data.catalog);
      setSelected(previous => ({...previous, [kind+'Id']: data.item.id, ...(kind==='motor'?{testSetupId:''}:{})}));
      if (kind==='motor') setEditor(null);
      setNames(previous => ({...previous, [kind]: ''}));
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  async function saveSetup() {
    setBusy(true); setError('');
    try {
      const body = {...editor,motorId:selected.motorId,ratio:Number(editor.ratio)};
      for (const key of ['massKg','volumeL']) body[key]=editor[key]===''?null:Number(editor[key]);
      if (editor.ratio.trim()==='' || !Number.isFinite(body.ratio) || ['massKg','volumeL'].some(key=>body[key]!==null&&!Number.isFinite(body[key]))) throw Error('减速比、质量或体积无效');
      const data = await request('/save-setup',body);
      setCatalog(data.catalog);setSelected(previous=>({...previous,testSetupId:data.item.id}));setEditor(null);
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  async function apply() {
    setBusy(true); setError('');
    try {
      setCatalog(await request('/select', selected));
      details.current.open = false;
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  const motor = catalog.motors.find(item => item.id === catalog.selected.motorId);
  const board = catalog.driverBoards.find(item => item.id === catalog.selected.driverBoardId);
  const setup = (catalog.testSetups||[]).find(item=>item.id===catalog.selected.testSetupId);
  const choices = (catalog.testSetups||[]).filter(item=>item.motorId===selected.motorId);
  const chosen = choices.find(item=>item.id===selected.testSetupId);
  const label = motor || board ? `${motor?.name || '选择电机'}${setup?` · ${setup.name} · ${setup.ratio}:1`:''} / ${board?.name || '选择驱动板'}` : '选择电机 / 驱动板';
  return <details className="test-subject-picker" ref={details} onToggle={event => {if (event.target===event.currentTarget && event.currentTarget.open) void load();}}>
    <summary className="button" aria-label="选择电机和驱动板" aria-haspopup="dialog" title={label}>
      <span>{label}</span><span aria-hidden="true">⌄</span>
    </summary>
    <div className="subject-popover" role="dialog" aria-label="电机和驱动板列表">
      {GROUPS.map(({kind, list, label}) => <section key={kind}>
        <label>{label}<select aria-label={`测试${label}`} value={selected[kind+'Id']} disabled={locked}
          onChange={event => {setSelected(previous => ({...previous, [kind+'Id']: event.target.value,...(kind==='motor'?{testSetupId:''}:{})}));if(kind==='motor')setEditor(null);}}>
          <option value="">未选择</option>
          {catalog[list].map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>
        <form onSubmit={event => {event.preventDefault(); void create(kind);}}>
          <input aria-label={`新建${label}名称`} placeholder={`新${label}名称`} maxLength={80} value={names[kind]} disabled={locked}
            onChange={event => setNames(previous => ({...previous, [kind]: event.target.value}))}/>
          <button className="button" disabled={locked || !names[kind].trim()}>新建{label}</button>
        </form>
      </section>)}
      <section>
        <label>测试配置<select aria-label="电机测试配置" value={selected.testSetupId||''} disabled={locked||!selected.motorId}
          onChange={event=>{setSelected(previous=>({...previous,testSetupId:event.target.value}));setEditor(null);}}>
          <option value="">未选择</option>{choices.map(item=><option key={item.id} value={item.id}>{setupText(item)}</option>)}
        </select></label>
        <div className="setup-actions"><button className="button" disabled={locked||!selected.motorId} onClick={()=>setEditor({...NEW_SETUP})}>新建配置</button>
          <button className="button" disabled={locked||!chosen} onClick={()=>setEditor({...chosen,ratio:String(chosen.ratio),massKg:chosen.massKg==null?'':String(chosen.massKg),volumeL:chosen.volumeL==null?'':String(chosen.volumeL)})}>编辑配置</button></div>
        {editor&&<div className="setup-editor" role="group" aria-label="测试配置编辑">
          <label>配置名称<input aria-label="测试配置名称" value={editor.name} maxLength={80} disabled={locked} onChange={e=>setEditor({...editor,name:e.target.value})}/></label>
          <label>机械形式<select aria-label="机械形式" value={editor.hasGearbox?'gearbox':'bare'} disabled={locked} onChange={e=>setEditor({...editor,hasGearbox:e.target.value==='gearbox',ratio:e.target.value==='bare'?'1':editor.ratio})}>
            <option value="bare">裸机 / 电机轴直测</option><option value="gearbox">带减速箱 / 输出轴测量</option></select></label>
          {editor.hasGearbox&&<label>减速箱名称<input aria-label="减速箱名称" maxLength={80} value={editor.gearboxName} disabled={locked} onChange={e=>setEditor({...editor,gearboxName:e.target.value})}/></label>}
          <label>转子 / 被测轴减速比<input aria-label="测试配置减速比" type="number" step="any" value={editor.ratio} disabled={locked||!editor.hasGearbox} onChange={e=>setEditor({...editor,ratio:e.target.value})}/></label>
          {['massKg','volumeL'].map((key,i)=><label key={key}>{i===0?'被测对象质量 kg':'被测对象体积 L'}<input aria-label={i===0?'配置质量 kg':'配置体积 L'} type="number" step="any" value={editor[key]} disabled={locked} onChange={e=>setEditor({...editor,[key]:e.target.value})}/></label>)}
          <p>裸机填电机本体质量和体积；带减速箱填本次被测总成。未知时留空。减速比不会修改驱动板参数。</p>
          <div className="setup-actions"><button className="button" disabled={locked||!editor.name.trim()} onClick={saveSetup}>保存配置</button><button className="button" disabled={busy} onClick={()=>setEditor(null)}>取消编辑</button></div>
        </div>}
      </section>
      <p>{catalog.configurationLocked?'测试或记录进行中，本次配置已冻结，结束后可切换。':'应用后统一用于新测试、显示换算和记录；已有记录保持原配置。'}</p>
      {error && <p role="alert" className="hw-inline-error">{error}</p>}
      <button className="button primary" onClick={apply} disabled={locked||!!editor}>应用选择</button>
    </div>
  </details>;
}
