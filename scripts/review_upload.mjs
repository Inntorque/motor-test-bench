import {execFileSync} from 'node:child_process';
import {lstat,readFile,mkdir,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';
const root=resolve(import.meta.dirname,'..');
const paths=[...new Set(execFileSync('git',['ls-files','--cached','--others','--exclude-standard','-z'],{cwd:root,encoding:'utf8'}).split('\0').filter(Boolean))].sort();
const directories=new Set(['.github','browser','desktop','docs','profiles','protocol','scripts','tests','types','web']);
const rootFiles=new Set(['.gitattributes','.gitignore','README.md','package.json','package-lock.json','tsconfig.json','tsconfig.strict.json','tsconfig.tools.json','tsconfig.worker.json','vite.config.ts']);
const files=[],deleted=[],errors=[];
for(const path of paths){
 let stat;try{stat=await lstat(resolve(root,path));}catch(error){if(error.code==='ENOENT'){deleted.push(path);continue;}throw error;}
 if(!stat.isFile()){errors.push(`${path}: only regular source files are permitted`);continue;}
 if(!(path.includes('/')?directories.has(path.split('/')[0]):rootFiles.has(path))||/(^|\/)(node_modules|artifacts|site|dist|\.runtime|测功数据|历史测功数据|config|logs|updates)(\/|$)/.test(path)||/\.(exe|dll|so|zip|csv|jsonl|key)$|private\.pem$|(^|\/)\.env($|\.)/i.test(path))errors.push(`${path}: forbidden upload path`);
 if(stat.size>2*1024*1024)errors.push(`${path}: source exceeds 2 MiB`);
 const data=await readFile(resolve(root,path));
 if(/-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----/.test(data.toString('utf8')))errors.push(`${path}: private signing material`);
 files.push({path,size:stat.size,sha256:createHash('sha256').update(data).digest('hex')});
}
const report={repository:'Inntorque/motor-test-bench',version:JSON.parse(await readFile(resolve(root,'package.json'),'utf8')).version,createdAt:new Date().toISOString(),uploaded:false,files,deleted,errors};
await mkdir(resolve(root,'.runtime'),{recursive:true});await writeFile(resolve(root,'.runtime/upload-review.json'),JSON.stringify(report,null,2)+'\n');
console.log(`${files.length} source files, ${files.reduce((n,f)=>n+f.size,0)} bytes; ${deleted.length} previously tracked deletions; ${errors.length} upload exclusions violated. No upload performed.`);
if(errors.length){console.error(errors.join('\n'));process.exitCode=1;}
