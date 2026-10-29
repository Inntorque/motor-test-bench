import test from 'node:test';
import assert from 'node:assert/strict';
import {referencesCsv,referenceAnalysis,loadAnalysis,saveAnalysis} from '../web/src/specialized/data.js';

test('external reference import rejects empty, invalid and duplicated visits',()=>{
  assert.deepEqual(referencesCsv('\uFEFFvisit_id,target_deg,reference_deg\r\n1,0,0.1\r\n2,0,0.2'),[{visitId:1,referenceDeg:.1},{visitId:2,referenceDeg:.2}]);
  for(const text of ['id,angle\n1,0','visit_id,reference_deg\n1,','visit_id,reference_deg\n1,0\n1,1'])assert.throws(()=>referencesCsv(text));
});

test('offline position analysis preserves raw record and keeps directional statistics',()=>{
  const report={id:'test',positionMetrics:{feedbackMaxAbsMeanErrorDeg:.001},positionVisits:[
    {visitId:1,direction:'forward',nominalDeg:10},{visitId:2,direction:'forward',nominalDeg:10},
    {visitId:3,direction:'reverse',nominalDeg:10},{visitId:4,direction:'reverse',nominalDeg:10}]};
  const rows=[30.1,30.12,29.9,29.92].map((referenceDeg,i)=>({visitId:i+1,referenceDeg}));
  const analysis=referenceAnalysis(report,rows,{name:'encoder',zeroDeg:20,aligned:true});
  assert.equal(report.referenceData,undefined);assert.equal(analysis.positionMetrics.externalCoverageComplete,true);
  assert.equal(analysis.positionMetrics.claim,'external_direct_measurement');
  assert.equal(referenceAnalysis(report,rows.slice(0,1),{name:'encoder',zeroDeg:20,aligned:true}).positionMetrics.claim,'feedback_only_or_partial_reference');
  assert.ok(Math.abs(analysis.positionMetrics.externalWorstRepeatRangeDeg-.02)<1e-9);
  assert.ok(Math.abs(analysis.positionMetrics.externalReversalDifferenceDeg-.2)<1e-9);
  const map=new Map(),storage={getItem:key=>map.get(key),setItem:(key,value)=>map.set(key,value)};
  saveAnalysis(report,analysis,storage);assert.equal(loadAnalysis(report,storage).referenceInstrument.name,'encoder');
  assert.throws(()=>referenceAnalysis(report,[{visitId:99,referenceDeg:0}],{name:'encoder',zeroDeg:0}));
});
