import {bytes, concat, crc16, hex, now, SerialQueue, sleep, view} from './bytes.ts';
const arbitrationCodes = new Map([1000000,800000,500000,400000,250000,200000,125000,100000,50000,40000,25000,20000,15000,10000,5000].map((rate, index) => [rate, index]));
const dataCodes = new Map([5000000,4000000,2000000,1000000,800000,500000,400000,250000,200000,125000,100000].map((rate, index) => [rate, index]));
const slcanRates = new Map([10000,20000,50000,100000,125000,250000,500000,750000,1000000,83300].map((rate,index) => [rate,index]));
export function canInterface(port) {
  return port.kind === 'usb' ? 'gs_usb' : port.vid === 0x16d0 && port.pid === 0x117e ? 'slcan' : port.vid === 0x3562 ? 'zqwl' : null;
}
export function slcanFrame(id,data) {
  if (!Number.isInteger(id) || id < 0 || id > 0x7ff || data.length > 8) throw new Error('Classic CAN standard frame required');
  return new TextEncoder().encode(`t${id.toString(16).padStart(3,'0')}${data.length}${hex(data)}\r`);
}
export class SlcanDecoder {
  declare receive;

  buffer = ''; version = ''; rejected = 0; dropped = 0;
  constructor(receive) {this.receive = receive;}
  push(chunk) {
    const text = new TextDecoder().decode(chunk);this.rejected += text.split('\x07').length - 1;
    this.buffer += text.replaceAll('\x07','');
    const lines = this.buffer.split(/[\r\n]/);this.buffer = lines.pop();
    if (this.buffer.length > 512) {this.buffer = '';this.dropped++;}
    for (const line of lines) {
      if (/canable2/i.test(line) || /^V[\da-f]{4}$/i.test(line)) {this.version = line;continue;}
      if (!line.startsWith('t')) continue;
      const match = /^t([\da-f]{3})([0-8])([\da-f]*?)$/i.exec(line);
      if (!match) {this.dropped++;continue;}
      const id = parseInt(match[1],16),length = Number(match[2]),data = match[3];
      if (id > 0x7ff || data.length !== length * 2 && data.length !== length * 2 + 4) {this.dropped++;continue;}
      this.receive(id,Uint8Array.from({length},(_,index) => parseInt(data.slice(index * 2,index * 2 + 2),16)));
    }
  }
}
export function zqwlConfig(func, data) {
  const body = new Uint8Array(16); body.set(data.slice(0,16));
  return concat(Uint8Array.of(0x49,0x3b,func,0x57), body, Uint8Array.of(0x45,0x2e));
}
export function zqwlFrame(id, data) {
  if (!Number.isInteger(id) || id < 0 || id > 0x7ff || data.length > 8) throw new Error('Classic CAN standard frame required');
  return concat(Uint8Array.of(0x5a,data.length,0x40,0,0,id >> 8,id & 255), data, Uint8Array.of(0xa5));
}
export class ZqwlDecoder {
  declare heartbeat: Uint8Array<ArrayBuffer> | undefined;
  declare receive;

  buffer = new Uint8Array(); heartbeats = 0; dropped = 0;
  constructor(receive) { this.receive = receive; }
  push(chunk) {
    this.buffer = concat(this.buffer, chunk);
    while (this.buffer.length) {
      const b = this.buffer;
      if (b[0] === 0x5a) {
        if (b.length < 2) break;
        if (b[1] === 0xff || b[1] === 0xfe) {
          if (b.length < 17) break;
          this.heartbeats++; this.heartbeat = b.slice(0,17); this.buffer = b.slice(17); continue;
        }
        const dlc = b[1] & 15;
        if (dlc > 8) { this.dropped++; this.buffer = b.slice(1); continue; }
        if (b.length < 8 + dlc) break;
        this.buffer = b.slice(8 + dlc);
        if (b[7 + dlc] !== 0xa5) { this.dropped++; continue; }
        if (!(b[2] & 6)) this.receive(((b[5] << 8) | b[6]) & 0x7ff, b.slice(7,7 + dlc));
      } else if (b[0] === 0x49) {
        if (b.length < 22) break;
        this.buffer = b.slice(22);
      } else { this.buffer = b.slice(1); this.dropped++; }
    }
  }
}
export class CanTransport {
  declare closed: boolean;
  declare decoder: SlcanDecoder | ZqwlDecoder | undefined;
  declare device;
  declare interface;
  declare io;
  declare owned: boolean;
  declare receive;
  declare remove;

  queue = new SerialQueue();
  constructor(io, receive) { this.io = io; this.receive = receive; this.closed = true;this.owned = false; }
  async open(config) {
    this.device = config.device; this.interface = config.interface || 'zqwl';
    const bitrate = Number(config.bitrate || config.baud || 500000);
    if (!(this.interface === 'slcan' ? slcanRates : arbitrationCodes).has(bitrate)) throw new Error('Unsupported CAN bitrate');
    if (this.interface === 'gs_usb') {
      await this.io.call('usb-open', {device:this.device, bitrate});
      this.owned = true;
      this.remove = this.io.subscribe(this.device, frame => this.receive(frame.id, bytes(frame.data)));
    } else if (this.interface === 'slcan') {
      this.decoder = new SlcanDecoder(this.receive);
      this.remove = this.io.subscribe(this.device,data => this.decoder.push(bytes(data)));
      try {
        await this.io.call('serial-open',{device:this.device,baudRate:115200,dataBits:8,stopBits:1,parity:'none'});
        this.owned = true;
        const command = value => this.io.call('serial-write',{device:this.device,data:new TextEncoder().encode(value + '\r')});
        await command('V');const deadline = now() + 1;
        while (!this.decoder.version && now() < deadline) await sleep(10);
        if (!this.decoder.version) throw new Error('CANable2/SLCAN 版本查询没有应答，请核对串口');
        for (const value of ['C',`S${slcanRates.get(bitrate)}`,'M0','O']) {await command(value);await sleep(10);}
        if (this.decoder.rejected) throw new Error('CANable2 拒绝适配器配置');
      } catch (error) {await this.close();throw error;}
    } else {
      this.decoder = new ZqwlDecoder(this.receive);
      this.remove = this.io.subscribe(this.device, data => this.decoder.push(bytes(data)));
      try {
        await this.io.call('serial-open', {device:this.device, baudRate:3000000, dataBits:8, stopBits:1, parity:'none'});
        this.owned = true;
        const code = (arbitrationCodes.get(bitrate) << 4) | (dataCodes.get(bitrate) || 0);
        await this.io.call('serial-write', {device:this.device, data:zqwlConfig(0x42,[0,0,code])});
        await this.io.call('serial-write', {device:this.device, data:zqwlConfig(0x44,[1,0,1,0])});
        const deadline = now() + 2;
        while (this.decoder.heartbeats < 2 && now() < deadline) await sleep(20);
        await sleep(150);
        if (!this.decoder.heartbeats) throw new Error('ZQWL 适配器没有心跳，请核对所选串口');
      } catch (error) {await this.close();throw error;}
    }
    this.closed = false;
  }
  send(id, data) {
    if (this.closed) return Promise.reject(new Error('CAN 未连接'));
    return this.queue.run(async () => {
      if (this.interface === 'gs_usb') return this.io.call('usb-send',{device:this.device,id,data});
      await this.io.call('serial-write',{device:this.device,data:this.interface === 'slcan' ? slcanFrame(id,data) : zqwlFrame(id,data)});
      await sleep(.3);
    });
  }
  async close() {
    this.closed = true;this.remove?.();
    if (!this.owned) return;
    try {if (this.interface === 'slcan') await this.io.call('serial-write',{device:this.device,data:new TextEncoder().encode('C\r')});}
    finally {await this.io.call(this.interface === 'gs_usb' ? 'usb-close' : 'serial-close',{device:this.device});this.owned = false;}
  }
}
export class ModbusRtu {
  declare config;
  declare device;
  declare io;
  declare opened: boolean | undefined;
  declare remove;

  queue = new SerialQueue(); buffer = new Uint8Array(); lastFrame = 0;
  constructor(io) { this.io = io; }
  async open(config) {
    if (config.transport === 'tcp') throw new Error('浏览器不能直接连接原始 TCP；请使用 USB-RS485 串口');
    this.config = {...config}; this.device = config.device;
    this.remove = this.io.subscribe(this.device, data => { this.buffer = concat(this.buffer,bytes(data)); });
    try {
      await this.io.call('serial-open',{device:this.device,baudRate:Number(config.baud || 9600),dataBits:8,stopBits:Number(config.stopbits || 1),parity:({N:'none',E:'even',O:'odd'})[config.parity || 'N']});
      this.opened = true;
    } catch (error) { this.remove(); throw error; }
  }
  exchange(func: 1 | 2, register: number, count?: number, values?: (number | boolean)[]): Promise<boolean[]>;
  exchange(func: 3 | 4, register: number, count?: number, values?: (number | boolean)[]): Promise<number[]>;
  exchange(func: 5 | 6 | 16, register: number, count?: number, values?: (number | boolean)[]): Promise<[]>;
  exchange(func: number, register: number, count?: number, values?: (number | boolean)[]): Promise<(number | boolean)[]>;
  exchange(func: number, register: number, count = 1, values: (number | boolean)[] = []): Promise<(number | boolean)[]> {
    return this.queue.run(async () => {
      const address = Number(this.config.address || 1);
      let body = Uint8Array.of(address,func,register >> 8,register & 255,count >> 8,count & 255);
      if (func === 5 || func === 6) {
        const value = func === 5 ? (values[0] ? 0xff00 : 0) : values[0] as number;
        body = Uint8Array.of(address,func,register >> 8,register & 255,value >> 8,value & 255);
      } else if (func === 16) body = concat(Uint8Array.of(address,func,register >> 8,register & 255,values.length >> 8,values.length & 255,values.length * 2),Uint8Array.from(values.flatMap(value => [(value as number) >> 8,(value as number) & 255])));
      const silent = Math.max(.0035,3.5 * (9 + Number(this.config.stopbits || 1)) / Number(this.config.baud || 9600));
      const remaining = silent - (now() - this.lastFrame);
      if (remaining > 0) await sleep(remaining * 1000);
      this.buffer = new Uint8Array();
      const frame = concat(body,crc16(body));
      await this.io.call('serial-write',{device:this.device,data:frame});
      const deadline = now() + .8;
      let expected = 0;
      while (now() < deadline) {
        if (this.buffer.length >= 2 && this.buffer[1] & 0x80) expected = 5;
        else if (this.buffer.length >= 3) expected = [5,6,16].includes(func) ? 8 : this.buffer[2] + 5;
        if (expected && this.buffer.length >= expected) break;
        await sleep(1);
      }
      this.lastFrame = now();
      const reply = this.buffer.slice(0,expected || this.buffer.length);
      if (reply.length < 5) throw new Error('RS485 无有效回复，请核对地址、波特率、停止位和 A/B 接线');
      if (hex(crc16(reply.slice(0,-2))) !== hex(reply.slice(-2))) throw new Error('Modbus CRC 校验失败');
      if (reply[0] !== address) throw new Error('Modbus 设备地址不匹配');
      if (reply[1] & 0x80) throw new Error(`Modbus 异常码 ${reply[2]}`);
      if (reply[1] !== func) throw new Error('Modbus 功能码不匹配');
      if ([5,6,16].includes(func)) {
        if (hex(reply.slice(0,6)) !== hex(body.slice(0,6))) throw new Error('Modbus 写入读回不匹配');
        return [];
      }
      if (func === 1 || func === 2) {
        if (reply[2] !== Math.ceil(count / 8)) throw new Error('Modbus 位读取长度不匹配');
        return Array.from({length:count},(_,index) => Boolean(reply[3 + (index >> 3)] & 1 << index % 8));
      }
      if (reply[2] !== count * 2 || reply.length !== count * 2 + 5) throw new Error('Modbus 寄存器数量不匹配');
      return Array.from({length:count},(_,index) => view(reply).getUint16(3 + index * 2,false));
    });
  }
  async close() { this.remove?.(); if (this.opened) {await this.io.call('serial-close',{device:this.device});this.opened = false;} }
}
