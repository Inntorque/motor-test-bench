import {sleep} from './jlink.js';
export const bit = (data,index) => (data[index >> 3] >> (index & 7)) & 1;
export function put(data,index,value) {if (value) data[index >> 3] |= 1 << (index & 7);}
export function parity(value) {
  let result = 0;
  for (let i = 0; i < 32; i++) result ^= (value >>> i) & 1;
  return result;
}
export function request(ap,read,address) {
  const a2 = (address >> 2) & 1,a3 = (address >> 3) & 1;
  return 0x81 | ap << 1 | read << 2 | a2 << 3 | a3 << 4 | (ap ^ read ^ a2 ^ a3) << 5;
}
export function frames(operations) {
  const count = operations.length * 54,direction = new Uint8Array(Math.ceil(count / 8)),data = new Uint8Array(direction.length);
  operations.forEach((op,index) => {
    const base = index * 54,header = request(op.ap,op.read,op.address);
    for (let i = 0; i < 8; i++) {put(direction,base + i,1);put(data,base + i,(header >> i) & 1);}
    if (!op.read) {
      for (let i = 0; i < 32; i++) {put(direction,base + 13 + i,1);put(data,base + 13 + i,(op.value >>> i) & 1);}
      put(direction,base + 45,1);put(data,base + 45,parity(op.value));
    }
    // Neither turnaround clock is driven by the host.
    for (let i = 46; i < 54; i++) put(direction,base + i,1);
  });
  return {direction,data,count};
}
export function decode(data,operations) {
  return operations.map((op,index) => {
    const base = index * 54,ack = bit(data,base + 9) | bit(data,base + 10) << 1 | bit(data,base + 11) << 2;
    if (ack !== 1) throw new Error(`SWD ACK=${ack}；请核对 SWD 接线、目标供电和芯片状态`);
    if (!op.read) return 0;
    let value = 0;
    for (let i = 0; i < 32; i++) value = (value | bit(data,base + 12 + i) << i) >>> 0;
    if (bit(data,base + 44) !== parity(value)) throw new Error('SWD 数据奇偶校验失败');
    return value;
  });
}

export class Swd {
  constructor(probe) {this.probe = probe;}
  async transfer(operations) {
    const {direction,data,count} = frames(operations);
    return decode(await this.probe.bits(direction,data,count),operations);
  }
  async dp(read,address,value = 0) {return (await this.transfer([{ap:0,read,address,value}]))[0];}
  async ap(read,address,value = 0) {return (await this.transfer([{ap:1,read,address,value}]))[0];}
  async init() {
    for (const data of [new Uint8Array(7).fill(255),Uint8Array.of(0x9e,0xe7),new Uint8Array(7).fill(255),Uint8Array.of(0)])
      await this.probe.bits(new Uint8Array(data.length).fill(255),data,data.length * 8);
    this.dpid = await this.dp(1,0);
    if (this.dpid === 0 || this.dpid === 0xffffffff) throw new Error('SWD 未读取到有效 DP ID');
    await this.dp(0,0,0x1e);await this.dp(0,8,0);await this.dp(0,4,0x50000001);
    const deadline = performance.now() + 1500;
    while (((await this.dp(1,4)) & 0xa0000000) >>> 0 !== 0xa0000000) {
      if (performance.now() > deadline) throw new Error('SWD 调试电源未就绪');
      await sleep(5);
    }
    await this.ap(0,0,0x23000052);await this.dp(1,12);
  }
  async read32(address) {
    await this.ap(0,4,address);await this.ap(1,12);const result = await this.dp(1,12);await this.check();return result;
  }
  async write32(address,value) {
    await this.ap(0,4,address);await this.ap(0,12,value);await this.dp(1,12);await this.check();
  }
  async check() {if ((await this.dp(1,4)) & 0xa2) throw new Error('SWD MEM-AP 总线访问失败');}
  async read(address,length,progress = () => {}) {
    if (address % 4 || length % 4) throw new Error('SWD 读取必须按 32 位对齐');
    const result = new Uint8Array(length),view = new DataView(result.buffer);
    for (let offset = 0; offset < length;) {
      const count = Math.min((1024 - ((address + offset) & 1023)) / 4,(length - offset) / 4);
      await this.ap(0,4,address + offset);
      const operations = Array.from({length:count},() => ({ap:1,read:1,address:12}));
      operations.push({ap:0,read:1,address:12});
      const data = await this.transfer(operations);
      await this.check();
      for (let i = 0; i < count; i++) view.setUint32(offset + i * 4,data[i + 1],true);
      offset += count * 4;progress(offset / length);
    }
    return result;
  }
  async halt() {
    await this.write32(0xe000edf0,0xa05f0003);
    const deadline = performance.now() + 1500;
    while (!((await this.read32(0xe000edf0)) & 0x20000)) {
      if (performance.now() > deadline) throw new Error('目标芯片未进入暂停状态');
      await sleep(5);
    }
    this.debugControl = await this.read32(0xe0042004);
    await this.write32(0xe0042004,this.debugControl | 0x100);
  }
  async resume() {
    if (this.debugControl !== undefined) await this.write32(0xe0042004,this.debugControl);
    await this.write32(0xe000edf0,0xa05f0001);
  }
  async reset() {
    if (this.debugControl !== undefined) await this.write32(0xe0042004,this.debugControl);
    await this.write32(0xe000edfc,(await this.read32(0xe000edfc)) & ~1);
    await this.write32(0xe000ed0c,0x05fa0004);
    await sleep(100);await this.write32(0xe000edf0,0xa05f0001);
  }
}
