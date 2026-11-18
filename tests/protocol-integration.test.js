import test from 'node:test';
import assert from 'node:assert/strict';
import {controlCapabilities} from '../protocol/capabilities.ts';
import {MotorDevice} from '../browser/motor.ts';
import {CurrentMotor} from '../browser/control.ts';
import {Bench} from '../browser/bench.ts';
import {VirtualBoard} from './fixtures/virtual-mbp.js';
import {O,OP,packet,objects} from '../browser/mbp-codec.ts';
import {pack,concat} from '../browser/bytes.ts';
async function rig(t,modeBits=2){
 const board=new VirtualBoard();board.identity[O.hardware_identity]='Third-party MBP drive';board.identity[O.control_modes]=modeBits;
 const device=new MotorDevice(board),motor=new CurrentMotor(device,{});
 t.after(async()=>{await motor.stop();await motor.task;await device.close();});
 await device.connect({device:'fixture',interface:'zqwl',node:1});const timer=setInterval(()=>board.publish(),30);t.after(()=>clearInterval(timer));
 return {board,device,motor};
}
test('capabilities distinguish generic current loop, dyno and unavailable limits',()=>{
 const identity=new VirtualBoard().identity;
 assert.equal(controlCapabilities(identity).currentLoop,true);assert.equal(controlCapabilities(identity).dyno,true);
 for(const field of [O.iq_min,O.iq_max,O.phase_run_limit_max,O.phase_protection_current]){const copy={...identity};delete copy[field];assert.equal(controlCapabilities(copy).currentLoop,false);}
 const currentOnly={...identity,[O.control_modes]:2};assert.equal(controlCapabilities(currentOnly).dyno,false);
 assert.equal(controlCapabilities({...identity,[O.feedback_fields]:20}).currentLoop,false);
 assert.equal(controlCapabilities(identity,5.37).adapter,'5014-native');
});
test('non-5014 standard board starts and changes a signed current target with fresh readback',async t=>{
 const {device,motor,board}=await rig(t);
 assert.equal(device.snapshot().capabilities.nativeControl,false);
 await motor.start({mode:'current_loop',targetA:-1.25});
 await device.wait(()=>motor.run.phase==='running',1000,'current loop did not start');
 assert.equal(device.snapshot().feedback.iq_a,-1.25);
 assert.equal(board.config[O.run_mode],1);assert.equal(board.config[O.direction],1);
 await device.setControlCurrent(.75);assert.equal(device.snapshot().control.iq_target_ma,750);
 await assert.rejects(device.setControlCurrent(99),/目标 Iq/);
 await motor.stop();await motor.task;assert.equal(motor.run.stopConfirmed,true);assert.equal(board.state,0);
 assert.ok(board.commands.some(c=>c.opcode===OP.prepare_run));
});
test('standard dyno mode requires declared MAX_AVAILABLE_IQ and supports feedback-driven operation',async t=>{
 const {device,motor,board}=await rig(t,32);
 await motor.start({mode:'dyno'});await device.wait(()=>motor.run.phase==='running',1000,'dyno did not start');
 assert.equal(board.mode,5);assert.equal(motor.run.targetA,27);
 await motor.stop();await motor.task;
});
test('unsupported generic mode is refused before any output command',async t=>{
 const {motor,board}=await rig(t,2);
 await assert.rejects(motor.start({mode:'dyno'}),/未声明/);
 assert.equal(board.commands.some(c=>c.opcode===OP.prepare_run||c.opcode===OP.start),false);
});
test('update preparation locks new commands, preserves STOP, and normal exit remains independent',async()=>{
 const store={get:async()=>null,put:async()=>{},entries:async()=>[]};
 const env=new Bench({call:async()=>[]},store,()=>{},true);
 env.desktopBusy=()=>true;assert.equal((await env.request('/api/desktop/update-ready','GET',{})).ready,false);
 env.desktopBusy=()=>false;assert.equal((await env.request('/api/desktop/update-ready','GET',{})).ready,true);
 await assert.rejects(env.request('/api/coupled-current/start','POST',{}),/准备更新/);
 await env.request('/api/desktop/update-cancel','GET',{});assert.equal(env.updating,false);
});

test('MBP progress responses do not complete a command before confirmation',()=>{
 const device=new MotorDevice({}),responses=[],failures=[];
 device.pending.set(77,{channel:0,opcode:OP.start,resolve:r=>responses.push(r),reject:e=>failures.push(e)});
 for(const [rc,stage] of [[1,0],[0,1]])device.response(0,packet(OP.start,77,pack('HBB',rc,stage,0),1));
 assert.equal(responses.length,0);assert.equal(failures.length,0);
 device.response(0,packet(OP.start,77,pack('HBB',0,2,0),1));
 assert.equal(responses.length,1);assert.equal(responses[0].stage,2);
});

test('5014 SDK 1.0 completes legacy reads at APPLIED while writes require CONFIRMED',()=>{
 const device=new MotorDevice({});device.node=1;
 const identity={[O.hardware_identity]:'455V1.3 + 5014',[O.sdk_version]:'1.0.0'};
 const completed=[];
 for(const op of [OP.identify,OP.read_state,OP.prepare_run]){
   device.pending.set(91,{channel:0,opcode:op,resolve:()=>completed.push(op)});
   device.response(0,packet(op,91,concat(pack('HBB',0,1,0),objects(op===OP.identify?identity:{})),1));
   device.identity=identity;
 }
 assert.deepEqual(completed,[OP.identify,OP.read_state]);
});
test('update waits for a write pending before a run becomes visible',async()=>{
 let resume;
 const env=new Bench({call:async()=>[]},{},()=>{});
 env.claim=()=>new Promise(done=>{resume=done;});
 const write=env.request('/api/control/claim','POST',{}, {'x-motorbench-token':env.token});
 assert.equal((await env.request('/api/desktop/update-ready','GET',{})).ready,false);
 resume();await write;
 assert.equal((await env.request('/api/desktop/update-ready','GET',{})).ready,true);
});
