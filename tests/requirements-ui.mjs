import assert from "node:assert/strict";
import { chromium } from "playwright";

const legacy = {
  id: "legacy-ui-record",
  name: "历史演示记录",
  range: 50,
  startedAt: "2026-10-09T00:00:00.000Z",
  finishedAt: "2026-10-09T00:00:01.000Z",
  duration: 1,
  samples: [{
    source: "simulation",
    receivedAt: "2026-10-09T00:00:00.000Z",
    t: 0,
    torque: 0,
    speed: 0,
    current: 0,
    targetSpeed: 300,
    targetCurrent: 0.5,
    motorEnabled: false,
    loadEnabled: false,
    range: 50,
  }],
};

const browser = await chromium.launch({
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
  headless: true,
});
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
await context.addInitScript((record) => localStorage.setItem("inntorque.demo-recordings.v1", JSON.stringify([record])), legacy);
const page = await context.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const baseUrl = process.env.TEST_URL || "http://127.0.0.1:5173/";

try {
  await page.goto(baseUrl);
  await page.getByRole("heading", { name: "扭矩工作台", exact: true }).waitFor();
  const motor = page.getByRole("region", { name: "电机电源控制", exact: true });
  const brake = page.getByRole("region", { name: "制动电源控制", exact: true });
  await motor.getByLabel("传输方式").selectOption("tcp");
  await motor.getByLabel("网关地址").fill("192.168.10.21");
  await motor.getByLabel("网关 TCP 端口").fill("4001");
  await motor.getByLabel("Modbus 地址").fill("2");
  await brake.getByLabel("Modbus 地址").fill("1");
  assert.equal(await motor.getByLabel("网关地址").inputValue(), "192.168.10.21");
  assert.equal(await brake.getByLabel("传输方式").inputValue(), "serial");
  assert.equal(await motor.getByLabel("Modbus 地址").inputValue(), "2");
  assert.equal(await brake.getByLabel("Modbus 地址").inputValue(), "1");

  assert.equal(await page.getByRole("heading", { name: "实时曲线", exact: true }).count(), 1);
  assert.equal(await page.getByRole("region", { name: "试验记录操作" }).count(), 1);
  assert.equal(await page.getByText("操作事件", { exact: true }).count(), 1);
  assert.equal(await page.getByRole("button", { name: "连接并验证", exact: true }).count(), 3);
  assert.equal(await page.getByRole("button", { name: "模拟连接", exact: true }).count(), 0);

  await page.getByRole("button", { name: /试验记录/ }).click();
  await page.getByRole("button", { name: "历史演示记录", exact: true }).waitFor();
  assert.equal(await page.getByText("历史演示数据", { exact: true }).count(), 1);
  assert.deepEqual(errors, [], "no browser runtime errors");
  console.log("Requirements UI passed: separate PSU settings, restored power-test workflow, no simulated connect path, and legacy records remain visible as historical data.");
} finally {
  await context.close();
  await browser.close();
}
