import {sleep} from './jlink.js';
export const BASE = 0x08000000,BOOT_SIZE = 0x8000,FLASH_SIZE = 0x80000,PAGE = 0x800;
const KEY = 0x40022004,STATUS = 0x4002200c,CONTROL = 0x40022010,ADDRESS = 0x40022014;
export const hex = value => '0x' + value.toString(16).padStart(8,'0').toUpperCase();
export async function sha256(data) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256',data))].map(value => value.toString(16).padStart(2,'0')).join('');
}
export function identity(cpuid,device) {
  const model = ((device >>> 12) & 15) << 8 | ((device >>> 8) & 15) << 4 | ((device >>> 20) & 15);
  const flashKiB = ((device >>> 16) & 15) * 64,sramKiB = (((device >>> 28) & 15) + 1) * 16;
  if (((cpuid >>> 4) & 0xfff) !== 0xc24 || model !== 0x455 || flashKiB !== 512 || sramKiB !== 144)
    throw new Error(`芯片型号/容量不符：CPUID=${hex(cpuid)}，DBG_ID=${hex(device)}；要求 N32G455 / 512 KiB / 144 KiB`);
  return {model:'N32G455',cpuid:hex(cpuid),dbgId:hex(device),flashKiB,sramKiB};
}
export async function validateImage(image,manifest) {
  if (manifest.schema !== 1 || manifest.target !== 'N32G455REL7' || manifest.base !== BASE || manifest.bootSize !== BOOT_SIZE ||
      manifest.flashSize !== FLASH_SIZE || manifest.pageSize !== PAGE || manifest.stack !== 0x20024000)
    throw new Error('发布清单的目标芯片或分区不符');
  if (image.length < 256 || image.length > BOOT_SIZE || image.length !== manifest.bytes || image.length % 4)
    throw new Error('Bootloader 镜像长度无效');
  const vectors = new DataView(image.buffer,image.byteOffset,image.byteLength),reset = vectors.getUint32(4,true);
  if (vectors.getUint32(0,true) !== 0x20024000 || !(reset & 1) || reset < BASE || reset >= BASE + image.length)
    throw new Error('Bootloader 栈顶或复位向量无效');
  if (await sha256(image) !== manifest.sha256) throw new Error('Bootloader SHA-256 不符；请重新加载网页');
}
export function replacement(before,image) {
  if (before.length !== FLASH_SIZE) throw new Error('512 KiB 全片备份不完整，不能擦写');
  if (!image.length || image.length > BOOT_SIZE) throw new Error('Bootloader 镜像越界');
  const expected = before.slice();expected.set(image);
  return {expected,pages:Array.from({length:Math.ceil(image.length / PAGE)},(_,i) => BASE + i * PAGE)};
}
export async function identify(core) {return identity(await core.read32(0xe000ed00),await core.read32(0xe0042000));}

export class Bootloader {
  constructor(core,update = () => {}) {this.core = core;this.update = update;this.started = false;this.verified = false;}
  async wait() {
    const deadline = performance.now() + 2000;
    for (;;) {
      const status = await this.core.read32(STATUS);
      if (status & 0x5c) throw new Error(`N32 Flash 状态错误：${hex(status)}`);
      if (!(status & 1)) return;
      if (performance.now() > deadline) throw new Error('N32 Flash 操作超时');
      await sleep(1);
    }
  }
  async backup() {
    const chip = await identify(this.core);await this.core.halt();
    const before = await this.core.read(BASE,FLASH_SIZE,value => this.update('读取全片备份',value));
    if (before.length !== FLASH_SIZE) throw new Error('全片备份读取不完整');
    return {chip,before};
  }
  async flash(image,manifest,persist) {
    await validateImage(image,manifest);
    const {chip,before} = await this.backup(),{expected,pages} = replacement(before,image);
    const record = {id:new Date().toISOString(),chip,manifest,before,backupSha256:await sha256(before),stage:'backup',programmed:false,verified:false};
    await persist(record);this.update('备份已保存，开始烧录',0);
    try {
      record.stage = 'programming';record.programmingStarted = true;await persist(record);this.started = true;
      await this.core.write32(KEY,0x45670123);await this.core.write32(KEY,0xcdef89ab);
      if ((await this.core.read32(CONTROL)) & 0x80) throw new Error('N32 Flash 解锁失败');
      const control = (await this.core.read32(CONTROL)) & ~7;
      const words = new DataView(expected.buffer,expected.byteOffset,expected.byteLength);
      for (const [index,page] of pages.entries()) {
        await this.core.write32(STATUS,0x7c);await this.wait();
        await this.core.write32(CONTROL,control | 2);await this.core.write32(ADDRESS,page);await this.core.write32(CONTROL,control | 0x42);await this.wait();
        await this.core.write32(CONTROL,control);await this.core.write32(STATUS,0x7c);await this.core.write32(CONTROL,control | 1);
        for (let offset = 0; offset < PAGE; offset += 4) {
          const value = words.getUint32(page - BASE + offset,true);
          if (value !== 0xffffffff) {await this.core.write32(page + offset,value);await this.wait();}
        }
        await this.core.write32(CONTROL,control);this.update(`烧录 Boot 页面 ${index + 1}/${pages.length}`,(index + 1) / pages.length);
      }
      await this.core.write32(CONTROL,control | 0x80);record.programmed = true;record.stage = 'verify';
      const after = await this.core.read(BASE,FLASH_SIZE,value => this.update('全片回读校验',value));
      if (after.length !== expected.length || after.some((value,index) => value !== expected[index])) throw new Error('全片回读与预期不符；未复位，保留暂停状态');
      this.verified = record.verified = true;record.afterSha256 = await sha256(after);record.stage = 'verified';
      await persist(record);await this.core.reset();record.stage = 'complete';await persist(record);
      this.update('烧录及回读校验完成，已复位',1);return record;
    } catch (error) {
      record.stage = 'failed';record.error = error.message;
      await persist(record).catch(() => {});throw error;
    }
  }
}
