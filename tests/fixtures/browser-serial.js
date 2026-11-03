import {VirtualBoard} from './virtual-mbp.js';
const state = window.__serialFixture = {board:new VirtualBoard(),authorizations:0,opens:0,writeCount:0,granted:false};
const port = {
  getInfo:() => ({usbVendorId:0x3562,usbProductId:0x1001}),
  async open(options) {
    if (options.baudRate !== 3000000) throw new Error('ZQWL fixture expected 3 Mbaud');
    state.opens++;
    let receive;
    this.readable = new ReadableStream({start(controller) {receive = data => controller.enqueue(data.slice());state.board.listeners.add(receive);},cancel() {state.board.listeners.delete(receive);}});
    this.writable = new WritableStream({async write(data) {state.writeCount++;await state.board.call('serial-write',{data});}});
    this.timer = setInterval(() => state.board.publish(),50);
  },
  async close() {clearInterval(this.timer);},
};
Object.defineProperty(navigator,'serial',{configurable:true,value:{getPorts:async () => state.granted ? [port] : [],requestPort:async () => {state.authorizations++;state.granted = true;return port;},addEventListener() {}}});
