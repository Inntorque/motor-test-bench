import test from 'node:test';
import assert from 'node:assert/strict';
import {MotorDevice} from '../browser/motor.ts';
import {CurrentMotor,CoupledCurrent} from '../browser/control.ts';
import {CAN,O,schema} from '../browser/mbp-codec.ts';
import {now,utc,sleep} from '../browser/bytes.ts';
import {motorColumns} from '../browser/recording-data.ts';

function rig(t,{mismatch=false,holdIdentity=false,dropHeartbeat=false,nativeLease=false}={}) {
  const commands=[],events=[],device=new MotorDevice({},(kind,data)=>events.push({kind,data}));
  const uid=Uint8Array.from({length:16},(_,i)=>i+1);
  let state=8,target=0,startedAt=0;
  const telemetry={fault:0,flags:7,temperature:25,temperatureLimit:80,rpm:100,id:.05,position:123.45,dropSnapshot:false,state:null};
  device.node=1;device.wire=schema.wire;
  device.identity={[O.device_uid]:uid,[O.hardware_identity]:'455V1.3 + 5014',[O.firmware_version]:'0x0914',
    [O.control_modes]:32,[O.feedback_fields]:(1<<24)-1,[O.optional_features]:5,[O.readiness_flags]:37,
    [O.iq_max]:18000,[O.phase_protection_current]:18000,[O.motor_temperature_trip]:80000};
  const publish=()=>{
    const on=state===11||state===17;
    if(nativeLease&&on&&now()-startedAt>=4)return;
    device.state={...device.state,state:on?2:0,mode:state===17?5:0,flags:on?1:0};
    device.stateAt=device.sampleAt=now();device.stateUtc=device.sampleUtc=utc();
    device.sample={fields:{0:1,1:0,2:target*1000,3:target*1000,4:0,5:100000,6:0,7:0,10:25000,12:0},flags:Object.fromEntries(Array.from({length:24},(_,i)=>[i,1]))};
  };
  publish();device.refreshReadiness=async()=>{};device.channels=[];
  device.bus={closed:false,interface:'canable',close:async()=>{},send:async(id,d)=>{
    if(id===CAN.stop+1){state=8;publish();device.state.lastStopId=new DataView(d.buffer,d.byteOffset,d.byteLength).getUint32(4,true);return;}
    assert.equal(id,0x610);assert.equal(d[0],0xd0);assert.equal(d[1],2);
    const action=d[3],value=new DataView(d.buffer,d.byteOffset,d.byteLength).getInt16(4,true);
    commands.push({action,value});
    if(action===23){
      if(holdIdentity)return;
      const meta=new Uint8Array(140);meta.set(uid.slice(0,12),128);if(mismatch)meta[128]^=1;
      queueMicrotask(()=>{for(let i=0;i<28;i++)device.receive(0x590,Uint8Array.of(0xdb,d[2],i,...meta.slice(i*5,i*5+5)));});return;
    }
    if(action===1){
      if(telemetry.dropSnapshot)return;
      queueMicrotask(()=>{for(const code of [0xd2,0xd3,0xd4,0xd5,0xd6,0xd7]){
        const row=Uint8Array.of(code,d[2],0,0,0,0,0,0);
        const values=new DataView(row.buffer);
        if(code===0xd2){row[2]=telemetry.state??state;row[3]=telemetry.fault;row[4]=telemetry.flags;}
        if(code===0xd3){values.setInt16(2,telemetry.rpm*10,true);values.setInt16(4,telemetry.id*1000,true);values.setInt16(6,target*1000,true);}
        if(code===0xd6){values.setInt16(2,telemetry.temperature*100,true);values.setInt16(4,telemetry.temperatureLimit*100,true);values.setInt16(6,state===17?target*1000:0,true);}
        if(code===0xd7)values.setInt32(2,telemetry.position*100,true);
        device.receive(0x590,row);
      }});return;
    }
    if(action===6&&dropHeartbeat)return;
    if(action===19){assert.equal(value,0);state=17;target=2;startedAt=now();}
    if(action===8){assert.ok(Math.abs(value)<=5370);state=11;target=value/1000;startedAt=now();}
    if(action===9){assert.equal(state,11);target=value/1000;}
    if(action===5){state=8;target=0;}
    publish();queueMicrotask(()=>device.receive(0x590,Uint8Array.of(0xd1,2,d[2],0,53,48,49,52)));
  }};
  const timer=setInterval(publish,20),motor=new CurrentMotor(device,{});
  t.after(async()=>{await motor.stop();await motor.task;clearInterval(timer);await device.close();});
  return {motor,device,commands,events,telemetry};
}

test('5014 dyno uses action 19 with no fixed current or MBP prepare; heartbeat and native STOP are confirmed',async t=>{
  const {motor,device,commands}=rig(t);
  await motor.start({mode:'dyno'});
  await device.wait(()=>motor.run.phase==='running',1000,'not running');
  await sleep(330);
  assert.equal(device.snapshot().motor.mode,5);assert.equal(motor.run.targetA,2);
  assert.deepEqual(commands.filter(c=>[8,9,19].includes(c.action)),[{action:19,value:0}]);
  assert.ok(commands.filter(c=>c.action===6).length>=3);
  await motor.stop();await motor.task;
  assert.equal(motor.run.stopConfirmed,true);assert.equal(device.snapshot().motor.output_on,false);
  const count=commands.filter(c=>c.action===6).length;await sleep(180);
  assert.equal(commands.filter(c=>c.action===6).length,count);
  assert.ok(commands.some(c=>c.action===5));
});

test('5014 current loop uses signed mA, action 9 updates, and native feedback',async t=>{
  const {motor,device,commands}=rig(t);
  await motor.start({mode:'current_loop',targetA:1.25});
  await device.wait(()=>motor.run.phase==='running',1000,'not running');
  assert.equal(device.state.mode,0);assert.equal(device.snapshot().motor.mode,1);
  await device.setNativeCurrent(-.5);
  await device.wait(()=>device.snapshot().feedback.iq_a===-.5,500,'current feedback did not update');
  assert.equal(device.snapshot().control.iq_target_ma,-500);
  assert.equal(device.snapshot().feedback.iq_a,-.5);
  assert.deepEqual(commands.filter(c=>[8,9,19].includes(c.action)),[{action:8,value:1250},{action:9,value:-500}]);
  await assert.rejects(device.setNativeCurrent(10),/电流/);
});

for(const mode of ['current_loop','dyno'])test(`${mode} remains running beyond the firmware MBP lease cutoff using fresh native data`,async t=>{
  const {motor,device,commands}=rig(t,{nativeLease:true});
  await motor.start({mode,targetA:1});
  await device.wait(()=>motor.run.phase==='running',1000,'not running');
  await sleep(5800);
  assert.equal(motor.run.running,true);
  assert.equal(motor.run.reason,'');
  assert.ok(now()-device.stateAt>1.5,'MBP status must have expired as on the real board');
  const snapshot=device.snapshot(),columns=motorColumns(snapshot,9.67);
  assert.equal(snapshot.feedbackSource,'foc-5014');
  assert.equal(snapshot.feedback.stale,false);
  assert.ok(utc()-snapshot.feedback.updated_at<.3);
  assert.equal(columns.actual_iq_A,mode==='dyno'?2:1);
  assert.equal(columns.actual_id_A,.05);
  assert.equal(columns.rotor_speed_rpm,100);
  assert.equal(columns.rotor_position_deg,123.45);
  assert.equal(columns.motor_ntc_C,25);
  assert.equal(columns.phase_a_A,null,'unsupported native fields must not reuse old MBP values');
  assert.equal(columns.motor_device_time_us,null);
  assert.equal(columns.reported_iq_reference_A,mode==='dyno'?2:null,'a commanded value is not measured feedback');
  await device.diagnostics();
  assert.equal(motor.run.running,true);
  assert.ok(commands.filter(c=>c.action===6).length>30);
  await motor.stop();await motor.task;
  assert.equal(motor.run.stopConfirmed,true);
  assert.equal(device.snapshot().motor.output_on,false);
});

test('native control finishes outstanding MBP traffic before START and pauses diagnostic reads',async t=>{
  const {motor,device,commands}=rig(t);
  let release;device.queues[0].run(()=>new Promise(resolve=>{release=resolve;}));
  await motor.start({mode:'current_loop',targetA:1});
  await sleep(20);
  assert.equal(commands.length,0);
  await device.diagnostics();
  const rejected=assert.rejects(device.request(2),/原生控制/);
  release();
  await rejected;
  await device.wait(()=>motor.run.phase==='running',1000,'not running');
});

for(const [name,change,reason] of [
  ['driver fault',s=>{s.fault=3;},/原生驱动故障 3/],
  ['invalid encoder',s=>{s.flags&=~2;},/编码器/],
  ['invalid NTC',s=>{s.flags&=~4;},/温度/],
  ['overtemperature',s=>{s.temperature=81;},/温度/],
  ['unexpected control mode',s=>{s.state=17;},/模式变化/],
  ['missing native snapshot with a live heartbeat',s=>{s.dropSnapshot=true;},/完整应答/],
])test(`${name} still stops native control even with fresh MBP feedback`,async t=>{
  const {motor,device,commands,telemetry}=rig(t);
  await motor.start({mode:'current_loop',targetA:1});
  await device.wait(()=>motor.run.phase==='running',1000,'not running');
  change(telemetry);
  await device.wait(()=>!motor.run.running,1500,'failure did not stop the run');
  await motor.task;
  assert.match(motor.run.reason,reason);
  assert.ok(commands.some(c=>c.action===5));
});

test('expired native feedback never falls back to fresh MBP data',async t=>{
  const {motor,device}=rig(t);
  await motor.start({mode:'current_loop',targetA:1});
  await device.wait(()=>motor.run.phase==='running',1000,'not running');
  clearInterval(device.nativeTimer);
  device.nativeSnapshotAt=now()-2;
  assert.throws(()=>device.snapshot(),/原生控制状态反馈过期/);
  await motor.task;
  assert.match(motor.run.reason,/原生控制状态反馈过期/);
  assert.equal(motor.run.stopConfirmed,true);
});

test('STOP while draining an MBP read prevents the queued native START',async t=>{
  const {motor,device,commands}=rig(t);
  let release;device.queues[0].run(()=>new Promise(resolve=>{release=resolve;}));
  await motor.start({mode:'current_loop',targetA:1});
  await sleep(10);
  await motor.stop();
  release();await motor.task;
  assert.equal(commands.some(c=>[8,19].includes(c.action)),false);
  assert.equal(motor.run.stopConfirmed,true);
});

test('out-of-range or obsolete fixed-current requests never start the board',async t=>{
  const {motor,commands}=rig(t);
  await assert.rejects(motor.start({mode:'current_loop',targetA:10}),/目标 Iq/);
  await assert.rejects(motor.start({mode:'full_current'}),/已删除/);
  assert.equal(commands.length,0);
});

test('UID mismatch prevents native START and is recorded as the failure',async t=>{
  const {motor,commands}=rig(t,{mismatch:true});
  await motor.start({mode:'dyno'});await motor.task;
  assert.match(motor.run.reason,/UID 不一致/);
  assert.equal(commands.some(c=>[8,19].includes(c.action)),false);
});

test('STOP during native identity check cancels the pending start without later energizing',async t=>{
  const {motor,device,commands}=rig(t,{holdIdentity:true});
  await motor.start({mode:'dyno'});
  await device.wait(()=>commands.some(c=>c.action===23),500,'no identity');
  await motor.stop();await motor.task;
  assert.equal(commands.some(c=>[8,19].includes(c.action)),false);
  assert.equal(motor.run.stopConfirmed,true);
});

test('missing native heartbeat ACK ends the run and sends STOP',async t=>{
  const {motor,commands}=rig(t,{dropHeartbeat:true});
  await motor.start({mode:'dyno'});await motor.task;
  assert.equal(motor.run.running,false);assert.match(motor.run.reason,/停止取消|完整应答/);
  assert.ok(commands.some(c=>c.action===5));
});

test('current target is locked during loading and stale run IDs cannot change it',async()=>{
  let updates=0;
  const motor={device:{setControlCurrent:async()=>updates++},run:{},status:()=>({})};
  const coupled=new CoupledCurrent({motor,brake:{status:()=>({})},safety:()=>({})});
  coupled.run={id:'one',running:true,motorMode:'current_loop',phase:'awaiting_load'};
  await assert.rejects(coupled.setCurrent({id:'old',targetA:1}),/仅可/);
  await coupled.setCurrent({id:'one',targetA:1});assert.equal(updates,1);
  coupled.run.loadRequested=true;
  await assert.rejects(coupled.setCurrent({id:'one',targetA:2}),/仅可/);assert.equal(updates,1);
});
