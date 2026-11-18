import {cp,mkdir,readFile,rename,rm,writeFile,chmod} from 'node:fs/promises';
import {dirname,resolve} from 'node:path';
import {createRequire} from 'node:module';
const root=import.meta.dirname,require=createRequire(import.meta.url);
const platform=process.env.BENCH_PLATFORM||process.platform,arch=process.env.BENCH_ARCH||process.arch;
if(!['linux','win32'].includes(platform)||arch!=='x64')throw new Error('支持 Windows x64 与 Ubuntu x64');
const destination=resolve(root,`../artifacts/InntorqueBench-${platform}-${arch}`),resources=resolve(destination,'resources/app');
await readFile(resolve(root,'../site/apps/dynamometer/index.html'));
await rm(destination,{recursive:true,force:true});await mkdir(destination,{recursive:true});
if(platform===process.platform&&arch===process.arch)await cp(dirname(require('electron')),destination,{recursive:true});
else {
  const {downloadArtifact}=await import('@electron/get');
  const {extract}=await import('@electron-internal/extract-zip');
  const version=JSON.parse(await readFile(resolve(root,'package.json'),'utf8')).devDependencies.electron;
  const zip=await downloadArtifact({version,artifactName:'electron',platform,arch,checksums:require('electron/checksums.json')});
  await extract(zip,{dir:destination});
}
await mkdir(resources,{recursive:true});
for(const file of ['main.mjs','archive.mjs','update-service.mjs','preload.cjs','icon.png','icon.svg','package.json'])await cp(resolve(root,file),resolve(resources,file));
for(const folder of ['apps/dynamometer','browser','export'])await cp(resolve(root,'../site',folder),resolve(resources,'site',folder),{recursive:true});
await rename(resolve(destination,platform==='win32'?'electron.exe':'electron'),resolve(destination,platform==='win32'?'InntorqueBench.exe':'inntorque-bench'));
if(platform==='linux'){
  const launcher=resolve(destination,'启动测功机.sh');
  await writeFile(launcher,'#!/bin/sh\nset -eu\nAPP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec "$APP_DIR/inntorque-bench" "$@"\n');await chmod(launcher,0o755);
}
await cp(resolve(root,'../README.md'),resolve(destination,'使用说明.md'));
console.log(destination);
