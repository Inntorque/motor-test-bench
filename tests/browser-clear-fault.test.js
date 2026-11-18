import test from 'node:test';
import assert from 'node:assert/strict';
import {MotorDevice} from '../browser/motor.ts';
import {Bench} from '../browser/bench.ts';
import {O} from '../browser/mbp-codec.ts';

function fixture({mismatch=false,reject=false,ready=true}={}) {
  const sent=[], device=new MotorDevice({}), uid=Uint8Array.from({length:16},(_,i)=>i+1);
  device.node=1; device.identity={[O.device_uid]:uid,[O.hardware_identity]:'455V1.3 + 5014',[O.firmware_version]:'0x0914'};
  device.bus={closed:false,interface:'canable',async send(id,data){
    sent.push({id,data:[...data]});
    if (data[3]===23) {
      const meta=new Uint8Array(140); meta.set(uid.slice(0,12),128); if(mismatch)meta[128]^=1;
      queueMicrotask(()=>{for(let i=0;i<28;i++)device.receive(0x590,Uint8Array.from([0xdb,data[2],i,...meta.slice(i*5,i*5+5)]));});
    } else if(data[3]===20) queueMicrotask(()=>device.receive(0x590,Uint8Array.from([0xd1,2,data[2],reject?1:0,53,48,49,52])));
  }};
  device.wait=async()=>{}; device.refreshReadiness=async()=>{};
  device.snapshot=()=>({motor:{fault:!ready,state:ready?0:4},readiness:{flags:ready?1:0,detail:'温度仍高'}});
  return {device,sent};
}

test('matches native UID, sends one clear and verifies fresh MBP readiness',async()=>{
  const {device,sent}=fixture(); await device.clearKnownFault(()=>{});
  assert.deepEqual(sent.map(row=>row.data[3]),[23,20]);
  assert.ok(sent.every(row=>row.id===0x610));
});
test('UID mismatch never sends clear',async()=>{
  const {device,sent}=fixture({mismatch:true});
  await assert.rejects(device.clearKnownFault(()=>{}),/UID 不一致/);
  assert.deepEqual(sent.map(row=>row.data[3]),[23]);
});
test('board rejection and failure to become ready remain errors',async()=>{
  const rejected=fixture({reject:true}); await assert.rejects(rejected.device.clearKnownFault(()=>{}),/板端拒绝/);
  const hot=fixture({ready:false}); await assert.rejects(hot.device.clearKnownFault(()=>{}),/仍未就绪/);
});
test('unconfirmed stop forbids even the identity read',async()=>{
  const {device,sent}=fixture(); await assert.rejects(device.clearKnownFault(()=>{throw Error('输出未停');}),/输出未停/);
  assert.equal(sent.length,0);
});
test('a stop or new operation during identity read cancels clear',async()=>{
  const {device,sent}=fixture(); let checks=0;
  await assert.rejects(device.clearKnownFault(()=>{if(++checks===2)throw Error('状态已变化');}),/状态已变化/);
  assert.deepEqual(sent.map(row=>row.data[3]),[23]);
});
test('backend rejects a clear while output is on or stop is unconfirmed',async()=>{
  const bench=new Bench({}, {},()=>{}); bench.claim=async()=>{};
  bench.coupled.run={running:false,stopConfirmed:true}; bench.motor.run={running:false,stopConfirmed:false};
  bench.brake.run={running:false}; bench.device.session=0n;
  bench.device.snapshot=()=>({motor:{output_on:true,age_s:0,fault:true,state:4}});
  let sent=false; bench.device.clearKnownFault=async check=>{check();sent=true;};
  const request=()=>bench.request('/api/motor-current/clear-fault','POST',{}, {'x-motorbench-token':bench.token});
  await assert.rejects(request(),/输出关闭/); assert.equal(sent,false);
  bench.coupled.run.stopConfirmed=false;
  await assert.rejects(request(),/确认输出关闭/); assert.equal(sent,false);
});
