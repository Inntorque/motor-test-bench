import {bytes, concat, pack, SerialQueue, view} from './bytes.js';
export const GS_FILTERS = [
  {vendorId:0x1d50,productId:0x606f},{vendorId:0x1209,productId:0x2323},
  {vendorId:0x1cd2,productId:0x606f},{vendorId:0x16d0,productId:0x10b8},{vendorId:0x1209,productId:0xca01},
];
export function gsBitTiming(capabilities, bitrate) {
  const c = view(capabilities), clock = c.getUint32(4,true);
  let best;
  for (let first = c.getUint32(8,true); first <= c.getUint32(12,true); first++) {
    for (let second = c.getUint32(16,true); second <= c.getUint32(20,true); second++) {
      const brp = clock / (bitrate * (1 + first + second));
      if (!Number.isInteger(brp) || brp < c.getUint32(28,true) || brp > c.getUint32(32,true) || (brp - c.getUint32(28,true)) % c.getUint32(36,true)) continue;
      const error = Math.abs((1 + first) / (1 + first + second) - .875);
      if (!best || error < best.error) best = {error,first,second,brp};
    }
  }
  if (!best) throw new Error('适配器无法实现所选 CAN 波特率');
  return pack('IIIII',1,best.first - 1,best.second,1,best.brp);
}
export class DeviceAccess {
  devices = new Map(); next = 1;
  constructor(emit, changed = () => {}) { this.emit = emit; this.changed = changed; }
  register(device, kind) {
    for (const [id,entry] of this.devices) if (entry.device === device) return id;
    const id = `${kind}-${crypto.randomUUID()}`;
    this.devices.set(id,{device,kind,queue:new SerialQueue(),name:`${kind === 'usb' ? device.productName || 'gs_usb' : '串口'} ${this.next++}`});
    this.changed(); return id;
  }
  async initialize() {
    for (const port of await navigator.serial?.getPorts() || []) this.register(port,'serial');
    for (const device of await navigator.usb?.getDevices() || []) {
      if (GS_FILTERS.some(filter => device.vendorId === filter.vendorId && device.productId === filter.productId)) this.register(device,'usb');
    }
    navigator.serial?.addEventListener('disconnect',event => this.disconnected(event.target));
    navigator.usb?.addEventListener('disconnect',event => this.disconnected(event.device));
  }
  disconnected(device) {
    for (const [id,entry] of this.devices) if (entry.device === device) {
      entry.open = false; this.emit({kind:'io-error',device:id,error:'设备已拔出'});
    }
    this.changed();
  }
  async authorizeSerial() {
    if (!navigator.serial) throw new Error('当前浏览器不支持 Web Serial，请使用桌面版 Chrome 或 Edge');
    return this.register(await navigator.serial.requestPort(),'serial');
  }
  async authorizeUsb() {
    if (!navigator.usb) throw new Error('当前浏览器不支持 WebUSB，请使用桌面版 Chrome 或 Edge');
    return this.register(await navigator.usb.requestDevice({filters:GS_FILTERS}),'usb');
  }
  ports() {
    return [...this.devices].map(([port,entry]) => {
      const info = entry.kind === 'serial' ? entry.device.getInfo() : {usbVendorId:entry.device.vendorId,usbProductId:entry.device.productId};
      return {port,description:`${entry.name} · ${[info.usbVendorId,info.usbProductId].map(id => (id || 0).toString(16).padStart(4,'0')).join(':')}`,kind:entry.kind,vid:info.usbVendorId,pid:info.usbProductId,opened:!!entry.open};
    });
  }
  async call(method, args) {
    if (method === 'ports') return this.ports();
    const entry = this.devices.get(args.device);
    if (!entry) throw new Error('请先点击“授权串口”或“授权 USB-CAN”，再选择对应设备');
    const device = entry.device;
    if (method === 'serial-open') {
      if (entry.open) throw new Error('该串口已被其他设备通道使用');
      await device.open({baudRate:args.baudRate,dataBits:args.dataBits,stopBits:args.stopBits,parity:args.parity,bufferSize:262144});
      entry.open = true; entry.writer = device.writable.getWriter();
      entry.reading = this.readSerial(args.device,entry); this.changed(); return true;
    }
    if (method === 'serial-write') {
      return entry.queue.run(async () => {
        if (!entry.open) throw new Error('串口已断开');
        await entry.writer.write(bytes(args.data)); return true;
      });
    }
    if (method === 'serial-close') {
      if (!entry.open && !entry.writer) return true;
      entry.open = false;
      await entry.reader?.cancel(); await entry.reading;
      await entry.queue.tail;
      entry.writer?.releaseLock(); entry.writer = null;
      await device.close(); this.changed(); return true;
    }
    if (method === 'usb-open') {
      if (entry.open) throw new Error('该 USB-CAN 已被使用');
      await device.open();
      try {
        if (!device.configuration) await device.selectConfiguration(1);
        const iface = device.configuration.interfaces.find(item => item.alternates.some(alt => alt.endpoints.some(endpoint => endpoint.direction === 'out' && endpoint.type === 'bulk')));
        if (!iface) throw new Error('USB-CAN 缺少 bulk 接口');
        entry.interfaceNumber = iface.interfaceNumber;
        await device.claimInterface(entry.interfaceNumber);
        const endpoints = iface.alternate.endpoints;
        entry.input = endpoints.find(endpoint => endpoint.direction === 'in' && endpoint.type === 'bulk')?.endpointNumber;
        entry.output = endpoints.find(endpoint => endpoint.direction === 'out' && endpoint.type === 'bulk')?.endpointNumber;
        if (!entry.input || !entry.output) throw new Error('USB-CAN 端点不完整');
        await this.usbOut(entry,0,pack('I',0x0000beef));
        const capability = await device.controlTransferIn({requestType:'vendor',recipient:'interface',request:4,value:0,index:entry.interfaceNumber},40);
        if (capability.status !== 'ok' || capability.data.byteLength !== 40) throw new Error('USB-CAN 能力读取失败');
        await this.usbOut(entry,2,pack('II',0,0));
        await this.usbOut(entry,1,gsBitTiming(new Uint8Array(capability.data.buffer,capability.data.byteOffset,40),args.bitrate));
        await this.usbOut(entry,2,pack('II',1,0));
        entry.open = true; entry.echo = 0; entry.reading = this.readUsb(args.device,entry); this.changed(); return true;
      } catch (error) {
        if (entry.interfaceNumber !== undefined && device.configuration?.interfaces.find(iface => iface.interfaceNumber === entry.interfaceNumber)?.claimed) await device.releaseInterface(entry.interfaceNumber);
        await device.close(); throw error;
      }
    }
    if (method === 'usb-send') {
      if (!entry.open) throw new Error('USB-CAN 已断开');
      const data = bytes(args.data);
      if (data.length > 8 || args.id < 0 || args.id > 0x7ff) throw new Error('Classic CAN standard frame required');
      return entry.queue.run(async () => {
        const frame = concat(pack('IIBBBB',entry.echo++ >>> 0,args.id,data.length,0,0,0),data,new Uint8Array(8 - data.length));
        const result = await device.transferOut(entry.output,frame);
        if (result.status !== 'ok' || result.bytesWritten !== frame.length) throw new Error('USB-CAN 发送失败');
        return true;
      });
    }
    if (method === 'usb-close') {
      entry.open = false;
      if (device.opened) {
        await entry.queue.tail;
        await this.usbOut(entry,2,pack('II',0,0));
        await device.close();
      }
      this.changed(); return true;
    }
    throw new Error(`Unknown device operation ${method}`);
  }
  async usbOut(entry, request, data) {
    const result = await entry.device.controlTransferOut({requestType:'vendor',recipient:'interface',request,value:0,index:entry.interfaceNumber},data);
    if (result.status !== 'ok') throw new Error(`USB-CAN configuration ${request} failed`);
  }
  async readSerial(id, entry) {
    entry.reader = entry.device.readable.getReader();
    try {
      while (entry.open) {
        const {done,value} = await entry.reader.read();
        if (done) break;
        if (value?.length) this.emit({kind:'input',device:id,data:value},[value.buffer]);
      }
    } catch (error) { if (entry.open) this.emit({kind:'io-error',device:id,error:error.message}); }
    finally { entry.reader.releaseLock(); entry.reader = null; }
  }
  async readUsb(id, entry) {
    try {
      while (entry.open) {
        const result = await entry.device.transferIn(entry.input,64);
        if (result.status !== 'ok') throw new Error('USB-CAN 接收失败');
        if (!result.data || result.data.byteLength < 20) continue;
        const frame = result.data;
        if (frame.getUint32(0,true) !== 0xffffffff || frame.getUint8(8) > 8 || frame.getUint8(9) !== 0 || frame.getUint32(4,true) > 0x7ff) continue;
        const data = new Uint8Array(frame.buffer,frame.byteOffset + 12,frame.getUint8(8)).slice();
        this.emit({kind:'input',device:id,data:{id:frame.getUint32(4,true),data}},[data.buffer]);
      }
    } catch (error) { if (entry.open) this.emit({kind:'io-error',device:id,error:error.message}); }
  }
}
