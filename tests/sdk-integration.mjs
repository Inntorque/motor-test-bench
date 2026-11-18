import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {MotorDevice} from '../browser/motor.ts';
import {IsoTp,CAN,OP} from '../browser/mbp-codec.ts';

const child=spawn(process.argv[2],[],{stdio:['pipe','pipe','inherit']});
const exited=new Promise(resolve=>child.once('exit',resolve));
const events=[];
const device=new MotorDevice({},(kind,data)=>events.push({kind,...data}));
device.node=1;
device.bus={closed:false,send:async(id,data)=>{child.stdin.write(`rx ${id.toString(16)} ${Buffer.from(data).toString('hex')}\n`);},close:async()=>{}};
device.channels=[0,1].map(channel=>new IsoTp(data=>device.bus.send((channel?CAN.diagnostic_request:CAN.request)+1,data),data=>device.response(channel,data),channel?256:1024));
createInterface({input:child.stdout}).on('line',line=>{
  const [id,data]=line.split(' ');
  device.receive(parseInt(id,16),Buffer.from(data,'hex'));
});
const clock=setInterval(()=>child.stdin.write(`tick ${Math.round(performance.now()*1000)}\n`),2);
try {
  await device.identify();
  await device.refreshReadiness();
  for(const [mode,current] of [['current_loop',1.25],['dyno',0]]){
    await device.startControl(mode,current,()=>{});
    assert.equal(device.snapshot().motor.output_on,true);
    if(mode==='current_loop'){
      await device.setControlCurrent(-.75);
      assert.equal(device.snapshot().feedback.iq_a,-.75);
    }
    await device.stop();
    assert.equal(device.snapshot().motor.output_on,false);
  }
  const starts=events.filter(e=>e.kind==='motor_command_request' && e.opcode===OP.start);
  assert.equal(starts.length,2);
  for(const start of starts){
    const replies=events.filter(e=>e.requestId===start.requestId && e.stage!==undefined);
    assert.deepEqual(replies.map(e=>e.stage),[0,2]);
  }
} finally {
  clearInterval(clock);
  clearInterval(device.heartbeatTimer);clearInterval(device.diagnosticTimer);
  child.stdin.end();
  assert.equal(await exited,0);
}
console.log('Host ↔ compiled C11 SDK: current loop, signed target, dyno, staged START, and confirmed STOP passed.');
