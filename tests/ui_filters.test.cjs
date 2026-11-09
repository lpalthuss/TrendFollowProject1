const {test}=require('node:test');
const assert=require('node:assert/strict');
const {filterRows}=require('../trendfollow/web/app.js');
const defaults={industry:'all',rs:'all',action:'all',setup:'all',signal:'all',volume:'all',search:''};
const rules={entry_method:'setup',volume_multiple:1.5};
const rows=[
 {symbol:'000001',name:'후보 A',sector:'A',industry_name:'반도체',rs_pass:true,sector_action:true,setup_phase:'base',setup_ready:true,signal:false,volume_ratio:1.5},
 {symbol:'000002',name:'후보 B',sector:'A',industry_name:'반도체',rs_pass:true,sector_action:false,setup_phase:'base',setup_ready:false,signal:false,volume_ratio:1.49},
 {symbol:'000003',name:'후보 C',sector:'B',industry_name:'기계',rs_pass:false,sector_action:true,setup_phase:'rising',setup_ready:false,setup_trigger:true,signal:true,volume_ratio:2},
 {symbol:'000004',sector:'B',rs_pass:null,sector_action:null,signal:null,volume_ratio:null}
];
const select=(filters={},config=rules)=>filterRows(rows,{...defaults,...filters},config).map(r=>r.symbol);
test('industry, RS, action, setup, volume and search compose with AND',()=>{
 assert.deepEqual(select({industry:'A',rs:'pass',action:'pass',setup:'ready',signal:'fail',volume:'pass',search:'반도체'}),['000001']);
 assert.deepEqual(select({industry:'A',signal:'pass'}),[]);
 assert.deepEqual(select({industry:'B',search:'후보 C',setup:'trigger'}),['000003']);
});
test('unknown is separate from failed and unapplied industry action',()=>{
 assert.deepEqual(select({rs:'unknown'}),['000004']);
 assert.deepEqual(select({rs:'fail'}),['000003']);
 assert.deepEqual(select({action:'unknown'}),['000004']);
 assert.deepEqual(select({action:'unapplied'}),[]);
 assert.deepEqual(select({action:'unapplied'},{...rules,entry_method:'breakout'}),rows.map(r=>r.symbol));
 assert.deepEqual(select({action:'unknown'},{...rules,entry_method:'breakout'}),[]);
});
test('setup phase is distinct from readiness and trigger flags',()=>{
 assert.deepEqual(select({setup:'base'}),['000001','000002']);
 assert.deepEqual(select({setup:'ready'}),['000001']);
 assert.deepEqual(select({setup:'not_ready'}),['000002','000003']);
 assert.deepEqual(select({setup:'unknown'}),['000004']);
});
test('volume uses configured threshold inclusively and does not coerce null to zero',()=>{
 assert.deepEqual(select({volume:'pass'}),['000001','000003']);
 assert.deepEqual(select({volume:'fail'}),['000002']);
 assert.deepEqual(select({volume:'unknown'}),['000004']);
 assert.deepEqual(select({volume:'pass'},{...rules,volume_multiple:2}),['000003']);
});
test('all conditions cleared returns every row without mutating inputs',()=>{
 const before=JSON.stringify(rows);assert.deepEqual(select(),rows.map(r=>r.symbol));assert.equal(JSON.stringify(rows),before);
});
