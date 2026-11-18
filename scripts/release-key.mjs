import {generateKeyPairSync} from 'node:crypto';
import {mkdir,writeFile,rm} from 'node:fs/promises';
import {dirname,resolve,relative,isAbsolute,sep} from 'node:path';
import {existsSync} from 'node:fs';
const target=process.argv[2];if(!target)throw new Error('指定仓库外的私钥保存路径');
const path=resolve(target),root=resolve(import.meta.dirname,'..');
const rel=relative(root,path);
if(!rel||(!rel.startsWith('..'+sep)&&!isAbsolute(rel)))throw new Error('私钥必须保存在仓库外');
const publicPath=resolve(root,'desktop/release-public-key.pem');
if(existsSync(publicPath))throw new Error('已有发布公钥；重新生成会使现有客户端无法验证更新');
await mkdir(dirname(path),{recursive:true,mode:0o700});
const {privateKey,publicKey}=generateKeyPairSync('ed25519');
await writeFile(path,privateKey.export({type:'pkcs8',format:'pem'}),{mode:0o600,flag:'wx'});
try {await writeFile(publicPath,publicKey.export({type:'spki',format:'pem'}),{flag:'wx'});}
catch(error){await rm(path);throw error;}
console.log('已生成仓库外发布私钥及可公开的验证公钥；未上传。');
