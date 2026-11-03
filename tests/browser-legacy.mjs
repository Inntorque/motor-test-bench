import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {LegacyApp} from '../browser/legacy.ts';
const fixture = JSON.parse(await readFile(new URL('./fixtures/browser-legacy.json',import.meta.url)));
for (const [app,frames] of Object.entries(fixture.apps)) {
  test(`${app} browser feedback matches the unchanged Python decoder wire fixtures`,async t => {
    const source = await readFile(new URL(`../gateway/hosts/${app}/scripts/foc_web.py`,import.meta.url));
    assert.equal(createHash('sha256').update(source).digest('hex'),fixture.sourceSha256[app]);
    t.mock.method(Date,'now',() => fixture.sourceTimeUnix * 1000);
    const engine = new LegacyApp(app,{}, {},() => {});
    for (const frame of frames) {
      engine.receive(frame.id,Uint8Array.from(Buffer.from(frame.hex,'hex')));
      const status = engine.status();
      for (const [key,value] of Object.entries(frame.expected)) assert.deepEqual(status[key],value,`${app} ${frame.name}: ${key}`);
    }
  });
}
