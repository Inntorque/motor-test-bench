import test from 'node:test';
import assert from 'node:assert/strict';
import {generateKeyPairSync,sign,createHash} from 'node:crypto';
import {mkdtemp,writeFile,readFile,rm,access} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {UpdateService,verifiedRelease,isNewer} from '../desktop/update-service.mjs';
const {privateKey,publicKey}=generateKeyPairSync('ed25519');
const key=publicKey.export({type:'spki',format:'pem'});
const signed=release=>{const bytes=Buffer.from(JSON.stringify(release));return {payload:bytes.toString('base64'),signature:sign(null,bytes,privateKey).toString('base64')};};
const bundle=Buffer.from('fixture package');
const descriptor=(changes={})=>({schema:1,version:'1.2.0',platform:process.platform,arch:process.arch,minLauncher:1,protocolMajor:1,dataSchema:3,size:bundle.length,sha256:createHash('sha256').update(bundle).digest('hex'),url:'https://example.invalid/update.zip',...changes});
test('only signed compatible newer releases are accepted',()=>{
  assert.equal(verifiedRelease(signed(descriptor()),key).version,'1.2.0');
  for(const change of [{platform:'unknown'},{arch:'arm64'},{minLauncher:2},{minLauncher:undefined},{minLauncher:0},{protocolMajor:2},{dataSchema:4},{version:'../../bad'},{url:'http://bad'},{size:-1}])assert.throws(()=>verifiedRelease(signed(descriptor(change)),key));
  const bad=signed(descriptor());bad.payload=Buffer.from(JSON.stringify(descriptor({version:'9.0.0'}))).toString('base64');assert.throws(()=>verifiedRelease(bad,key),/签名/);
  assert.equal(isNewer('1.10.0','1.9.0'),true);assert.equal(isNewer('1.0.0','1.0.0'),false);assert.equal(isNewer('0.9.9','1.0.0'),false);assert.equal(isNewer('999999999999.0.0','1.0.0'),false);
});
async function fixture(t,{bytes=bundle,release=descriptor()}={}){
  const root=await mkdtemp(join(tmpdir(),'bench-update-'));t.after(()=>rm(root,{recursive:true,force:true}));
  await writeFile(join(root,'release-public-key.pem'),key);await writeFile(join(root,'update-source.json'),JSON.stringify({repository:'Inntorque/motor-test-bench'}));
  const envelope=signed(release),requests=[];
  const service=new UpdateService({root,version:'1.1.0',request:async url=>{requests.push(url);return new Response(url.endsWith('.json')?JSON.stringify(envelope):bytes);}});
  return {root,service,requests,envelope};
}
test('download is verified before a separate explicit apply request; data and config remain untouched',async t=>{
  const {root,service,requests,envelope}=await fixture(t);
  await writeFile(join(root,'operator-data.txt'),'preserve');
  assert.equal((await service.check()).version,'1.2.0');await service.stage();
  await assert.rejects(access(join(root,'updates/pending.json')));
  assert.deepEqual(JSON.parse(await readFile(join(root,'updates/downloaded.json'),'utf8')),envelope);
  const offline=new UpdateService({root,version:'1.1.0',request:async()=>{throw Error('offline');}});
  assert.equal((await offline.staged()).version,'1.2.0');
  await offline.requestApply();assert.equal(await offline.staged(),null);assert.deepEqual(JSON.parse(await readFile(join(root,'updates/pending.json'),'utf8')),envelope);
  assert.equal(await readFile(join(root,'operator-data.txt'),'utf8'),'preserve');
  assert.match(requests[0],/^https:\/\/github.com\/Inntorque\/motor-test-bench\/releases\/latest\/download\/update-/);
});
test('truncated or tampered downloads do not schedule an update',async t=>{
  for(const bytes of [bundle.subarray(0,4),Buffer.from('fixture packagX'),Buffer.alloc(100)]){
    const {root,service}=await fixture(t,{bytes});await service.check();await assert.rejects(service.stage(),/校验|大小/);
    assert.equal(service.busy,false);await assert.rejects(access(join(root,'updates/downloaded.json')));await assert.rejects(access(join(root,'updates/pending.json')));
  }
});
test('older release is not staged and cannot overwrite the current version',async t=>{
  const {service}=await fixture(t,{release:descriptor({version:'1.0.0'})});assert.equal(await service.check(),null);await assert.rejects(service.stage(),/没有待更新/);
});
