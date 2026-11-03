import test from 'node:test';
import assert from 'node:assert/strict';
import {Connection} from '../gateway/ui/bootloader/connection.ts';
import {VirtualJLink} from './fixtures/jlink.js';
const sleep = ms => new Promise(resolve => setTimeout(resolve,ms));
async function until(condition) {
  const deadline = performance.now() + 6000;
  while (!condition()) {assert.ok(performance.now() < deadline,'connection state timed out');await sleep(10);}
}
async function stop(connection) {connection.disconnect();connection.resume();await until(() => !connection.pending && !connection.session);}

test('persistent connection retries identification, reconnects only selected probe and stays off after stop',async () => {
  const initial = new VirtualJLink(),other = new VirtualJLink();other.serialNumber = 'OTHER';initial.badId = true;
  const usb = {devices:[other,initial],queries:0,async getDevices() {this.queries++;return this.devices;}},states = [];
  const connection = new Connection(usb,state => states.push(state));
  assert.equal(usb.queries,0);
  try {
    connection.connect(initial);
    await until(() => usb.queries >= 2 && states.at(-1)?.error);
    assert.equal(connection.enabled,true);assert.equal(connection.session,undefined);assert.equal(other.opens,0);
    initial.badId = false;await until(() => connection.session?.probe.device === initial);
    assert.equal(initial.writes.length,0);assert.equal(initial.resets,0);
    usb.devices = [other];initial.opened = false;connection.deviceChanged(initial);
    await until(() => !connection.session);
    const replacement = new VirtualJLink();usb.devices = [other,replacement];connection.deviceChanged(replacement);
    await until(() => connection.session?.probe.device === replacement);
    assert.equal(other.opens,0);assert.equal(replacement.writes.length,0);assert.equal(replacement.resets,0);
    await stop(connection);const queries = usb.queries;await sleep(1150);
    assert.equal(usb.queries,queries);assert.equal(replacement.opened,false);assert.equal(connection.enabled,false);
  } finally {await stop(connection);}
});

test('operation pause drains current probe IO and excludes polling until resume',async () => {
  const device = new VirtualJLink(),usb = {async getDevices() {return [device];}},connection = new Connection(usb,() => {});
  try {
    connection.connect(device);await until(() => connection.session);
    const core = await connection.pause(),before = device.commands.length;
    await sleep(1150);assert.equal(device.commands.length,before);
    assert.equal(await core.read32(0xe0042000),0x80584501);
    connection.resume();await until(() => device.commands.length > before + 5 && !connection.pending);
    assert.equal(device.resets,0);assert.equal(device.erased.length,0);
  } finally {await stop(connection);}
});

test('turning connection off during USB open cannot publish a late connection or restart retries',async () => {
  const device = new VirtualJLink();let release,opening = false;
  const opened = new Promise(resolve => {release = resolve;});
  const open = device.open.bind(device);device.open = async () => {opening = true;await opened;await open();};
  const states = [],usb = {queries:0,async getDevices() {this.queries++;return [device];}},connection = new Connection(usb,state => states.push(state));
  try {
    connection.connect(device);await until(() => opening);connection.disconnect();release();
    await until(() => !connection.pending && !device.opened);
    const queries = usb.queries;await sleep(1150);
    assert.equal(states.some(state => state.state === 'connected'),false);assert.equal(usb.queries,queries);
    assert.equal(device.writes.length,0);assert.equal(device.resets,0);
  } finally {release();await stop(connection);}
});
