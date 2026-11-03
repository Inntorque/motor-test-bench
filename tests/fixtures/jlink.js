// USB/SWD target model for isolated tests. No physical USB API is used here.
export class VirtualJLink {
  vendorId = 0x1366;productId = 0x0105;serialNumber = 'TEST-JLINK';opened = false;
  configuration = {interfaces:[{interfaceNumber:0,alternates:[{interfaceClass:255,alternateSetting:0,endpoints:[{type:'bulk',direction:'in',endpointNumber:1},{type:'bulk',direction:'out',endpointNumber:2}]}]}]};
  flash = Uint8Array.from({length:0x80000},(_,i) => i % 251);
  responses = [];commands = [];writes = [];erased = [];resets = 0;halted = false;control = 0x80;status = 0;debug = 0x10;badId = false;
  opens = 0;closes = 0;
  async open() {this.opens++;this.opened = true;}
  async claimInterface() {this.claimed = true;}
  async selectAlternateInterface() {}
  async releaseInterface() {this.claimed = false;}
  async close() {this.closes++;this.opened = false;}
  queue(data) {this.responses.push(Uint8Array.from(data));}
  async transferIn(_ep,length) {
    if (!this.opened) throw new Error('USB device disconnected');
    if (!this.responses.length) throw new Error('Unexpected USB read without device response');
    const chunk = this.responses[0],data = chunk.slice(0,length);
    if (data.length === chunk.length) this.responses.shift();else this.responses[0] = chunk.slice(length);
    return {status:'ok',data:new DataView(data.buffer)};
  }
  async transferOut(_ep,packet) {
    if (!this.opened) throw new Error('USB device disconnected');
    const data = new Uint8Array(packet);this.commands.push(data[0]);
    if (data[0] === 1) {const text = new TextEncoder().encode('J-Link V9 compiled test');this.queue([text.length,0,...text]);}
    else if (data[0] === 7) this.queue([0xd7,0x0c,1,1,1,1,1,1]);
    else if (data[0] === 0xc7) this.queue([data[1] === 255 ? 2 : 0,0,0,0]);
    else if (data[0] === 0xcf) {
      const count = data[2] | data[3] << 8,size = Math.ceil(count / 8),direction = data.subarray(4,4 + size),out = data.subarray(4 + size);
      const response = new Uint8Array(size);
      const get = (array,index) => (array[index >> 3] >> (index & 7)) & 1;
      const put = (index,value) => {if (value) response[index >> 3] |= 1 << (index & 7);};
      if (count % 54 === 0) {
        for (let base = 0; base < count; base += 54) {
          let header = 0;
          for (let i = 0; i < 8; i++) header |= get(out,base + i) << i;
          const ap = (header >> 1) & 1,read = (header >> 2) & 1,address = ((header >> 3) & 3) * 4;
          if (get(direction,base + 8) || get(direction,base + (read ? 45 : 12))) throw new Error('Host must release SWD turnaround');
          put(base + 9,1);
          let value = 0;
          if (!read) for (let i = 0; i < 32; i++) value = (value | get(out,base + 13 + i) << i) >>> 0;
          if (ap) {
            if (read) {value = this.pending || 0;this.pending = this.readMemory(this.tar);this.increment();}
            else if (address === 0) this.csw = value;
            else if (address === 4) this.tar = value;
            else if (address === 12) {this.writeMemory(this.tar,value);this.increment();}
          } else if (read) value = address === 0 ? 0x2ba01477 : address === 4 ? 0xf0000001 : this.pending || 0;
          if (read) {
            let parity = 0;
            for (let i = 0; i < 32; i++) {const bit = (value >>> i) & 1;put(base + 12 + i,bit);parity ^= bit;}
            put(base + 44,parity);
          }
        }
      }
      this.queue(response);this.queue([this.swdStatus || 0]);
    } else if (data[0] !== 5) throw new Error(`Unexpected J-Link command ${data[0]}`);
    return {status:'ok',bytesWritten:data.length};
  }
  increment() {if ((this.csw & 0x30) === 0x10) this.tar = ((this.tar & ~1023) | ((this.tar + 4) & 1023)) >>> 0;}
  readMemory(address) {
    if (address >= 0x08000000 && address < 0x08080000) {
      const value = new DataView(this.flash.buffer).getUint32(address - 0x08000000,true);
      return this.corruptReadback && this.erased.length && address === 0x08040000 ? value ^ 1 : value;
    }
    const registers = {0xe000ed00:0x410fc241,0xe0042000:this.badId ? 0x80284501 : 0x80584501,0xe000edf0:this.halted ? 0x20000 : 0,
      0xe0042004:this.debug,0x40022010:this.control,0x4002200c:this.status,0xe000edfc:0};
    return registers[address] || 0;
  }
  writeMemory(address,value) {
    this.writes.push([address,value]);
    if (address === 0xe000edf0) this.halted = !!(value & 2);
    else if (address === 0xe0042004) this.debug = value;
    else if (address === 0xe000ed0c) this.resets++;
    else if (address === 0x40022004 && value === 0xcdef89ab) this.control &= ~0x80;
    else if (address === 0x4002200c) this.status = 0;
    else if (address === 0x40022014) this.page = value;
    else if (address === 0x40022010) {
      if (value & 4) throw new Error('Mass erase is forbidden');
      this.control = value & ~0x40;
      if ((value & 0x42) === 0x42) {
        if (this.page < 0x08000000 || this.page >= 0x08008000 || this.page % 0x800) throw new Error('Erase outside Boot');
        this.erased.push(this.page);this.flash.fill(255,this.page - 0x08000000,this.page - 0x08000000 + 0x800);
      }
    } else if (address >= 0x08000000 && address < 0x08080000) {
      if (address >= 0x08008000 || !(this.control & 1)) throw new Error('Invalid program address or mode');
      new DataView(this.flash.buffer).setUint32(address - 0x08000000,value,true);
    }
  }
}
