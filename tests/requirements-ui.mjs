import assert from "node:assert/strict";
import { mkdir, readFile } from "node:fs/promises";
import { chromium } from "playwright";
import { createDemoState, sampleFromState } from "../web/src/model.js";
import { STORE_KEY, readRecords } from "../web/src/records.js";

// Fresh, isolated browser profiles only; no operator tabs or hardware access.
const started = performance.now();
const browser = await chromium.launch({
  ...(process.env.CHROME_PATH
    ? { executablePath: process.env.CHROME_PATH }
    : {}),
  headless: true,
});
const baseUrl = process.env.TEST_URL || "http://127.0.0.1:5173/";
const errors = [];
await mkdir("output", { recursive: true });

async function newContext() {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  context.setDefaultTimeout(6000);
  context.on("page", (page) => {
    page.on("pageerror", (error) => errors.push(error.message));
  });
  return context;
}

async function openPage(context) {
  const page = await context.newPage();
  await page.goto(baseUrl);
  await page
    .getByRole("heading", { name: "试验工作台", exact: true })
    .waitFor();
  return page;
}

async function seedRecords(page, raw) {
  await page.evaluate(({ key, value }) => localStorage.setItem(key, value), {
    key: STORE_KEY,
    value: raw,
  });
  await page.reload();
  await page
    .getByRole("heading", { name: "试验工作台", exact: true })
    .waitFor();
}

const button = (page, name) => page.getByRole("button", { name, exact: true });
const status = (page) => page.locator(".bench-status");
const stopAll = (page) =>
  status(page).getByRole("button", { name: "停止全部模拟输出", exact: true });

async function sensorConnection(page, connect) {
  await button(page, "设备设置").click();
  const sensor = page.locator(".device-setup").filter({
    has: page.getByRole("heading", { name: "动态扭矩传感器", exact: true }),
  });
  await sensor
    .getByRole("button", {
      name: connect ? "模拟连接" : "模拟断开",
      exact: true,
    })
    .click();
  await button(page, "完成").click();
}

async function atLeastSamples(page, count) {
  await page.waitForFunction(
    (minimum) =>
      Number(
        document
          .querySelector(".record-heading p")
          ?.textContent.match(/(\d+) 个数据点/)?.[1],
      ) >= minimum,
    count,
  );
}

async function finishRecording(page) {
  await button(page, "停止记录").click();
  await page.waitForFunction(() => {
    const button = [
      ...document.querySelectorAll(".record-controls button"),
    ].find((element) => element.textContent.trim() === "开始记录");
    return (
      button &&
      !button.disabled &&
      !document.querySelector(".bench-status")?.textContent.includes("正在保存")
    );
  });
}

async function exportText(page, action) {
  const downloading = page.waitForEvent("download");
  await action.click();
  const download = await downloading;
  return {
    name: download.suggestedFilename(),
    text: await readFile(await download.path(), "utf8"),
  };
}

try {
  const context = await newContext();
  const page = await openPage(context);
  const speed = page.getByLabel(/^电机目标转速/);
  const current = page.getByLabel(/^目标电流/);

  // Draft values cannot silently start an output using an older applied value.
  await speed.fill("850");
  assert.equal(await button(page, "启动模拟电机").isDisabled(), true);
  await speed.fill("-1");
  assert.equal(await button(page, "启动模拟电机").isDisabled(), true);
  await speed.fill("850");
  await button(page, "应用转速").click();
  await button(page, "启动模拟电机").click();
  await speed.fill("-1");
  assert.equal(await button(page, "停止电机").isEnabled(), true);
  await button(page, "停止电机").click();
  await current.fill("0.8");
  assert.equal(await button(page, "开启模拟制动输出").isDisabled(), true);
  await current.fill("0.805");
  assert.equal(await button(page, "开启模拟制动输出").isDisabled(), true);
  await current.fill("0.8");
  await button(page, "应用电流").click();
  await button(page, "开启模拟制动输出").click();
  await current.fill("0.805");
  assert.equal(await button(page, "关闭制动输出").isEnabled(), true);
  await button(page, "关闭制动输出").click();

  // First-run convenience must respect a deliberately disconnected channel.
  await page.reload();
  await sensorConnection(page, false);
  assert.equal(await button(page, "运行加载演示").isDisabled(), true);
  assert.match(
    await page.locator(".connection-alert").innerText(),
    /扭矩和转速不可用/,
  );
  assert.match(await status(page).innerText(), /未使能/);
  assert.match(await status(page).innerText(), /输出关闭/);
  assert.equal(await button(page, "开始记录").isDisabled(), true);
  await sensorConnection(page, true);
  await button(page, "运行加载演示").click();
  await page.getByLabel("试验名称").fill("需求检查 · 加载与回落");
  await button(page, "开始记录").click();
  await atLeastSamples(page, 3);
  await current.fill("0.7");
  await button(page, "应用电流").click();
  await sensorConnection(page, false);
  const countBeforeGap = Number(
    (await page.locator(".record-heading p").innerText()).match(
      /(\d+) 个数据点/,
    )[1],
  );
  await atLeastSamples(page, countBeforeGap + 1);
  assert.match(
    await page.locator(".connection-alert").innerText(),
    /其他模拟输出仍在运行/,
  );
  await sensorConnection(page, true);

  // Reviewing earlier trials must leave current output controls accessible.
  await page.getByRole("button", { name: /^试验记录/ }).click();
  assert.equal(await stopAll(page).isEnabled(), true);
  await stopAll(page).click();
  assert.match(await status(page).innerText(), /未使能/);
  assert.match(await status(page).innerText(), /输出关闭/);
  assert.match(await status(page).innerText(), /采集中/);
  await button(page, "返回正在记录的试验").click();
  await button(page, "启动模拟电机").click();
  await button(page, "开启模拟制动输出").click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  const sticky = await status(page).boundingBox();
  assert.ok(
    sticky.y >= 0 && sticky.y + sticky.height < 844,
    "global controls remain visible after mobile scrolling",
  );
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  await stopAll(page).click();
  assert.equal(await stopAll(page).isDisabled(), true);
  assert.match(await status(page).innerText(), /采集中/);
  const stoppedCount = Number(
    (await page.locator(".record-heading p").innerText()).match(
      /(\d+) 个数据点/,
    )[1],
  );
  await atLeastSamples(page, stoppedCount + 1);
  await page.screenshot({ path: "output/audit-final-mobile-controls.png" });
  await page.setViewportSize({ width: 1440, height: 900 });
  await finishRecording(page);
  const saved = await page.evaluate(
    (key) => JSON.parse(localStorage.getItem(key)),
    STORE_KEY,
  );
  assert.equal(saved.length, 1);
  const trial = saved[0];
  assert.equal(trial.samples[0].t, 0);
  assert.ok(trial.duration > 0 && trial.duration >= trial.samples.at(-1).t);
  assert.equal(trial.config.source, "simulation");
  assert.equal(trial.config.motorTargetSpeedRpm, 600);
  assert.equal(trial.config.brakeTargetCurrentA, 1.2);
  assert.ok(trial.events.some((event) => /0.7 A/.test(event.text)));
  assert.ok(trial.events.some((event) => /全部模拟输出/.test(event.text)));
  assert.ok(trial.samples.some((sample) => sample.torque === null));
  assert.ok(
    trial.samples.some((sample) => !sample.motorEnabled && !sample.loadEnabled),
    "recording captures the output stop independently",
  );
  assert.ok(
    trial.events.every(
      (event, index) =>
        event.t >= 0 && (!index || event.t >= trial.events[index - 1].t),
    ),
  );
  const csv = await exportText(page, button(page, "导出 CSV"));
  assert.match(csv.name, /^SIMULATION_/);
  assert.equal(csv.text.split("\r\n")[1].split(",")[2], "0");

  await page.getByRole("button", { name: /^试验记录/ }).click();
  await page.locator(".record-name").first().click();
  const metrics = page.locator(".history-details .metric-tabs");
  assert.equal(await metrics.getByRole("button").count(), 3);
  await metrics.getByRole("button", { name: /^转速/ }).click();
  assert.match(
    await page.locator(".history-details .plot").getAttribute("aria-label"),
    /输出转速/,
  );
  await metrics.getByRole("button", { name: /^制动电流/ }).click();
  assert.match(
    await page.locator(".history-details .plot").getAttribute("aria-label"),
    /制动加载电流/,
  );
  await metrics.getByRole("button", { name: /^扭矩/ }).click();
  await page.locator(".record-events summary").click();
  const eventsCsv = await exportText(page, button(page, "导出事件 CSV"));
  assert.match(eventsCsv.name, /^SIMULATION_EVENTS_/);
  assert.match(eventsCsv.text, /source,receivedAt,elapsedSeconds,event/);
  assert.match(eventsCsv.text, /全部模拟输出/);
  assert.match(await page.locator(".record-config").innerText(), /600 rpm/);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "output/audit-final-history.png",
    fullPage: true,
  });
  await context.close();

  // Legacy elapsed times remain readable, and negative torque has real area
  // below the zero axis instead of being clamped into a flat zero line.
  const fixtureStart = Date.now() - 5000;
  const negativeTrial = {
    id: "legacy-negative-torque",
    name: "正反向扭矩历史检查",
    range: 50,
    startedAt: fixtureStart,
    finishedAt: fixtureStart + 3000,
    duration: 3,
    samples: [-40, -10, 0, 30].map((torque, index) =>
      sampleFromState(
        {
          ...createDemoState(),
          t: 10 + index,
          torque,
          speed: 120,
          current: 0.5,
        },
        fixtureStart + index * 1000,
      ),
    ),
  };
  const fixtureRaw = JSON.stringify([negativeTrial]);
  assert.equal(
    readRecords({ getItem: () => fixtureRaw }).blocked,
    false,
    "history fixture must satisfy production validation",
  );
  const historyContext = await newContext();
  const historyPage = await openPage(historyContext);
  await seedRecords(historyPage, fixtureRaw);
  await historyPage.getByRole("button", { name: /^试验记录/ }).click();
  await historyPage.locator(".record-name").click();
  const geometry = await historyPage
    .locator(".history-details .plot")
    .evaluate((svg) => {
      const zeroTick = [...svg.querySelectorAll("g")].find(
        (group) => group.querySelector("text")?.textContent.trim() === "0",
      );
      return {
        zero: Number(zeroTick.querySelector("line").getAttribute("y1")),
        points: [
          ...svg
            .querySelector("path")
            .getAttribute("d")
            .matchAll(/[ML]([\d.-]+),([\d.-]+)/g),
        ].map((match) => ({ x: Number(match[1]), y: Number(match[2]) })),
      };
    });
  assert.equal(geometry.points.length, 4);
  assert.ok(geometry.points[0].y > geometry.points[1].y);
  assert.ok(geometry.points[1].y > geometry.zero);
  assert.ok(Math.abs(geometry.points[2].y - geometry.zero) < 0.02);
  assert.ok(geometry.points[3].y < geometry.zero);
  const shortTicks = await historyPage
    .locator(".history-details .plot text")
    .allTextContents();
  assert.ok(
    shortTicks.includes("0.0 s"),
    "short trial axes use fractional seconds",
  );
  assert.ok(shortTicks.includes("3.0 s"));
  await historyPage.screenshot({
    path: "output/audit-final-negative-torque.png",
    fullPage: true,
  });
  await historyContext.close();

  // Existing damaged data stays untouched; a new in-memory trial still exports.
  const corruptContext = await newContext();
  const corruptPage = await openPage(corruptContext);
  const damagedRaw = JSON.stringify([
    negativeTrial,
    { id: "bad-record", samples: [] },
  ]);
  await seedRecords(corruptPage, damagedRaw);
  assert.match(
    await corruptPage.locator(".storage-error").innerText(),
    /原始数据已保留/,
  );
  await corruptPage.getByLabel("试验名称").fill("坏存储后的可导出记录");
  await button(corruptPage, "开始记录").click();
  await atLeastSamples(corruptPage, 2);
  await finishRecording(corruptPage);
  assert.equal(
    await corruptPage.evaluate((key) => localStorage.getItem(key), STORE_KEY),
    damagedRaw,
  );
  const rescued = await exportText(
    corruptPage,
    button(corruptPage, "导出 CSV"),
  );
  assert.match(rescued.text, /gearboxOutputTorqueNm/);
  assert.ok(
    rescued.text.split("\r\n").filter((line) => line.startsWith("simulation,"))
      .length >= 2,
  );
  await corruptPage.getByRole("button", { name: /^试验记录/ }).click();
  assert.equal(await corruptPage.locator(".record-name").count(), 2);
  await corruptContext.close();

  // Two tabs begin with the same empty snapshot and finish concurrently.
  const multiContext = await newContext();
  const [left, right] = await Promise.all([
    openPage(multiContext),
    openPage(multiContext),
  ]);
  await Promise.all([
    left.getByLabel("试验名称").fill("独立页面 A"),
    right.getByLabel("试验名称").fill("独立页面 B"),
  ]);
  await Promise.all([
    button(left, "开始记录").click(),
    button(right, "开始记录").click(),
  ]);
  await Promise.all([atLeastSamples(left, 2), atLeastSamples(right, 2)]);
  await Promise.all([finishRecording(left), finishRecording(right)]);
  const combined = await left.evaluate(
    (key) => JSON.parse(localStorage.getItem(key)),
    STORE_KEY,
  );
  assert.equal(combined.length, 2);
  assert.deepEqual(combined.map((record) => record.name).sort(), [
    "独立页面 A",
    "独立页面 B",
  ]);
  assert.equal(new Set(combined.map((record) => record.id)).size, 2);
  for (const tab of [left, right]) {
    await tab.getByRole("button", { name: /^试验记录/ }).click();
    await tab.waitForFunction(
      () => document.querySelectorAll(".record-name").length === 2,
    );
  }
  await left.evaluate((key) => localStorage.removeItem(key), STORE_KEY);
  await right
    .getByRole("alert")
    .filter({
      hasText: "本地存储在其他页面发生变化",
    })
    .waitFor();
  assert.equal(
    await right.locator(".record-name").count(),
    2,
    "externally removed storage does not discard in-memory trials",
  );
  assert.equal(
    await right.evaluate((key) => localStorage.getItem(key), STORE_KEY),
    null,
  );
  await multiContext.close();

  // Advance a browser-controlled clock to exercise the automatic stop/save
  // branch without waiting five real minutes or claiming a hardware soak test.
  const limitContext = await newContext();
  const limitPage = await limitContext.newPage();
  await limitPage.clock.install();
  await limitPage.goto(baseUrl);
  await button(limitPage, "运行加载演示").click();
  await button(limitPage, "开始记录").click();
  await limitPage.clock.runFor(300000);
  await limitPage.waitForFunction(
    (key) => JSON.parse(localStorage.getItem(key) || "[]").length === 1,
    STORE_KEY,
  );
  const capped = await limitPage.evaluate(
    (key) => JSON.parse(localStorage.getItem(key))[0],
    STORE_KEY,
  );
  assert.equal(capped.samples.length, 1500);
  assert.equal(capped.samples[0].t, 0);
  assert.match(capped.events.at(-1).text, /1500/);
  assert.equal(await button(limitPage, "停止记录").count(), 0);
  assert.equal(await button(limitPage, "停止电机").count(), 1);
  await limitContext.close();
  assert.deepEqual(
    errors,
    [],
    "no browser runtime errors across all scenarios",
  );
  console.log(
    `Requirements UI checks passed (8 scenarios, ${((performance.now() - started) / 1000).toFixed(1)}s).`,
  );
} finally {
  await browser.close();
}
