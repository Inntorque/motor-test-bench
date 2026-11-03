import test from 'node:test';
import assert from 'node:assert/strict';
import {FrameBuffer} from '../web/src/frameBuffer.ts';
import {EncoderBins} from '../web/src/encoderBins.ts';

function referenceRows(points,limit) {
  const groups=new Map();
  for(const point of points) {
    if(!Number.isFinite(point.torque)||point.torque>limit)continue;
    const key=Math.floor(point.torque/Math.max(.001,limit/80));
    if(!groups.has(key))groups.set(key,[]);groups.get(key).push(point);
  }
  return [...groups.values()].map(group=>{
    const mean=key=>{const values=group.map(p=>p[key]).filter(Number.isFinite);return values.length?values.reduce((a,b)=>a+b,0)/values.length:null;};
    return {torque:mean('torque'),temp:mean('temp'),sensorRpm:mean('sensorRpm'),encoderRpm:mean('encoderRpm')};
  }).sort((a,b)=>a.torque-b.torque);
}

test('frame ring preserves all retained sequences, gaps, reset and independent returned rows',()=>{
  const buffer=new FrameBuffer(16000);let reference=[],sequence=0;
  for(let batch=0;batch<1600;batch++) {
    const frames=Array.from({length:20},()=>{sequence+=sequence%97===0?2:1;return [sequence,sequence/1000,sequence%11,sequence%17];});
    buffer.append(frames);reference=reference.concat(frames).slice(-16000);
    if(batch%103===0)for(const since of [0,sequence-30,sequence,sequence+1])assert.deepEqual(buffer.after(since),reference.filter(row=>row[0]>since));
  }
  assert.equal(buffer.length,16000);assert.deepEqual(buffer.at(0),reference[0]);
  const copy=buffer.after(sequence-1);copy[0][2]=999;assert.notEqual(buffer.after(sequence-1)[0][2],999);
  buffer.clear();assert.deepEqual(buffer.after(0),[]);buffer.append([[1,0,2,3]]);assert.deepEqual(buffer.after(0),[[1,0,2,3]]);
});

test('dirty torque bins match chronological batch sums exactly through expiration and axis changes',()=>{
  const bins=new EncoderBins(5,73);let reference=[];
  for(let i=0;i<1700;i++) {
    const point={torque:i%43===0?NaN:(i%113)/20,temp:i%9===0?null:20+i%7*.1,sensorRpm:i%13===0?null:100.1-i%11,encoderRpm:i%5===0?null:99+i%8*.3};
    reference=[...reference,point].slice(-73);bins.append(point);
    if(i%101===0)bins.setLimit(i%202===0?.025:5);
    assert.deepEqual(bins.rows(),referenceRows(reference,bins.limit));
    assert.equal(bins.clipped>0,reference.some(p=>Number.isFinite(p.torque)&&p.torque>bins.limit));
  }
});

test('constant torque and returning to a previous axis retain the existing averaging results',()=>{
  const bins=new EncoderBins(5,12000),points=Array.from({length:14000},(_,i)=>({torque:1,temp:25,sensorRpm:i%2?99.9:100.1,encoderRpm:100}));
  for(const point of points)bins.append(point);
  assert.deepEqual(bins.rows(),referenceRows(points.slice(-12000),5));
  bins.setLimit(.5);assert.deepEqual(bins.rows(),[]);assert.equal(bins.clipped,12000);
  bins.setLimit(5);assert.deepEqual(bins.rows(),referenceRows(points.slice(-12000),5));
});
