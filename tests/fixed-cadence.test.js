import test from 'node:test';
import assert from 'node:assert/strict';
import {CoupledCurrent} from '../browser/control.ts';
import {now,utc,sleep} from '../browser/bytes.ts';

function rig(t,{delay=()=>0,respond=true,speed=()=>100}={}) {
  let on=false,effective=0,serial=0,epoch=0;const commands=[],events=[];
  const motor={run:{running:false},device:{bus:{closed:false}},
    status(){return {connected:true,run:this.run,snapshot:{capabilities:{fields:(1<<2)|(1<<5)|(1<<6)|(1<<7)},motor:{output_on:this.run.running,fault:false,state:this.run.running?2:0,updated_at:utc()},encoder:{valid:true,stale:false},feedback:{speed_rpm:speed(coupled.run),position_deg:1,stale:false,position_stale:false,updated_at:utc(),position_at:utc()}}};},
    async start(){this.run={running:true,phase:'running',mode:'dyno',limitReached:true,limitReachedAt:now()};},
    async stop(){this.run.running=false;},
  };
  const power={config:{protocol:'ru',voltageDecimals:2,currentDecimals:3},status(){return {connected:true,config:this.config,snapshot:{status:128,receivedAt:utc()-.01,settings:[1,2400,20000],measured:[2400,1000]}};},async read(){return this.status();}};
  const brake={config:{},snapshot:{status:0},status(){return {connected:true,snapshot:{...this.snapshot,receivedAt:utc()}};},async read(){return this.status();},
    async setValues({current}){
      const loading=coupled.run.phase==='loading',target=coupled.run.targetNm||0;
      commands.push({kind:'set',target,current,at:utc()});
      if(loading)await sleep(delay(serial++));
      if(respond)effective=target;
    },
    async output(value,settings){
      if(!value)epoch++;
      const requestedEpoch=epoch;
      if(value)await this.setValues(settings);
      if(requestedEpoch!==epoch)throw new Error('开启已被停止取消');
      on=value;this.snapshot.status=value?128:0;commands.push({kind:'output',value,at:utc()});return this.status();
    },
  };
  const env={motor,channels:{'motor-supply':power,'brake-supply':brake},
    brake:{run:{running:false},status:()=>({}),stop:()=>brake.output(false)},
    sensor:{status:()=>({connected:true,snapshot:{receivedAt:utc(),torque:on?effective:.02,speed:speed(coupled.run),torqueDecimals:2,speedDecimals:1}})},
    recordings:{live:{id:'existing',running:true}},safety:()=>({}),observe:(kind,data)=>events.push({kind,data}),
    device:{async wait(check,timeout,message){const end=now()+timeout/1000;while(!check()){if(now()>end)throw new Error(message);await sleep(5);}}},
  };
  const coupled=new CoupledCurrent(env);
  t.after(async()=>{await coupled.stop();await coupled.task;});
  const start=async(startNm=0,maxNm=.3,requestDelay=0)=>{
    await coupled.start({mode:'torque_sweep',motorMode:'dyno',brake:{startNm,stepNm:.15,maxNm,intervalS:99}});
    await env.device.wait(()=>coupled.run.phase==='awaiting_load'||!coupled.run.running,1000,'not ready');
    assert.equal(coupled.run.phase,'awaiting_load',coupled.run.reason);
    assert.equal(coupled.run.characteristicPoints.length,0);
    const clickedAt=utc();if(requestDelay)await sleep(requestDelay);coupled.load({id:coupled.run.id,clickedAt});
    assert.equal(coupled.run.loadStartedAt,clickedAt);
    await env.device.wait(()=>coupled.run.phase==='loading',1000,'not loading');
  };
  return {coupled,start,commands,events,env};
}

test('variable device latency does not accumulate into the 2-second stage schedule or sensor sampling',async t=>{
  const {coupled,start,events}=rig(t,{delay:i=>[70,160,30][i%3]});
  await start(0,.3,300);await coupled.task;
  assert.match(coupled.run.reason,/阶梯完成/);
  assert.equal(coupled.run.torqueConfig.intervalS,2);
  const points=coupled.run.characteristicPoints,startAt=coupled.run.loadStartedAt;
  assert.deepEqual(points.map(p=>p.kind),['stage','stage','stage']);
  assert.deepEqual(events.filter(e=>e.kind==='characteristic_point').map(e=>e.data),points);
  assert.deepEqual(points.map(p=>p.torque),[0,.15,.3]);
  assert.deepEqual(points.map(p=>p.scheduledAt-startAt),[2,4,6]);
  for(const p of points){assert.ok(p.scheduledAt-p.receivedAt>=0&&p.scheduledAt-p.receivedAt<=.1);assert.ok(p.sampleCount>=8);}
  const stages=events.filter(e=>e.kind==='torque_stage').map(e=>e.data);
  assert.deepEqual(stages.map(s=>s.scheduledAt-startAt),[0,2,4]);
  for(const s of stages)assert.ok(Math.abs(s.startedAt-s.scheduledAt)<.1);
  assert.equal(coupled.run.stopConfirmed,true);
});

test('missing torque response does not stop the sweep or change the programmed currents',async t=>{
  const {coupled,start,commands}=rig(t,{respond:false});
  await start(.2,.35);await coupled.task;
  assert.match(coupled.run.reason,/阶梯完成/);
  assert.deepEqual(coupled.run.characteristicPoints.map(p=>p.torque),[.2,.35]);
  assert.deepEqual(coupled.run.characteristicPoints.map(p=>p.measuredTorque),[0,0]);
  assert.deepEqual(commands.filter(c=>c.kind==='set'&&c.target>0).map(c=>c.current),[.046,.125]);
  assert.equal(coupled.run.stopConfirmed,true);
});

test('the last calibrated target ends the sweep even when a legacy request asks for more',async t=>{
  const {coupled,start,commands}=rig(t);
  await start(2.7,99.9);await coupled.task;
  assert.match(coupled.run.reason,/标定范围内的力矩阶梯完成/);
  assert.equal(coupled.run.torqueConfig.maxNm,2.7);
  assert.deepEqual(coupled.run.characteristicPoints.map(p=>p.torque),[2.7]);
  assert.deepEqual(commands.filter(c=>c.kind==='set'&&c.target>0).map(c=>c.current),[.396]);
  assert.equal(coupled.run.stopConfirmed,true);
});

test('slow supply transaction stops at the deadline, never queues a catch-up step or late output enable',async t=>{
  const {coupled,start,commands}=rig(t,{delay:()=>2500});
  await start(.15,.3);await coupled.task;
  assert.match(coupled.run.reason,/控制或通信未完成/);
  assert.equal(coupled.run.loadSteps,1);assert.deepEqual(coupled.run.characteristicPoints,[]);
  assert.ok(coupled.run.measurementEndedAt-coupled.run.loadStartedAt<2.15);
  assert.equal(commands.filter(c=>c.kind==='set'&&c.target===.15).length,1);
  assert.equal(commands.some(c=>c.kind==='output'&&c.value),false);
});

test('operator STOP during asynchronous brake adjustment cancels subsequent enable and sampling',async t=>{
  const {coupled,start,commands}=rig(t,{delay:()=>300});
  await start(.15,.3);await coupled.stop();await coupled.task;
  assert.equal(commands.some(c=>c.kind==='output'&&c.value),false);
  assert.deepEqual(coupled.run.characteristicPoints,[]);
  assert.equal(coupled.run.stopConfirmed,true);
});

test('temporary standstill holds braking, then resumes on the original grid before a confirmed stall',async t=>{
  const speed=run=>{
    if(!run.loadStartedAt)return 100;
    const elapsed=utc()-run.loadStartedAt;
    return elapsed>=.2&&elapsed<3 || run.targetNm>=.3 ? 0:100;
  };
  const {coupled,start,commands}=rig(t,{speed});
  await start(.15,.6);await coupled.task;
  assert.match(coupled.run.reason,/连续零速 5 秒/);
  assert.equal(coupled.run.stopConfirmation.confirmed,true);
  const points=coupled.run.characteristicPoints,origin=coupled.run.loadStartedAt;
  assert.equal(points.length,2);
  assert.equal(points[0].kind,'stage');assert.equal(points[0].scheduledAt-origin,4);
  assert.equal(points[1].kind,'stall');assert.ok(points[1].receivedAt-origin>=9);
  assert.equal(coupled.run.loadSteps,2);
  assert.equal(commands.some(c=>c.kind==='set'&&c.at-origin>.3&&c.at-origin<3),false,'brake command is held during temporary zero');
  assert.equal(commands.some(c=>c.kind==='set'&&c.at-origin>4.2),false,'brake command is held through final confirmation');
});

test('operator STOP during zero-speed confirmation never records a maximum-torque endpoint',async t=>{
  const {coupled,start,env}=rig(t,{speed:run=>run.loadStartedAt&&utc()-run.loadStartedAt>.2?0:100});
  await start(.15,.6);
  await env.device.wait(()=>coupled.run.stopConfirmation?.state==='confirming',1000,'no standstill confirmation');
  await coupled.stop();await coupled.task;
  assert.deepEqual(coupled.run.characteristicPoints,[]);
  assert.equal(coupled.run.stallEndpoint,undefined);
  assert.equal(coupled.run.stopConfirmed,true);
});
