import {VirtualBoard} from './virtual-mbp.js';
import {slcanFrame,SlcanDecoder,ZqwlDecoder,zqwlFrame} from '../../browser/transports.ts';
const state = window.__serialFixture = {board:new VirtualBoard(),authorizations:0,opens:0,writeCount:0,granted:false};
const slcan = Boolean(window.__browserTestSlcan);
const port = {
  getInfo:() => slcan ? {usbVendorId:0x16d0,usbProductId:0x117e} : {usbVendorId:0x3562,usbProductId:0x1001},
  async open(options) {
    if (options.baudRate !== (slcan ? 115200 : 3000000)) throw new Error('Fixture serial bitrate mismatch');
    state.opens++;
    let receive;
    this.readable = new ReadableStream({start(controller) {
      const decoder = new ZqwlDecoder((id,data) => controller.enqueue(slcanFrame(id,data)));
      receive = data => {if (slcan) decoder.push(data);else controller.enqueue(data.slice());};state.board.listeners.add(receive);
      port.version = () => controller.enqueue(new TextEncoder().encode('16e7497-dirty github.com/normaldotcom/canable2.git\r'));
    },cancel() {state.board.listeners.delete(receive);}});
    const decoder = new SlcanDecoder((id,data) => {void state.board.call('serial-write',{data:zqwlFrame(id,data)});});
    this.writable = new WritableStream({async write(data) {
      state.writeCount++;
      if (!slcan) {await state.board.call('serial-write',{data});return;}
      if (new TextDecoder().decode(data) === 'V\r') port.version();else decoder.push(data);
    }});
    this.timer = setInterval(() => state.board.publish(),50);
  },
  async close() {clearInterval(this.timer);},
};
Object.defineProperty(navigator,'serial',{configurable:true,value:{getPorts:async () => state.granted ? [port] : [],requestPort:async () => {state.authorizations++;state.granted = true;return port;},addEventListener() {}}});
