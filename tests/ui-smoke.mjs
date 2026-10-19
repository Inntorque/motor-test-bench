import assert from "node:assert/strict";
import { chromium } from "playwright";

const browser = await chromium.launch({
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
  headless: true,
});
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const baseUrl = process.env.TEST_URL || "http://127.0.0.1:5173/";

try {
  await page.goto(baseUrl);
  await page.getByRole("heading", { name: "扭矩工作台", exact: true }).waitFor();
  assert.equal(await page.locator(".hw-supply-card").count(), 3, "two separate PSU panels and the independent sensor panel are present");
  assert.equal(await page.getByRole("region", { name: "动态扭矩传感器连接", exact: true }).count(), 1);
  assert.equal(await page.getByRole("button", { name: "连接并验证", exact: true }).count(), 3);
  const sensor = page.getByRole("region", { name: "动态扭矩传感器连接", exact: true });
  assert.equal(await sensor.getByLabel("USB-RS485 串口").locator("option").allTextContents().then((items) => items.some((item) => /CAN|SLCAN/i.test(item))), false, "CAN adapters are not offered as torque sensor ports");
  assert.equal(await sensor.getByRole("button", { name: "连接并验证", exact: true }).isDisabled(), true, "sensor requires an explicit serial port selection");
  assert.equal(await page.locator(".connections .connection-status").count(), 4);
  assert.deepEqual(
    (await page.locator(".connections .connection-status").allTextContents()).map((text) => text.trim()),
    ["未连接", "未连接", "未连接", "未连接"],
  );
  assert.equal(await page.getByRole("button", { name: "运行加载演示", exact: true }).count(), 0);
  assert.equal(await page.getByText("暂无真实采集数据", { exact: true }).count(), 1);
  assert.equal(await page.getByRole("button", { name: "开始记录", exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole("button", { name: "CAN 控制协议尚未接入" }).count(), 0);
  for (const button of await page.getByRole("button", { name: "ON · 应用后开启", exact: true }).all())
    assert.equal(await button.isEnabled(), false, "disconnected PSU output stays disabled");

  const motorSupply = page.getByRole("region", { name: "电机电源控制", exact: true });
  const brakeSupply = page.getByRole("region", { name: "制动电源控制", exact: true });
  await motorSupply.getByRole("button", { name: "收起电机电源控制", exact: true }).click();
  assert.equal(await motorSupply.locator("#motor-supply-controls").isHidden(), true, "collapsing hides the motor PSU controls");
  assert.equal(await motorSupply.getByLabel("电机电源实时读数").isVisible(), true, "live PSU readings stay visible while collapsed");
  assert.equal(await brakeSupply.locator("#brake-supply-controls").isVisible(), true, "each PSU collapses independently");
  await motorSupply.getByRole("button", { name: "展开电机电源控制", exact: true }).click();
  assert.equal(await motorSupply.locator("#motor-supply-controls").isVisible(), true, "PSU controls can be expanded again");

  await page.getByRole("button", { name: "设备设置", exact: true }).click();
  await page.getByRole("dialog", { name: "设备与连接" }).waitFor();
  assert.equal(await page.getByRole("button", { name: "模拟连接" }).count(), 0);
  assert.equal(await page.getByRole("button", { name: "模拟断开" }).count(), 0);
  await page.getByRole("button", { name: "完成", exact: true }).click();

  for (const width of [1440, 1024, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      true,
      `no horizontal overflow at ${width}px`,
    );
  }
  assert.deepEqual(errors, [], "no browser runtime errors");
  console.log("Workbench smoke passed: restored plots/records, two PSU panels, read-only torque sensor integration, all offline by default and no simulated connection action.");
} finally {
  await context.close();
  await browser.close();
}
