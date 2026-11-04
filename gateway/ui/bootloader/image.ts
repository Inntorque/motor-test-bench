import type {BootManifest,DataBytes} from './types.ts';
import {BASE,BOOT_SIZE,FLASH_SIZE,PAGE,sha256,validateImage} from './n32.ts';

export function parseHex(text: string): DataBytes {
  const image = new Uint8Array(BOOT_SIZE).fill(255),written = new Uint8Array(BOOT_SIZE);
  let upper = 0,end = 0,eof = false;
  for (const [index,raw] of text.trim().split(/\r?\n/).entries()) {
    const line = raw.trim();if (!line) continue;
    if (eof) throw new Error('HEX 结束记录后仍有内容');
    if (!/^:(?:[0-9a-f]{2}){5,260}$/i.test(line)) throw new Error(`HEX 第 ${index + 1} 行格式无效`);
    const record = Uint8Array.from(line.slice(1).match(/../g)!,value => parseInt(value,16));
    const [length,high,low,type] = record,address = high * 256 + low,data = record.subarray(4,-1);
    if (record.length !== length + 5 || record.reduce((sum,value) => sum + value,0) % 256)
      throw new Error(`HEX 第 ${index + 1} 行长度或校验和错误`);
    if (type === 0) {
      const start = upper + address - BASE;
      if (start < 0 || start + length > BOOT_SIZE) throw new Error('HEX 地址超出 32 KiB Boot 分区');
      for (let i = 0; i < length; i++) {
        const offset = start + i;
        if (written[offset] && image[offset] !== data[i]) throw new Error('HEX 包含冲突的重叠数据');
        image[offset] = data[i];written[offset] = 1;
      }
      end = Math.max(end,start + length);
    } else if (type === 1 && length === 0 && address === 0) eof = true;
    else if ((type === 2 || type === 4) && length === 2 && address === 0)
      upper = (data[0] * 256 + data[1]) * (type === 4 ? 65536 : 16);
    else if ((type === 3 || type === 5) && length === 4 && address === 0) continue;
    else throw new Error(`HEX 第 ${index + 1} 行记录类型或长度无效`);
  }
  if (!eof || written.subarray(0,8).some(value => !value)) throw new Error('HEX 缺少结束记录或 Boot 起始向量');
  return image.slice(0,Math.ceil(end / 4) * 4);
}

export async function externalImage(name: string,file: DataBytes): Promise<{image: DataBytes; manifest: BootManifest}> {
  const format = name.split('.').at(-1)?.toLowerCase();
  if (format !== 'bin' && format !== 'hex') throw new Error('请选择 BIN 或 HEX 镜像文件');
  if (file.length > 1024 * 1024) throw new Error('镜像文件过大；Boot 分区仅 32 KiB');
  let image: DataBytes;
  if (format === 'hex') image = parseHex(new TextDecoder().decode(file));
  else {
    if (file.length < 256 || file.length > BOOT_SIZE) throw new Error('Bootloader 镜像长度无效');
    image = new Uint8Array(Math.ceil(file.length / 4) * 4).fill(255);image.set(file);
  }
  if (image.length < 256) throw new Error('Bootloader 镜像长度无效');
  const fileSha256 = await sha256(file);
  const manifest: BootManifest = {
    schema:1,target:'N32G455REL7',base:BASE,bootSize:BOOT_SIZE,flashSize:FLASH_SIZE,pageSize:PAGE,
    stack:new DataView(image.buffer).getUint32(0,true),bytes:image.length,sha256:await sha256(image),release:'外部文件',
    source:{name,format,sha256:fileSha256},...(format === 'hex' ? {hexSha256:fileSha256} : {}),
  };
  await validateImage(image,manifest);return {image,manifest};
}
