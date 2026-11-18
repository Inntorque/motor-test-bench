import {createHash,verify} from 'node:crypto';
import {readFile,writeFile,mkdir,rename,rm,open} from 'node:fs/promises';
import {join} from 'node:path';
import {createReadStream} from 'node:fs';

export const validVersion = value => typeof value==='string'&&/^\d+\.\d+\.\d+$/.test(value)&&value.split('.').every(part=>Number(part)<=0xffffffff);
export function isNewer(next,current) {
  if(!validVersion(next)||!validVersion(current))return false;
  const a=next.split('.').map(Number),b=current.split('.').map(Number);
  for(let i=0;i<3;i++)if(a[i]!==b[i])return a[i]>b[i];
  return false;
}
export function verifiedRelease(envelope,key,{platform=process.platform,arch=process.arch}={}) {
  const payload=Buffer.from(envelope.payload||'','base64'),signature=Buffer.from(envelope.signature||'','base64');
  if(payload.length>1024*1024||!verify(null,payload,key,signature))throw new Error('更新清单签名无效');
  const release=JSON.parse(payload.toString('utf8'));
  if(release.schema!==1||!validVersion(release.version)||release.platform!==platform||release.arch!==arch||release.minLauncher!==1||release.protocolMajor!==1||release.dataSchema!==3||!Number.isSafeInteger(release.size)||release.size<=0||release.size>2**31||!/^[a-f0-9]{64}$/.test(release.sha256))throw new Error('更新包与本机、启动器或协议版本不兼容');
  if(new URL(release.url).protocol!=='https:')throw new Error('更新地址必须使用 HTTPS');
  return release;
}
async function get(url,{timeout=30000}={},fetcher=fetch) {
  if(new URL(url).protocol!=='https:')throw new Error('更新地址必须使用 HTTPS');
  const response=await fetcher(url,{signal:AbortSignal.timeout(timeout),headers:{'User-Agent':'InntorqueBench'}});
  if(!response.ok)throw new Error(`更新服务返回 ${response.status}`);
  if(new URL(response.url).protocol!=='https:')throw new Error('更新重定向未使用 HTTPS');
  return response;
}
export class UpdateService {
  constructor({root,version,platform=process.platform,arch=process.arch,request,fetcher=fetch}) {
    Object.assign(this,{root,version,platform,arch});this.request=request||((url,options)=>get(url,options,fetcher));this.busy=false;this.available=null;
  }
  async check() {
    const config=JSON.parse(await readFile(join(this.root,'update-source.json'),'utf8'));
    if(!config.repository||!/^[-\w.]+\/[-\w.]+$/.test(config.repository))throw new Error('未配置有效的发布仓库');
    const name=`update-${this.platform}-${this.arch}.json`;
    const url=`https://github.com/${config.repository}/releases/latest/download/${name}`;
    const response=await this.request(url);const chunks=[];let size=0;
    for await(const chunk of response.body){size+=chunk.length;if(size>1024*1024)throw new Error('更新清单过大');chunks.push(chunk);}
    const text=Buffer.concat(chunks).toString('utf8');
    const envelope=JSON.parse(text),key=await readFile(join(this.root,'release-public-key.pem'));
    const release=verifiedRelease(envelope,key,this);
    this.available=isNewer(release.version,this.version)?{release,envelope}:null;
    return this.available?.release||null;
  }
  async stage(progress=()=>{}) {
    if(this.busy)throw new Error('正在下载更新');
    if(!this.available)throw new Error('没有待更新版本');
    this.busy=true;
    const {release,envelope}=this.available,folder=join(this.root,'updates');
    const file=join(folder,release.sha256+'.zip'),partial=file+'.part';
    let handle;
    try {
      await mkdir(folder,{recursive:true});
      const response=await this.request(release.url,{timeout:15*60*1000});
      handle=await open(partial,'w',0o600);const hash=createHash('sha256');let bytes=0;
      for await(const chunk of response.body) {
        bytes+=chunk.length;if(bytes>release.size)throw new Error('更新包大小不符');hash.update(chunk);await handle.writeFile(chunk);progress(bytes/release.size);
      }
      await handle.sync();await handle.close();handle=null;
      if(bytes!==release.size||hash.digest('hex')!==release.sha256)throw new Error('更新包校验失败');
      await rm(file,{force:true});await rename(partial,file);
      await writeFile(join(folder,'downloaded.json'),JSON.stringify(envelope),{mode:0o600});
      return release;
    } finally {if(handle)await handle.close();await rm(partial,{force:true});this.busy=false;}
  }
  async staged() {
    const folder=join(this.root,'updates');
    try {
      const envelope=JSON.parse(await readFile(join(folder,'downloaded.json'),'utf8'));
      const release=verifiedRelease(envelope,await readFile(join(this.root,'release-public-key.pem')),this);
      if(!isNewer(release.version,this.version))return null;
      const hash=createHash('sha256');let bytes=0;
      for await(const chunk of createReadStream(join(folder,release.sha256+'.zip'))){
        bytes+=chunk.length;if(bytes>release.size)return null;
        hash.update(chunk);
      }
      return bytes===release.size && hash.digest('hex')===release.sha256 ? release : null;
    } catch(error){if(error.code!=='ENOENT')console.warn('已下载的更新不可用',error.message);return null;}
  }
  async requestApply() {
    const folder=join(this.root,'updates');
    if(!await this.staged())throw new Error('已下载的更新校验失败，请重新下载');
    await rename(join(folder,'downloaded.json'),join(folder,'pending.json'));
  }
}
