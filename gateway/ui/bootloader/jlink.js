const sleep = ms => new Promise(resolve => setTimeout(resolve,ms));

export class JLink {
  constructor(device) {this.device = device;this.pending = new Uint8Array();this.owned = false;}
  async usb(promise) {
    let timer;
    try {
      return await Promise.race([promise,new Promise((_,reject) => {
        timer = setTimeout(() => {this.device.close().catch(() => {});reject(new Error('J-Link USB 通信超时，连接已关闭'));},5000);
      })]);
    } finally {clearTimeout(timer);}
  }
  async open() {
    if (this.device.vendorId !== 0x1366 || this.device.opened) throw new Error('请选择未被其他程序打开的 J-Link');
    await this.device.open();this.owned = true;
    try {
      if (!this.device.configuration) await this.device.selectConfiguration(1);
      const candidate = this.device.configuration.interfaces.flatMap(iface => iface.alternates.map(alt => ({iface,alt})))
        .find(({alt}) => alt.interfaceClass === 255 && alt.endpoints.some(ep => ep.type === 'bulk' && ep.direction === 'in') && alt.endpoints.some(ep => ep.type === 'bulk' && ep.direction === 'out'));
      if (!candidate) throw new Error('J-Link 没有可用的 USB 调试接口');
      this.interfaceNumber = candidate.iface.interfaceNumber;
      await this.device.claimInterface(this.interfaceNumber);this.claimed = true;
      await this.device.selectAlternateInterface(this.interfaceNumber,candidate.alt.alternateSetting);
      this.input = candidate.alt.endpoints.find(ep => ep.direction === 'in' && ep.type === 'bulk').endpointNumber;
      this.output = candidate.alt.endpoints.find(ep => ep.direction === 'out' && ep.type === 'bulk').endpointNumber;
      await this.write([1]);const size = await this.read(2),length = size[0] | size[1] << 8;
      if (!length || length > 1024) throw new Error('J-Link 固件版本应答无效');
      this.version = new TextDecoder().decode(await this.read(length)).replaceAll('\0','');
      await this.write([7]);const hardware = await this.read(8);this.millivolts = hardware[0] | hardware[1] << 8;
      await this.write([0xc7,0xff]);const interfaces = await this.read(4);
      if (!(interfaces[0] & 2)) throw new Error('此 J-Link 不支持 SWD');
      await this.write([0xc7,1]);await this.read(4);
      await this.write([5,100,0]);
    } catch (error) {await this.close();throw error;}
  }
  async write(data) {
    const packet = Uint8Array.from(data),result = await this.usb(this.device.transferOut(this.output,packet));
    if (result.status !== 'ok' || result.bytesWritten !== packet.length) throw new Error('J-Link USB 写入不完整');
  }
  async read(length) {
    const output = new Uint8Array(length);let offset = 0;
    while (offset < length) {
      if (!this.pending.length) {
        const result = await this.usb(this.device.transferIn(this.input,Math.max(64,length - offset)));
        if (result.status !== 'ok' || !result.data?.byteLength) throw new Error('J-Link USB 应答为空或传输失败');
        this.pending = new Uint8Array(result.data.buffer,result.data.byteOffset,result.data.byteLength);
      }
      const count = Math.min(this.pending.length,length - offset);
      output.set(this.pending.subarray(0,count),offset);offset += count;this.pending = this.pending.subarray(count);
    }
    return output;
  }
  async bits(direction,data,count) {
    const size = Math.ceil(count / 8),packet = new Uint8Array(4 + size * 2);
    if (!count || count > 16384 || direction.length !== size || data.length !== size) throw new Error('SWD 位序列长度无效');
    packet.set([0xcf,0,count & 255,count >> 8]);packet.set(direction,4);packet.set(data,4 + size);
    await this.write(packet);const response = await this.read(size + 1);
    if (response[size]) throw new Error(`J-Link SWD 操作失败：${response[size]}`);
    return response.subarray(0,size);
  }
  async close() {
    if (!this.owned) return;
    try {if (this.device.opened && this.claimed) await this.device.releaseInterface(this.interfaceNumber);}
    finally {this.claimed = false;this.owned = false;if (this.device.opened) await this.device.close();this.pending = new Uint8Array();}
  }
}

export {sleep};
