import {cp,mkdir,readFile,rm,writeFile,chmod} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {createHash,createPublicKey,sign} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root=resolve(import.meta.dirname,'..');
const platform=process.env.BENCH_PLATFORM||process.platform,arch='x64';
if(!['linux','win32'].includes(platform))throw new Error('unsupported release platform');
const pkg=JSON.parse(await readFile(join(root,'desktop/package.json'),'utf8')),version=pkg.version;
if(!/^\d+\.\d+\.\d+$/.test(version))throw new Error('发布版本应为 x.y.z');
const go=process.env.GO_BINARY||'go';
const keyFile=process.env.INNTORQUE_SIGNING_KEY_FILE;if(!keyFile)throw new Error('必须指定仓库外的 INNTORQUE_SIGNING_KEY_FILE');
const key=await readFile(keyFile),publicKey=await readFile(join(root,'desktop/release-public-key.pem'));
if(createPublicKey(key).export({type:'spki',format:'pem'}).toString()!==publicKey.toString())throw new Error('发布私钥与受信公钥不匹配');
const output=join(root,'artifacts/releases',`v${version}`);await mkdir(output,{recursive:true});
const name=`InntorqueBench-${platform}-${arch}`,portable=join(output,name);
await rm(portable,{recursive:true,force:true});await mkdir(join(portable,'versions'),{recursive:true});
execFileSync(process.execPath,[join(root,'desktop/package.mjs')],{cwd:root,stdio:'inherit',env:{...process.env,BENCH_PLATFORM:platform}});
await cp(join(root,'artifacts',name),join(portable,'versions',version),{recursive:true});
const launcher=join(portable,platform==='win32'?'测功机.exe':'测功机');
execFileSync(go,['build','-trimpath','-ldflags',platform==='win32'?'-s -w -H windowsgui':'-s -w','-o',launcher,'.'],{cwd:join(root,'desktop/launcher'),stdio:'inherit',env:{...process.env,CGO_ENABLED:'0',GOOS:platform==='win32'?'windows':'linux',GOARCH:'amd64'}});
await writeFile(join(portable,'active.json'),JSON.stringify({version}));
await cp(join(root,'desktop/release-public-key.pem'),join(portable,'release-public-key.pem'));
await cp(join(root,'desktop/update-source.json'),join(portable,'update-source.json'));
await cp(join(root,'docs/portable-deployment.md'),join(portable,'使用说明.md'));
if(platform==='linux'){
  await cp(join(root,'desktop/setup-ubuntu.sh'),join(portable,'首次配置Ubuntu.sh'));await chmod(join(portable,'首次配置Ubuntu.sh'),0o755);
  await writeFile(join(portable,'启动测功机.sh'),'#!/bin/sh\nset -eu\nROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec "$ROOT/测功机" "$@"\n');await chmod(join(portable,'启动测功机.sh'),0o755);
}
const updateName=`update-${platform}-${arch}-${version}.zip`,update=join(output,updateName);
const pack=(source,file)=>execFileSync(go,['run','./cmd/pack',source,file],{cwd:join(root,'desktop/launcher'),stdio:'inherit'});
pack(join(portable,'versions',version),update);
const bytes=await readFile(update),source=JSON.parse(await readFile(join(root,'desktop/update-source.json'),'utf8'));
const release={schema:1,version,platform,arch,minLauncher:1,protocolMajor:1,dataSchema:3,size:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),url:`https://github.com/${source.repository}/releases/download/v${version}/${updateName}`,notes:await readFile(join(root,'docs/releases',`${version}.md`),'utf8')};
const payload=Buffer.from(JSON.stringify(release));
await writeFile(join(output,`update-${platform}-${arch}.json`),JSON.stringify({payload:payload.toString('base64'),signature:sign(null,payload,key).toString('base64')},null,2)+'\n');
const portableName=`${name}-${version}-portable.zip`;
pack(portable,join(output,portableName));
const assets=[portableName,updateName];
if(platform==='linux'){
  const {sdk}=JSON.parse(await readFile(join(root,'protocol/schema.json'),'utf8'));
  const sdkName=`Inntorque-MBP-SDK-${sdk}.zip`;
  pack(join(root,'protocol'),join(output,sdkName));assets.push(sdkName);
}
const files=[];
for(const name of assets){
  const content=await readFile(join(output,name));
  files.push({name,size:content.length,sha256:createHash('sha256').update(content).digest('hex')});
}
const index=Buffer.from(JSON.stringify({version,platform,arch,files}));
await writeFile(join(output,`artifacts-${platform}-${arch}.json`),JSON.stringify({payload:index.toString('base64'),signature:sign(null,index,key).toString('base64')},null,2)+'\n');
console.log(`本地发布包已准备：${output}；没有上传或发布。`);
