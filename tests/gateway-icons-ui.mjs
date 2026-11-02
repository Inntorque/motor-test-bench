import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {resolve, dirname, extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
import {Box3, Vector3} from 'three';
import {createIconScene} from '../gateway/graphics/icon-scene.js';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const output = resolve(root, '.runtime/img2threejs-icons/pixel-motion');
await mkdir(output, {recursive: true});
const movingRegions = Object.entries({
  dynamometer: ['output-shaft', 'shaft-end-chamfer', 'shaft-keyway', 'shaft-index-mark'],
  'sg-6010hb': ['radial-face-sectors', 'rotor-index-mark', 'inner-rotor-ring', 'output-shaft', 'shaft-end-chamfer', 'shaft-keyway', 'shaft-index-mark'],
  'rm-7010': ['annular-face', 'face-vent-sectors'],
}).map(([id, names]) => {
  const {scene, camera} = createIconScene(id);
  const bounds = new Box3();
  for (const name of names) bounds.union(new Box3().setFromObject(scene.getObjectByName(name)));
  const points = [];
  for (const x of [bounds.min.x, bounds.max.x]) {
    for (const y of [bounds.min.y, bounds.max.y]) {
      for (const z of [bounds.min.z, bounds.max.z]) points.push(new Vector3(x, y, z).project(camera));
    }
  }
  return {
    left: Math.floor(Math.min(...points.map(p => (p.x + 1) * 120))) - 3,
    right: Math.ceil(Math.max(...points.map(p => (p.x + 1) * 120))) + 3,
    top: Math.floor(Math.min(...points.map(p => (1 - p.y) * 120))) - 3,
    bottom: Math.ceil(Math.max(...points.map(p => (1 - p.y) * 120))) + 3,
  };
});
const types = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png'};
const server = createServer(async (request, response) => {
  const path = new URL(request.url, 'http://localhost').pathname;
  try {
    const file = resolve(root, 'site', '.' + (path === '/' ? '/index.html' : path));
    response.setHeader('Content-Type', types[extname(file)] || 'application/octet-stream');
    response.end(await readFile(file));
  } catch {
    response.writeHead(404);
    response.end();
  }
});
await new Promise(done => server.listen(0, '127.0.0.1', done));
let browser;
try {
  browser = await chromium.launch({headless: true, ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {})});
  const page = await browser.newPage({viewport: {width: 1200, height: 800}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    const getContext = HTMLCanvasElement.prototype.getContext;
    const drawImage = CanvasRenderingContext2D.prototype.drawImage;
    window.iconProbe = {webgl: new Set(), frames: 0, hidden: false};
    HTMLCanvasElement.prototype.getContext = function(type, ...args) {
      if (type === 'webgl2') window.iconProbe.webgl.add(this);
      return getContext.call(this, type, ...args);
    };
    CanvasRenderingContext2D.prototype.drawImage = function(...args) {
      if (this.canvas.closest('.app-icon')) window.iconProbe.frames++;
      return drawImage.apply(this, args);
    };
    Object.defineProperty(document, 'hidden', {get: () => window.iconProbe.hidden});
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.waitForFunction(() => document.querySelectorAll('.is-animated').length === 3);
  const info = await page.evaluate(() => ({
    contexts: window.iconProbe.webgl.size,
    sizes: [...document.querySelectorAll('.app-icon canvas')].map(c => [c.width, c.height]),
    pixels: [...document.querySelectorAll('.app-icon canvas')].every(c => getComputedStyle(c).imageRendering === 'pixelated'),
    names: [...document.querySelectorAll('.app-name')].map(n => n.textContent),
  }));
  assert.equal(info.contexts, 1);
  assert.deepEqual(info.sizes, [[240,240], [240,240], [240,240]]);
  assert.equal(info.pixels, true);
  assert.deepEqual(info.names, ['测功机', 'SG-6010HB', '7010 上位机']);
  const images = await page.locator('.app-icon canvas').evaluateAll(cs => cs.map(c => c.toDataURL()));
  assert.equal(new Set(images).size, 3, 'shared renderer must preserve three distinct motors');
  const idle = await page.evaluate(() => window.iconProbe.frames);
  await page.waitForTimeout(400);
  assert.equal(await page.evaluate(() => window.iconProbe.frames), idle, 'idle motors must not keep drawing');
  assert.deepEqual(await page.locator('.app-icon canvas').evaluateAll(cs => cs.map(c => c.toDataURL())), images);
  await page.screenshot({path: resolve(output, 'gateway.png')});
  for (let i = 0; i < images.length; i++) {
    await writeFile(resolve(output, `icon-${i}.png`), Buffer.from(images[i].split(',')[1], 'base64'));
  }

  const rates = [];
  for (let index = 0; index < 3; index++) {
    const before = await page.locator('.app-icon canvas').evaluateAll(cs => cs.map(c => c.toDataURL()));
    await page.evaluate(() => {
      window.iconProbe.pixels = [...document.querySelectorAll('.app-icon canvas')].map(c => c.getContext('2d').getImageData(0, 0, 240, 240).data);
    });
    const position = await page.locator('.app-icon').nth(index).boundingBox();
    await page.locator('.app').nth(index).hover();
    await page.waitForFunction(index => {
      const canvas = document.querySelectorAll('.app-icon canvas')[index];
      return Math.abs(canvas.getBoundingClientRect().width / parseFloat(getComputedStyle(canvas).width) - 1.08) < .001;
    }, index);
    await page.waitForFunction(({index, image}) => document.querySelectorAll('.app-icon canvas')[index].toDataURL() !== image, {index, image: before[index]});
    assert.deepEqual(await page.locator('.app-icon').nth(index).boundingBox(), position, 'APP must not lift or move on hover');
    const start = await page.evaluate(() => ({frames: window.iconProbe.frames, time: performance.now()}));
    await page.waitForTimeout(700);
    const end = await page.evaluate(() => ({frames: window.iconProbe.frames, time: performance.now()}));
    const fps = (end.frames - start.frames) / ((end.time - start.time) / 1000);
    assert.ok(fps > 0 && fps <= 26, `unexpected draw rate: ${fps}`);
    rates.push(Number(fps.toFixed(1)));
    await page.screenshot({path: resolve(output, `gateway-hover-${index}.png`)});
    const after = await page.locator('.app-icon canvas').evaluateAll(cs => cs.map(c => c.toDataURL()));
    for (let other = 0; other < 3; other++) if (other !== index) assert.equal(after[other], before[other], 'other APPs must stay frozen');
    const changedOutside = await page.evaluate(({index, region}) => {
      const initial = window.iconProbe.pixels[index];
      const current = document.querySelectorAll('.app-icon canvas')[index].getContext('2d').getImageData(0, 0, 240, 240).data;
      let changed = 0;
      for (let y = 0; y < 240; y++) {
        for (let x = 0; x < 240; x++) {
          if (x >= region.left && x <= region.right && y >= region.top && y <= region.bottom) continue;
          const offset = (y * 240 + x) * 4;
          for (let channel = 0; channel < 4; channel++) if (initial[offset + channel] !== current[offset + channel]) changed++;
        }
      }
      return changed;
    }, {index, region: movingRegions[index]});
    assert.equal(changedOutside, 0, 'housing and camera must remain fixed outside the shaft/rotor region');
    await page.mouse.move(1, 1);
    const stopped = await page.evaluate(() => window.iconProbe.frames);
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => window.iconProbe.frames), stopped, 'pointer leave must stop drawing');
    assert.equal(await page.locator('.app-icon canvas').nth(index).evaluate(c => getComputedStyle(c).transform), 'none', 'pointer leave must restore normal size');
  }

  await page.locator('.app').first().hover();
  await page.evaluate(() => {window.iconProbe.hidden = true; document.dispatchEvent(new Event('visibilitychange'));});
  const paused = await page.evaluate(() => window.iconProbe.frames);
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(() => window.iconProbe.frames), paused, 'hidden page must stop drawing');
  await page.evaluate(() => {window.iconProbe.hidden = false; document.dispatchEvent(new Event('visibilitychange'));});
  await page.waitForFunction(before => window.iconProbe.frames > before, paused);
  await page.locator('.apps').evaluate(nav => {nav.style.transform = 'translateY(150vh)';});
  await page.waitForTimeout(200);
  const offscreen = await page.evaluate(() => window.iconProbe.frames);
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(() => window.iconProbe.frames), offscreen, 'offscreen icons must stop drawing');
  await page.locator('.apps').evaluate(nav => {nav.style.transform = '';});
  await page.locator('.app').first().hover();
  await page.waitForFunction(before => window.iconProbe.frames > before, offscreen);
  await page.emulateMedia({reducedMotion: 'reduce'});
  await page.waitForTimeout(200);
  const reducedFrames = await page.evaluate(() => window.iconProbe.frames);
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(() => window.iconProbe.frames), reducedFrames, 'reduced motion must stop continuous drawing');
  await page.emulateMedia({reducedMotion: 'no-preference'});
  await page.waitForFunction(before => window.iconProbe.frames > before, reducedFrames);
  await page.mouse.move(1, 1);
  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert.deepEqual(await page.locator('.app-icon canvas').evaluateAll(cs => cs.map(c => [c.width, c.height])), info.sizes);
  await page.screenshot({path: resolve(output, 'gateway-mobile.png')});
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({passed: true, contexts: info.contexts, sizes: info.sizes, hoverFps: rates, hoverScale: 1.08, fixedTile: true, idleStatic: true, hoverOnly: true, fixedHousingAndCamera: true, pointerLeaveStops: true, hiddenPause: true, offscreenPause: true, reducedMotion: true, responsive: true, errors}, null, 2));
} finally {
  await browser?.close();
  await new Promise(done => server.close(done));
}
