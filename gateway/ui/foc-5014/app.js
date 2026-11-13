import {setRotor} from '../../foc-shared/scene.js';
const $=id=>document.getElementById(id);
const meta=await fetch('/api/meta').then(r=>r.json());
$('port').textContent='浏览器直连';
if(meta.selfcheck)document.body.classList.add('selfcheck');
const states=['空闲','零偏校准','电阻低点','电阻高点','电感复位','电感阶跃','90°对齐','0°对齐','就绪','速度闭环','故障','电流闭环','位置闭环','正向整圈校准','反向整圈校准','力矩模式','MIT模式','测功模式'];
const faults=['无','编码器反馈异常','相电流ADC越界（旧固件也用于温度/母线异常）','相电流过流','转速超过3970 rpm','零偏校准失败','参数辨识失败','磁编对齐失败','控制计算超时','温度达到门限','持续反向拖动 / 回馈','测功电压模型无可行解（当前弱磁、电流和电压边界内无解）','运行心跳超过500ms未到达','电机温度采样无效或超过1.5秒未更新','母线电压采样无效、低于6V或超过1秒未更新'];
const stopSources=['操作者停止','通信超时','板端保护','电压模型无可行解','确认反向拖动','OTA升级'];
let latest=null,pending=false,lastSample=0,actionError="";
const modes={
  dyno:{state:17,start:19,name:'测功模式',startLabel:'启动测功',label:'可用 Iq 目标',unit:'A 峰值',readOnly:true},
  mit:{state:16,name:'MIT模式',label:'目标机械角度（编码器0～360°）',unit:'°',min:0,max:360,step:.1},
  torque:{state:15,start:12,apply:13,name:'力矩模式',label:'目标力矩',unit:'N·m',min:-.5,max:.5,step:.01},
  current:{state:11,start:8,apply:9,name:'电流环',label:'目标 Iq',unit:'A 峰值',min:-5.37,max:5.37,step:.05},
  speed:{state:9,start:3,apply:4,name:'速度环',label:'目标机械转速',unit:'rpm',min:-3276.7,max:3276.7,step:10},
  position:{state:12,start:10,apply:11,name:'位置环',label:'目标机械角度（编码器0～360°）',unit:'°',min:0,max:360,step:1}
};
const selectedMode=()=>modes[$('control-mode').value];
let renderedMode;
function renderMode(){
  const m=selectedMode(),input=$('target');
  if(m===renderedMode)return;
  renderedMode=m;
  $('mit-settings').hidden=$('control-mode').value!=='mit';
  const fixed=Boolean(m.readOnly);
  input.readOnly=fixed;
  $('minus').disabled=$('plus').disabled=fixed;
  if(!fixed){input.min=m.min;input.max=m.max;input.step=m.step;}
  input.placeholder=fixed?'自动分配':'';
  input.value=fixed?'':0;
  $('target-name').textContent=m.label;$('target-unit').textContent=m.unit;
  $('run').textContent='02 '+(m.startLabel??'启动'+m.name);
}
$('control-mode').onchange=renderMode;
renderMode();
let calibrationObserved=false,calibrationRequested=false;
const calibrationStages={1:[0,'1/8 · 电流零偏采样'],2:[1,'2/8 · 电阻低点辨识'],3:[2,'3/8 · 电阻高点辨识'],4:[3,'4/8 · 电感辨识（复位 / 阶跃）'],5:[3,'4/8 · 电感辨识（复位 / 阶跃）'],6:[4,'5/8 · 磁编90°初始对齐'],7:[5,'6/8 · 磁编0°初始对齐'],13:[6,'7/8 · 正向整圈校准'],14:[7,'8/8 · 反向整圈校准']};
function calibrationProgress(value,text){
  if(value!=null)$('calibration-progress').value=value;
  $('calibration-stage').textContent=text;
}
function renderCalibration(d){
  if(!d){
    if(calibrationObserved||calibrationRequested)calibrationProgress(null,'数据中断，等待板端确认校准状态');
    return;
  }
  const stage=calibrationStages[d.state];
  if(stage){
    calibrationObserved=true;calibrationRequested=false;
    let [progress,text]=stage;
    if(d.state===13||d.state===14){
      progress+=Math.min(1,Math.max(0,d.calibration_travel_deg)/360);
      text+=` · 已转 ${d.calibration_travel_deg.toFixed(1)}° / 至少360°`;
    }
    calibrationProgress(progress,text);
  }else if(d.state===10){
    if(calibrationObserved||calibrationRequested)calibrationProgress(null,'校准失败：'+(faults[d.fault]||d.fault));
    else calibrationProgress(d.calibrated?8:0,d.calibrated?'故障已锁存：'+(faults[d.fault]||d.fault)+'；已有校准保留':'故障阻止校准：'+(faults[d.fault]||d.fault));
    calibrationObserved=calibrationRequested=false;
  }else if(d.calibrated&&d.calibration_pending){
    calibrationProgress(8,'校准完成，正在保存到板端');
  }else if(calibrationObserved){
    calibrationObserved=calibrationRequested=false;
    if(d.state===8&&d.calibrated&&d.fault===0){
      calibrationProgress(8,d.calibration_saved?'校准完成，已保存到板端 · 8/8':'校准完成，但保存失败；仅本次有效');
      $('calibration-result').textContent=`R = ${d.phase_r.toFixed(3)} Ω · L = ${(d.phase_l*1000).toFixed(3)} mH。${d.calibration_saved?'已保存，断电保留。':'未保存，复位后将恢复上次已保存校准；没有旧记录则需重新校准。'}`;
      $('calibration-complete').showModal();
    }else calibrationProgress(null,'校准已中止，未完成');
  }else if(!calibrationRequested&&d.calibrated)calibrationProgress(8,d.calibration_saved?'设备校准已保存 · 8/8':'设备已有校准，尚未保存到板端');
  else if(!calibrationRequested&&!d.calibrated&&$('calibration-progress').value===8)calibrationProgress(0,'等待校准');
}
const runningStates=[9,11,12,15,16,17];
let diagnosticPrevious=null,diagnosticKey='',lastStop=null,stopRequested=false;
function renderDiagnostic(s,statusError,fresh){
  const d=s.data;
  if(fresh&&!s.error){
    const key=`5014-last-stop:${s.interface}:${s.channel}:${$('node').value}`;
    if(key!==diagnosticKey){
      diagnosticKey=key;diagnosticPrevious=null;
      try{lastStop=JSON.parse(sessionStorage.getItem(key));}catch{lastStop=null;}
    }
    if(runningStates.includes(d.state)&&!runningStates.includes(diagnosticPrevious?.state))stopRequested=false;
    let reason='';
    const stopSource=d.dyno_stop?stopSources[d.dyno_stop.source]??'未知来源':null;
    if(d.fault&&d.fault!==diagnosticPrevious?.fault)
      reason=`${faults[d.fault]??'未知故障'}（故障码 ${d.fault}）`+(stopSource?`；测功停机来源：${stopSource}`:'');
    else if(runningStates.includes(diagnosticPrevious?.state)&&[0,8].includes(d.state))
      reason=stopSource?`测功停机：${stopSource}`:stopRequested?'操作者请求关闭输出':'板端停止输出，但未上报原因；旧固件的无故障停机无法追溯';
    if(reason){
      const before=d.fault?d:diagnosticPrevious;
      lastStop={reason,at:new Date().toLocaleTimeString(),rpm:before.rpm,
        vbus:before.vbus_v,temperature:before.ntc_valid?before.temperature:null,isr:before.isr_us};
      try{sessionStorage.setItem(key,JSON.stringify(lastStop));}catch{}
      stopRequested=false;
    }
    diagnosticPrevious=d;
  }
  let text;
  if(statusError||s.error)text='通信异常：'+(statusError||s.error);
  else if(!fresh)text=s.connected?'停机原因：等待新鲜的板端反馈':'停机原因：未连接，无法读取';
  else if(d.fault)text=`故障原因：${faults[d.fault]??'未知故障'}（故障码 ${d.fault}）`;
  else if(runningStates.includes(d.state))text='运行中，板端未报告故障';
  else if(lastStop?.reason)text=`历史故障 / 停机（观察于 ${lastStop.at}）：${lastStop.reason}`;
  else text=d.calibrated?'停机原因：暂无记录，当前就绪':'停机原因：暂无记录，校准未完成';
  if(fresh&&!runningStates.includes(d.state)&&lastStop?.reason){
    const voltage=lastStop.vbus==null?'无效':lastStop.vbus.toFixed(2)+'V';
    const temperature=lastStop.temperature==null?'无效':lastStop.temperature.toFixed(1)+'℃';
    text+=`；当时速度 ${lastStop.rpm.toFixed(1)}rpm，母线 ${voltage}，温度 ${temperature}，ISR ${lastStop.isr}μs`;
  }
  $('control-hint').textContent=text;
}
const history=[];
function error(text){$('error').textContent=text;$('error').hidden=!text;}
async function post(route,data={}) {
  pending=true;
  try{
    const r=await fetch('/api/'+route,{method:'POST',headers:{'Content-Type':'application/json','X-Control-Token':meta.token},body:JSON.stringify(data)});
    const body=await r.json();if(!r.ok)throw Error(body.error||'操作失败');actionError='';error('');
  }catch(e){if(route==='command'&&data.action===5)stopRequested=false;actionError=e.message;error(e.message);throw e;}
  finally{pending=false;}
}
function action(id,fn){$(id).onclick=()=>fn().catch(()=>{});}
action('connect',()=>post('connect',{interface:$('interface').value,channel:$('channel').value,node:Number($('node').value)}));
action('disconnect',()=>post('disconnect'));
action('calibrate',async()=>{
  if(!confirm('校准会使能桥臂，机械正反各转至少360°，约25秒。确认电机已装轴承、空载且可自由整圈转动？'))return;
  calibrationObserved=false;calibrationRequested=true;
  calibrationProgress(0,'正在请求校准');
  try{
    await post('command',{action:2});
    if(!calibrationObserved)calibrationProgress(0,'请求已接受，等待板端进入校准');
  }catch(e){
    calibrationRequested=false;calibrationProgress(0,'校准请求失败：'+e.message);throw e;
  }
});
function control(start){
  if($('control-mode').value==='mit')return post('mit',{start,position:Number($('target').value),
    torque:Number($('mit-torque').value),speed:Number($('mit-speed').value),direction:Number($('mit-direction').value)});
  const mode=selectedMode();
  return post('command',{action:start?mode.start:mode.apply,value:mode.readOnly?0:Number($('target').value)});
}
action('run',()=>control(true));
action('apply',()=>control(false));
action('stop',async()=>{
  stopRequested=true;
  await post('command',{action:5});
  if(calibrationRequested&&!calibrationObserved){
    calibrationRequested=false;calibrationProgress(null,'校准已中止，未完成');
  }
});
action('reset-fault',()=>post('command',{action:20}));
action('export-dyno',()=>post('export-dyno'));
action('save-limit',()=>post('command',{action:7,value:Number($('limit').value)}));
for(const [id,direction] of [['minus',-1],['plus',1]])$(id).onclick=()=>{
  const m=selectedMode();$('target').value=Number(Math.max(m.min,Math.min(m.max,Number($('target').value)+direction*m.step)).toFixed(3));
};
function number(id,value,digits=1){$(id).textContent=value==null?'—':value.toFixed(digits);}
let displayedAngle=null;
const slowDisplayInterval=200;
let nextCurrentDisplay=0,nextTemperatureDisplay=0;
let displayedTemperatureValid=false;
function nextDisplayAt(now,deadline){
  return (deadline>0&&now<deadline+slowDisplayInterval?deadline:now)+slowDisplayInterval;
}
function renderMechanicalAngle(angle){
  if(angle==null)displayedAngle=null;
  else {
    const delta=Math.abs(angle-(displayedAngle??angle));
    if(displayedAngle===null||Math.min(delta,360-delta)>.09)
      displayedAngle=(Math.round(angle*10)%3600)/10;
  }
  number('angle',displayedAngle,1);
}
function drawTrace(){
  const c=$('trace'),w=c.width=Math.max(1,c.clientWidth),h=c.height=160,ctx=c.getContext('2d');
  ctx.fillStyle='#111a22';ctx.fillRect(0,0,w,h);ctx.strokeStyle='#2d3a47';ctx.lineWidth=1;
  for(let y=20;y<h;y+=30){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();}
  const now=Date.now();
  for(const [field,color,scale] of [['rpm','#71ead2',3276.7],['temperature','#f5bb70',150]]){
    ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();let previous=null;
    for(const point of history){
      const x=w-(now-point.t)/60000*w,v=point[field];if(v==null){previous=null;continue;}
      const y=field==='rpm'?80-v/scale*65:145-v/scale*130;
      if(previous&&point.t-previous<1500)ctx.lineTo(x,y);else ctx.moveTo(x,y);previous=point.t;
    }ctx.stroke();
  }
}
function flagText(flags){
  const names=[];
  if(flags&1)names.push('enc_ok');
  if(flags&2)names.push('enc_zero');
  if(flags&4)names.push('enc_one');
  if(flags&8)names.push('zero_valid');
  if(flags&0x10)names.push('adc_ok');
  if(flags&0x20)names.push('enc_spi_err');
  return names.join(',')||'none';
}
function renderSelfCheck(s,statusError=''){
  const d=s.data,fresh=!statusError&&s.connected&&d&&s.age!=null&&s.age<.8;
  $('connection').textContent=statusError?'● 状态读取失败':s.connected?'● 已连接':s.searching?'● 正在连接':'● 未连接';
  $('connection').style.color=fresh?'var(--cyan)':'var(--orange)';
  $('connect').disabled=Boolean(statusError)||s.connected||s.searching||pending;
  $('connect').textContent=(s.searching||pending&&!s.connected)?'正在连接…':$('interface').value==='auto'?'搜索并连接':'连接设备';
  $('disconnect').disabled=!s.connected||pending;
  for(const id of ['interface','node'])$(id).disabled=s.connected||s.searching||pending;
  $('channel').disabled=s.connected||s.searching||pending;
  if(s.connected)$('channel').value=s.channelId;
  /* 单次轮询失败时保留最近 2 秒的有效数据，避免整屏数值瞬间清空 */
  const value=(d&&s.connected&&s.age!=null&&s.age<2.0)?d:null;
  $('state').textContent=value?'自检采样中':'离线';
  $('calibrated').textContent=value?(value.zero_valid?'有效':'无效 / 未完成'):'—';
  $('encoder-ok').textContent=value?(value.enc_ok?'有效':'异常'):'—';
  $('fault').textContent=value?flagText(value.flags):'—';
  $('isr').textContent=value?String(value.loops):'—';
  number('angle',value?value.angle_deg:null,2);
  number('vbus',value?.vbus_v,2);
  number('electrical',null,2);
  number('position',null,2);
  number('rpm',null,0);
  number('id',value?value.ia:null,3);
  number('iq',value?value.ib:null,3);
  number('torque',value?value.ibus:null,3);
  number('resistance',value?value.temperature:null,1);
  number('inductance',value?value.board_temperature:null,1);
  const temp=value?value.temperature:null;
  number('temperature',temp,1);
  $('thermal-valid').textContent=temp==null?'未采样 / 无效':'B3950 近似值';
  $('thermal-bar').style.width=temp==null?'0%':Math.max(0,Math.min(100,temp/150*100))+'%';
  $('ntc-adc').textContent='—';
  $('ntc-r').textContent='—';
  $('scene-status').textContent=value&&value.enc_ok?'LIVE · KTH7823':'WAITING FOR ENCODER';
  if(value&&value.enc_ok)setRotor(value.angle_deg);
  $('logs').textContent=s.logs.join('\n')||'等待连接自检设备。';
  $('logs').scrollTop=$('logs').scrollHeight;
  error(statusError||s.error||actionError);
  if(value&&s.age<.2&&Date.now()-lastSample>90){history.push({t:Date.now(),rpm:null,temperature:temp});lastSample=Date.now();}
  while(history.length&&Date.now()-history[0].t>60000)history.shift();drawTrace();
}
function render(s,statusError=''){
  if(meta.selfcheck)return renderSelfCheck(s,statusError);
  renderMode();
  const d=s.data,fresh=!statusError&&s.connected&&d&&s.age!=null&&s.age<.8;
  $('connection').textContent=statusError?'● 状态读取失败':s.busy?'● 维护中':fresh?'● 已连接':s.connected?'● 数据过期':'● 未连接';
  $('connection').style.color=fresh?'var(--cyan)':'var(--orange)';
  const ready=fresh&&!s.busy&&!pending;
  $('connect').disabled=Boolean(statusError)||s.connected||s.searching||pending;
  $('connect').textContent=(s.searching||pending&&!s.connected)?'正在连接…':$('interface').value==='auto'?'搜索并连接':'连接设备';
  $('disconnect').disabled=!s.connected||s.busy||pending;
  for(const id of ['interface','node'])$(id).disabled=s.connected||s.searching||pending;
  $('channel').disabled=s.connected||s.searching||pending;
  if(s.connected)$('channel').value=s.channelId;
  $('calibrate').disabled=!ready||!d?.encoder_ok||![0,8].includes(d?.state);
  $('run').disabled=!ready||!d?.encoder_ok||d?.state!==8||!d?.calibrated||d?.calibration_pending;
  $('apply').disabled=!ready||d?.state!==selectedMode().state||Boolean(selectedMode().readOnly);
  for(const id of ['mit-torque','mit-speed','mit-direction'])$(id).disabled=!ready||![0,8,16].includes(d?.state);
  $('control-mode').disabled=!ready||![0,8,10].includes(d?.state);
  $('stop').disabled=!s.connected||s.busy;
  $('save-limit').disabled=!ready||![0,8,10].includes(d?.state);
  $('reset-fault').disabled=!ready||d?.state!==10;
  const value=fresh?d:null;
  renderCalibration(fresh&&!s.error?d:null);
  renderDiagnostic(s,statusError,fresh);
  if(selectedMode().readOnly)$('target').value=value?.state===17?value.dyno_iq_target.toFixed(2):'';
  $('position').previousElementSibling.textContent=value?.state===10?'故障前机械角反馈 / °':'目标机械角反馈 / °';
  number('position',value?.calibrated?value.position_deg:null,2);
  $('state').textContent=value?(value.state===16?(value.mit_holding?'MIT保持':'MIT移动'):states[value.state]||'未知'):'离线';
  $('calibrated').textContent=value?(value.calibrated?(value.calibration_saved?'完成 · 已保存':value.calibration_pending?'完成 · 保存中':'完成 · 未保存'):'未完成'):'—';
  $('encoder-ok').textContent=value?(value.encoder_ok?'有效':'异常'):'—';
  $('fault').textContent=value?(faults[value.fault]||value.fault):'—';
  renderMechanicalAngle(value?.encoder_ok?value.angle_deg:null);
  number('resistance',value?.phase_r,3);
  $('rpm').previousElementSibling.textContent=value?.state===10?'故障前机械速度 / rpm':'机械速度 / rpm';
  const rpm=value?Math.round(value.rpm):null;
  number('rpm',rpm!==null&&Math.abs(rpm)<=2?0:rpm,0);
  const currentActive=value&&[2,3,4,5,6,7,9,11,12,13,14,15,16,17].includes(value.state);
  const now=performance.now();
  if(currentActive?now>=nextCurrentDisplay:nextCurrentDisplay>0){
    number('id',currentActive?value.id:null,3);
    number('iq',currentActive?value.iq:null,3);
    number('torque',currentActive?value.iq*(.163/Math.SQRT2):null,3);
    nextCurrentDisplay=currentActive?nextDisplayAt(now,nextCurrentDisplay):0;
  }
  number('electrical',value?.calibrated?value.electrical_deg:null,2);
  number('inductance',value?value.phase_l*1000:null,3);
  number('vbus',value?.vbus_v,2);$('isr').textContent=value?value.isr_us+' μs':'—';
  const temp=value?.ntc_valid?value.temperature:null;
  const temperatureValid=temp!=null;
  const temperatureValidityChanged=temperatureValid!==displayedTemperatureValid;
  if(value?now>=nextTemperatureDisplay||temperatureValidityChanged:nextTemperatureDisplay>0){
    number('temperature',temp,1);
    $('thermal-valid').textContent=temp==null?'未采样 / 无效':'B3950 近似值';
    $('thermal-bar').style.width=temp==null?'0%':Math.max(0,Math.min(100,temp/150*100))+'%';
    $('ntc-adc').textContent=value?.ntc_adc??'—';
    $('ntc-r').textContent=value?.ntc_ohm?Math.round(value.ntc_ohm/100)/10+' kΩ':'—';
    nextTemperatureDisplay=value?nextDisplayAt(now,temperatureValidityChanged?0:nextTemperatureDisplay):0;
    displayedTemperatureValid=temperatureValid;
  }
  if(value&&document.activeElement!==$('limit'))$('limit').value=value.temperature_limit;
  $('scene-status').textContent=value?.encoder_ok?'LIVE · KTH7823':'WAITING FOR ENCODER';
  if(value?.encoder_ok)setRotor(value.angle_deg);
  if(value&&s.age<.2&&Date.now()-lastSample>90){history.push({t:Date.now(),rpm:value.state===10?null:value.rpm,temperature:temp});lastSample=Date.now();}
  if(!fresh&&history.length&&history.at(-1).rpm!==null)history.push({t:Date.now(),rpm:null,temperature:null});
  while(history.length&&Date.now()-history[0].t>60000)history.shift();drawTrace();
  $('logs').textContent=s.logs.join('\n')||'等待连接独立设备。';$('logs').scrollTop=$('logs').scrollHeight;
  $('export-dyno').disabled=!s.capture;
  $('job').textContent=s.job?`${s.job.action} / ${s.job.state}${s.job.error?' / '+s.job.error:''}`:'';
  error(statusError||s.error||actionError);
}
async function poll(){
  const nextPoll=performance.now()+20;
  try{
    const r=await fetch('/api/state');
    if(!r.ok)throw Error('上位机状态读取失败');
    const state=await r.json();
    render(state);
    latest=state;
  }catch(e){
    if(latest)render(latest,e.message);
    else error(e.message);
  }finally{setTimeout(poll,Math.max(0,nextPoll-performance.now()));}
}
poll();
