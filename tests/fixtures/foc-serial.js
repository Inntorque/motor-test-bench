(() => {
  const {model,wrongModel} = window.__focTest;
  const fixture = window.__focSerial = {actions:[],state:8,opens:0};
  const port = {
    getInfo:() => ({usbVendorId:0x16d0,usbProductId:0x117e}),
    async open() {
      fixture.opens++;
      let reader;
      this.readable = new ReadableStream({start(controller) {reader = controller;}});
      const encoder = new TextEncoder();
      const send = text => reader.enqueue(encoder.encode(text + '\r'));
      const frame = data => send('t5908' + [...data].map(byte => byte.toString(16).padStart(2,'0')).join(''));
      this.writable = new WritableStream({write(chunk) {
        const request = new TextDecoder().decode(chunk).trim();
        if (request === 'V') {send('V1234');return;}
        if (!request.startsWith('t6108')) return;
        const payload = Uint8Array.from(request.slice(5).match(/../g),byte => parseInt(byte,16));
        const [,version,seq,action] = payload;
        fixture.actions.push({version,action,value:new DataView(payload.buffer).getInt16(4,true)});
        if (action === 1) {
          const flags = model === '7010' ? 0 : 1 | (fixture.frozen ? 2 : 0);
          const states = [
            [0xd2,seq,fixture.state,0,15,0,model === '7010' ? 16 : 64,flags],
            [0xd3,seq,250,0,24,252,208,7], [0xd4,seq,0,8,0,0,17,0],
            [0xd5,seq,100,0,30,0,0,16], [0xd6,seq,196,9,64,31,208,7],
            [0xd7,seq,40,35,0,0,16,14],
          ];
          if (model !== '7010') states.push([0xd8,seq,96,9,0,0,0,0]);
          if (fixture.frozen && model !== '7010') states.push([0xd9,seq,0,17,0,3,0,10],[0xda,seq,0,0,208,7,96,9]);
          states.reverse().forEach(frame);
          return;
        }
        if ([21,22,23].includes(action)) {
          const bytes = new Uint8Array(action === 23 ? 140 : 145), view = new DataView(bytes.buffer);
          if (action === 23) {
            view.setUint32(12,144000000,true);view.setUint32(36,9,true);
          } else {
            view.setUint32(8,9,true);bytes[47] = 129;
            for (const [index,value] of [1.25,-.5,-.75].entries()) view.setFloat32(132 + index * 4,value,true);
          }
          for (let i = 0; i < Math.ceil(bytes.length / 5); i++) frame([0xdb,seq,i,...bytes.slice(i*5,i*5+5)]);
          return;
        }
        if (action === 8) fixture.state = 11;
        if (action === 19) {fixture.state = 17;fixture.frozen = false;}
        if (action === 5) {fixture.frozen = fixture.state === 17;fixture.state = 8;}
        frame([0xd1,2,seq,0,...encoder.encode(wrongModel || model)]);
      }});
    },
    async close() {},
  };
  Object.defineProperty(navigator,'serial',{value:{getPorts:async () => [],requestPort:async () => port,addEventListener() {}}});
  Object.defineProperty(navigator,'usb',{value:{getDevices:async () => [],addEventListener() {}}});
})();
