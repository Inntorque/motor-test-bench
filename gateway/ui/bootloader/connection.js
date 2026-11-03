import {JLink} from './jlink.js';
import {Swd} from './swd.js';
import {identify} from './n32.js';

const sameProbe = (a,b) => a === b || !!a?.serialNumber && a.serialNumber === b?.serialNumber && a.vendorId === b.vendorId && a.productId === b.productId;

export class Connection {
  constructor(usb,changed) {this.usb = usb;this.changed = changed;this.enabled = false;this.paused = false;this.generation = 0;this.attempts = 0;}
  connect(device) {
    this.selected = device;this.enabled = true;this.generation++;this.attempts = 0;
    this.kick();
  }
  disconnect() {
    this.enabled = false;this.generation++;clearTimeout(this.timer);
    this.changed({state:'off',message:'不连接；停止自动识别',attempts:this.attempts});this.kick();
  }
  deviceChanged(device) {if (this.enabled && sameProbe(this.selected,device)) this.kick();}
  kick() {
    clearTimeout(this.timer);
    if (this.pending || this.paused) return;
    const generation = this.generation;
    this.pending = this.step(generation).finally(() => {
      this.pending = undefined;
      if (this.paused) return;
      if (generation !== this.generation) this.kick();
      else if (this.enabled) this.timer = setTimeout(() => this.kick(),1000);
    });
  }
  async close() {
    const session = this.session;this.session = undefined;
    if (session) await session.probe.close().catch(() => {});
  }
  current(generation) {return this.enabled && generation === this.generation;}
  async step(generation) {
    if (!this.enabled) {await this.close();return;}
    if (this.session && !sameProbe(this.selected,this.session.probe.device)) await this.close();
    let probe = this.session?.probe;
    try {
      if (this.session) {
        await identify(this.session.core);
        if (!this.current(generation)) await this.close();
        return;
      }
      this.attempts++;
      this.changed({state:'waiting',message:'正在自动识别所选 J-Link 与芯片',attempts:this.attempts});
      const devices = await this.usb.getDevices();
      if (!this.current(generation)) return;
      const device = devices.find(device => sameProbe(this.selected,device));
      if (!device) throw new Error('等待所选 J-Link 接入；不会连接其他探针');
      probe = new JLink(device);await probe.open();
      if (!this.current(generation)) {await probe.close();return;}
      const core = new Swd(probe);await core.init();const chip = await identify(core);
      if (!this.current(generation)) {await probe.close();return;}
      this.session = {probe,core,chip};
      this.changed({state:'connected',message:'芯片已识别；持续检查连接',session:this.session,attempts:this.attempts});
    } catch (error) {
      this.session = undefined;await probe?.close().catch(() => {});
      if (this.current(generation)) this.changed({state:'waiting',message:`${error.message}；保持连接选择，1 秒后自动重试`,error:true,attempts:this.attempts});
    }
  }
  async pause() {
    this.paused = true;clearTimeout(this.timer);await this.pending;
    if (!this.session) throw new Error('芯片尚未识别，不能开始设备操作');
    return this.session.core;
  }
  resume() {this.paused = false;this.kick();}
}
