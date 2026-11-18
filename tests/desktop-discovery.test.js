import test from 'node:test';
import assert from 'node:assert/strict';
import {DeviceAccess} from '../browser/hardware.ts';

test('desktop discovers and refreshes interfaces without permission prompts or device I/O', async t => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis,'navigator');
  t.after(() => Object.defineProperty(globalThis,'navigator',descriptor));
  let requests=0, opens=0;
  const port={getInfo:()=>({usbVendorId:0x3562,usbProductId:0x1001}),open:()=>{opens++;}};
  const usb={vendorId:0x1d50,productId:0x606f,open:()=>{opens++;}};
  const serialEvents={},usbEvents={};
  let ports=[port],devices=[usb,{vendorId:1,productId:2}];
  Object.defineProperty(globalThis,'navigator',{configurable:true,value:{
    serial:{getPorts:async()=>ports,requestPort:()=>{requests++;},addEventListener:(name,handler)=>{serialEvents[name]=handler;}},
    usb:{getDevices:async()=>devices,requestDevice:()=>{requests++;},addEventListener:(name,handler)=>{usbEvents[name]=handler;}},
  }});
  const access=new DeviceAccess(()=>{},()=>{},true);
  await access.initialize();
  assert.equal(access.ports().length,2);
  const serialId=access.ports().find(item=>item.kind==='serial').port;
  ports=[];serialEvents.disconnect({target:port});
  assert.equal((await access.call('ports',{})).length,1);
  ports=[port];serialEvents.connect({target:port});
  assert.equal(access.ports().find(item=>item.kind==='serial').port,serialId);
  devices=[];usbEvents.disconnect({device:usb});
  assert.equal(access.ports().length,1);
  devices=[usb];usbEvents.connect({device:usb});
  assert.equal(access.ports().length,2);
  await access.call('ports',{});
  assert.equal(requests,0);
  assert.equal(opens,0);
});
