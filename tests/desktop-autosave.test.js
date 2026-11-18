import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readFile, readdir, rm, mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {DataArchive} from '../desktop/archive.mjs';
import {Recordings, DesktopRecordStore} from '../browser/storage.ts';

async function fixture(t) {
  const directory = await mkdtemp(resolve(tmpdir(),'bench-autosave-'));
  t.after(()=>rm(directory,{recursive:true,force:true}));
  const archive = new DataArchive(directory);
  let subject = {motor:null,driverBoard:null,testSetup:null};
  const recording = new Recordings(new DesktopRecordStore({call:(_method,args)=>archive.write(args.archive)}),()=>({}),()=>subject,true);
  t.after(()=>clearInterval(recording.timer));
  await recording.start({name:'自动采集'});
  const folder = resolve(directory,(await readdir(directory))[0]);
  return {recording,archive,folder,setSubject:value=>{subject=value;}};
}

test('automatic saving starts without devices and preserves every frame and current subject in native files',async t=>{
  const {recording,folder,setSubject} = await fixture(t);
  setSubject({motor:{name:'M,1'},driverBoard:{name:'D1'},testSetup:{id:'fixture',name:'裸机',ratio:1}});
  const frames=Array.from({length:1000},(_,i)=>[i+1,100+i/1000,i,200]);
  recording.observe('sensor_frames',{config:{torqueDecimals:2,speedDecimals:1},frames});
  await recording.flush();
  const rows=(await readFile(resolve(folder,'timeline.jsonl'),'utf8')).trim().split('\n').map(JSON.parse);
  const sensorRows=rows.filter(row=>row.source==='sensor_frames');
  assert.equal(sensorRows.length,1000);
  assert.equal(sensorRows[999].sensor_sequence,1000);
  assert.equal(sensorRows[999].torque_Nm,9.99);
  assert.equal(sensorRows[0].motor_name,'M,1');
  assert.match(await readFile(resolve(folder,'timeline.csv'),'utf8'),/"M,1"/);
  recording.observe('operation_request',{path:'/fixture/stop'});
  await recording.stop(null);
  const report=JSON.parse(await readFile(resolve(folder,'report.json'),'utf8'));
  assert.equal(report.state,'complete'); assert.equal(report.running,false);
  assert.equal(report.counts.sensor_frames,1000);assert.equal(report.files.csv,'timeline.csv');
  assert.match(await readFile(resolve(folder,'timeline.jsonl'),'utf8'),/fixture\/stop/);
});

test('failed native append retains the data and retries without duplicate rows',async t=>{
  const {recording,folder} = await fixture(t);
  const jsonl=resolve(folder,'timeline.jsonl');
  await mkdir(jsonl);
  await assert.rejects(recording.flush());
  assert.equal(recording.buffer.length,1);
  assert.equal(await readFile(resolve(folder,'timeline.csv'),'utf8'),'');
  await rm(jsonl,{recursive:true});
  await recording.flush();
  assert.equal(recording.buffer.length,0);
  assert.equal((await readFile(jsonl,'utf8')).trim().split('\n').length,1);
});

test('archive rejects paths supplied in a recording identifier',async t=>{
  const {archive} = await fixture(t);
  await assert.rejects(archive.write({id:'../../outside',report:{}}),/标识无效/);
});

test('an unsuccessful final save keeps automatic acquisition active for another close attempt',async t=>{
  const {recording,folder} = await fixture(t);
  const jsonl=resolve(folder,'timeline.jsonl');await mkdir(jsonl);
  await assert.rejects(recording.stop(null));
  assert.equal(recording.live.running,true);
  recording.observe('operation_request',{path:'/after-failure'});
  await rm(jsonl,{recursive:true});await recording.stop(null);
  assert.match(await readFile(jsonl,'utf8'),/after-failure/);
});

test('aligned measurement CSV is written with the raw files and an append failure rolls all files back', async t=>{
  const {recording,folder}=await fixture(t);
  recording.source=()=>({'torque-sensor':{connected:true,snapshot:{receivedAt:Date.now()/1000,torque:-2,speed:600}}});
  recording.capture();
  const file=resolve(folder,'measurements.csv');await mkdir(file);
  await assert.rejects(recording.flush());
  assert.equal(await readFile(resolve(folder,'timeline.csv'),'utf8'),'');
  assert.equal(await readFile(resolve(folder,'timeline.jsonl'),'utf8'),'');
  await rm(file,{recursive:true});await recording.flush();
  const lines=(await readFile(file,'utf8')).trim().split('\r\n');
  assert.equal(lines.length,2);assert.match(lines[0],/sensor_age_s/);assert.match(lines[0],/motor_supply_actual_power_W/);
  assert.equal((await readFile(resolve(folder,'timeline.jsonl'),'utf8')).trim().split('\n').map(JSON.parse).filter(r=>r.source==='measurement').length,1);
});


test('characteristic table contains only accepted stage points and is saved with the raw evidence',async t=>{
  const {recording,folder}=await fixture(t);
  recording.observe('sensor_frames',{config:{torqueDecimals:2,speedDecimals:1},frames:[[1,100,99,500]]});
  recording.observe('characteristic_point',{runId:'sweep',stage:0,kind:'stage',torque:0,measuredTorque:.1,speed:100,power:1,voltage:24,current:1,inputPower:24,efficiency:100/24,startedAt:100,receivedAt:101,sampleCount:10,electricalCount:10,toleranceNm:.03});
  await recording.stop(null);
  const csv=(await readFile(resolve(folder,'characteristics.csv'),'utf8')).trim().split('\r\n');
  assert.equal(csv.length,2);assert.match(csv[0],/torque,speed/);assert.doesNotMatch(csv[0],/measuredTorque/);assert.match(csv[1],/sweep/);
  const report=JSON.parse(await readFile(resolve(folder,'report.json'),'utf8'));assert.equal(report.characteristicPoints.length,1);assert.equal(report.files.characteristics,'characteristics.csv');
  assert.match(await readFile(resolve(folder,'timeline.jsonl'),'utf8'),/sensor_frames/);
});
