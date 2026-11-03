import test from 'node:test';
import assert from 'node:assert/strict';
import {motorOutputFeedback,toCsv} from '../web/src/model.ts';
import {subjectText,setupText} from '../web/src/testSubjects.ts';

test('bare and geared configurations are explicit and old records are never reinterpreted',()=>{
  const subject={motor:{name:'A'},driverBoard:{name:'B'},testSetup:{id:'bare',name:'裸机',ratio:1,axis:'motor',hasGearbox:false,massKg:.5,volumeL:null}};
  assert.match(subjectText(subject),/裸机.*1:1.*电机轴/);
  assert.match(subjectText({motor:{name:'old'}}),/测试配置未记录/);
  assert.equal(setupText(null),'测试配置未选择');
  assert.match(setupText({...subject.testSetup,ratioConfirmed:false}),/待确认/);
  const csv=toCsv([{t:0}],subject);
  assert.match(csv,/test_setup_id,test_setup_name,gearbox_name,has_gearbox,reduction_ratio,measurement_axis/);
  assert.match(csv,/A,B,bare,裸机,,false,1,motor,0.5,/);
  assert.doesNotMatch(toCsv([{t:0}],{motor:{name:'old'}}),/test_setup_id/);
});

test('selected ratios 1, 9.67 and 100 replace the old device ratio without scaling sensor values',()=>{
  for(const ratio of [1,9.67,100]){
    const feedback=motorOutputFeedback({connected:true,testSubject:{testSetup:{ratio}},snapshot:{motorProfile:{nameplate:{reduction_ratio:500}},feedback:{position_stale:false,stale:false,position_deg:360,speed_rpm:600}}});
    assert.equal(feedback.positionDeg,360/ratio);assert.equal(feedback.speedRpm,600/ratio);
    const csv=toCsv([{t:0,torque:2,speed:3}],{testSetup:{ratio}});
    assert.match(csv,/,0,2,3,/);
  }
});
