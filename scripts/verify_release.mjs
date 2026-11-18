import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash,verify} from 'node:crypto';
import {join,resolve} from 'node:path';
import {verifiedRelease} from '../desktop/update-service.mjs';
const root=resolve(import.meta.dirname,'..');
const {version}=JSON.parse(await readFile(join(root,'desktop/package.json'),'utf8'));
const {sdk}=JSON.parse(await readFile(join(root,'protocol/schema.json'),'utf8'));
const dir=resolve(process.argv[2]||join(root,'artifacts/releases','v'+version));
const key=await readFile(join(root,'desktop/release-public-key.pem'));
const json=async name=>JSON.parse(await readFile(join(dir,name),'utf8'));
for(const platform of ['linux','win32']){
  const arch='x64';
  const envelope=await json(`artifacts-${platform}-${arch}.json`),payload=Buffer.from(envelope.payload,'base64');
  assert.ok(verify(null,payload,key,Buffer.from(envelope.signature,'base64')));
  const index=JSON.parse(payload);
  assert.deepEqual([index.version,index.platform,index.arch],[version,platform,arch]);
  const release=verifiedRelease(await json(`update-${platform}-${arch}.json`),key,{platform,arch});
  assert.equal(release.version,version);
  const updateName=`update-${platform}-${arch}-${version}.zip`;
  const names=[`InntorqueBench-${platform}-${arch}-${version}-portable.zip`,updateName];
  if(platform==='linux')names.push(`Inntorque-MBP-SDK-${sdk}.zip`);
  assert.deepEqual(index.files.map(f=>f.name).sort(),names.sort());
  for(const file of index.files){
    const bytes=await readFile(join(dir,file.name));
    assert.equal(bytes.length,file.size,file.name);
    assert.equal(createHash('sha256').update(bytes).digest('hex'),file.sha256,file.name);
    if(file.name===updateName){
      assert.equal(release.sha256,file.sha256);assert.equal(release.size,file.size);
      assert.equal(new URL(release.url).pathname.split('/').at(-1),updateName);
    }
  }
}
console.log(`Verified signed Windows, Linux and SDK release artifacts for ${version}.`);
