import assert from "node:assert/strict";
import { mkdir, readFile } from "node:fs/promises";
import { chromium } from "playwright";

// Runs in a fresh isolated browser profile, never in the operator's browser.
const browser = await chromium.launch({
  ...(process.env.CHROME_PATH
    ? { executablePath: process.env.CHROME_PATH }
    : {}),
  headless: true,
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1050 },
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const baseUrl = process.env.TEST_URL || "http://127.0.0.1:5173/";
await mkdir("output", { recursive: true });

try {
  await page.goto(baseUrl);
  await page
    .getByRole("heading", { name: "试验工作台", exact: true })
    .waitFor();
  assert.equal(
    await page.getByText("所有读数与操作均为模拟，未连接真实设备").count(),
    1,
  );
  await page.screenshot({ path: "output/workbench-idle.png", fullPage: true });

  await page.getByRole("button", { name: "运行加载演示", exact: true }).click();
  await page.getByRole("button", { name: "开始记录", exact: true }).click();
  await page.getByRole("button", { name: "暂停曲线", exact: true }).click();
  const frozenPath = await page
    .locator(".chart-panel .plot path")
    .getAttribute("d");
  await page.waitForFunction(
    () =>
      Number(
        document
          .querySelector(".record-heading p")
          ?.textContent.match(/(\d+) 个数据点/)?.[1],
      ) >= 8,
  );
  assert.equal(
    await page.locator(".chart-panel .plot path").getAttribute("d"),
    frozenPath,
    "plot freezes while recording continues",
  );
  assert.equal(await page.getByLabel("设备量程配置").isDisabled(), true);

  await page.getByRole("button", { name: "设备设置", exact: true }).click();
  const sensor = page.locator(".device-setup").filter({
    has: page.getByRole("heading", { name: "扭矩传感器", exact: true }),
  });
  await sensor.getByRole("button", { name: "模拟断开", exact: true }).click();
  await page.waitForTimeout(450); // collect disconnected samples, not a UI readiness wait
  await page.getByRole("button", { name: "完成", exact: true }).click();
  assert.equal(
    await page.locator(".torque-reading .value > span").innerText(),
    "—",
  );
  await page.getByRole("button", { name: "停止记录", exact: true }).click();
  assert.equal(
    await page.getByRole("button", { name: "停止电机", exact: true }).count(),
    1,
    "ending a record does not stop motor",
  );
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 CSV", exact: true }).click();
  const download = await downloaded;
  assert.ok(download.suggestedFilename().startsWith("SIMULATION_"));
  const csv = await readFile(await download.path(), "utf8");
  assert.match(csv, /source,receivedAt/);
  assert.match(csv, /simulation,/);
  assert.ok(
    csv
      .split("\r\n")
      .some(
        (line) => line.startsWith("simulation,") && line.split(",")[3] === "",
      ),
    "sensor gap is empty in CSV",
  );

  await page.getByRole("button", { name: "停止电机", exact: true }).click();
  await page.getByRole("button", { name: "关闭制动输出", exact: true }).click();
  await page.getByLabel("设备量程配置").selectOption("100");
  assert.equal(
    await page.locator(".torque-reading .value > span").innerText(),
    "—",
    "profile changes preserve disconnected sensor",
  );
  await page.getByRole("button", { name: "设备设置", exact: true }).click();
  await sensor.getByRole("button", { name: "模拟连接", exact: true }).click();
  await page.getByRole("button", { name: "完成", exact: true }).click();
  await page.getByLabel(/^目标电流/).fill("0.504");
  assert.equal(
    await page
      .getByRole("button", { name: "应用电流", exact: true })
      .isDisabled(),
    true,
  );
  await page.getByLabel(/^目标电流/).fill("0.8");
  await page.getByRole("button", { name: "应用电流", exact: true }).click();
  assert.equal(
    await page
      .getByRole("button", { name: "开启模拟制动输出", exact: true })
      .count(),
    1,
    "applying current does not energize output",
  );
  await page.getByRole("button", { name: "开始记录", exact: true }).click();
  assert.equal(
    await page
      .getByRole("button", { name: "导出 CSV", exact: true })
      .isDisabled(),
    true,
    "current record cannot export the prior run",
  );
  await page.getByRole("button", { name: "停止记录", exact: true }).click();

  await page.reload();
  await page.getByRole("button", { name: /^试验记录/ }).click();
  assert.equal(
    await page.locator(".records-table tbody tr").count(),
    2,
    "finished records survive reload",
  );
  await page.locator(".record-name").first().click();
  await page
    .getByRole("button", { name: "关闭记录详情", exact: true })
    .waitFor();
  await page.screenshot({ path: "output/records-desktop.png", fullPage: true });
  await page.getByRole("button", { name: "试验工作台", exact: true }).click();
  await page.getByRole("button", { name: "运行加载演示", exact: true }).click();
  await page.waitForFunction(
    () =>
      Number(
        document.querySelector(".torque-reading .value > span")?.textContent,
      ) > 10,
  );
  // Exercise applied setpoints and capture an actual response trace, rather
  // than inserting fabricated chart history for the layout screenshot.
  await page.waitForFunction(
    () =>
      Number(
        document.querySelectorAll(".readings .value > span")[1].textContent,
      ) > 599,
  );
  await page.getByLabel(/^目标电流/).fill("0.6");
  await page.getByRole("button", { name: "应用电流", exact: true }).click();
  await page.waitForFunction(
    () =>
      Number(
        document.querySelectorAll(".readings .value > span")[2].textContent,
      ) < 0.602,
  );
  await page.getByLabel(/^目标电流/).fill("1.6");
  await page.getByRole("button", { name: "应用电流", exact: true }).click();
  await page.waitForFunction(
    () =>
      Number(
        document.querySelectorAll(".readings .value > span")[2].textContent,
      ) > 1.598,
  );
  await page.screenshot({
    path: "output/workbench-desktop.png",
    fullPage: true,
  });
  for (const width of [1440, 1024, 768, 390]) {
    await page.setViewportSize({ width, height: width === 1440 ? 900 : 1000 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      true,
      `no horizontal page overflow at ${width}px`,
    );
    if (width === 1440) {
      assert.ok(
        (
          await page
            .getByRole("button", { name: "开始记录", exact: true })
            .boundingBox()
        ).y < 865,
        "record action is visible on a 900px-high desktop",
      );
      await page.screenshot({
        path: "output/workbench-desktop.png",
        fullPage: true,
      });
    }
    if (width === 390)
      await page.screenshot({
        path: "output/workbench-mobile.png",
        fullPage: true,
      });
  }
  assert.deepEqual(errors, [], "no browser runtime errors");
  console.log(
    "UI smoke passed: recording, CSV, disconnect gaps, paused view, profiles, precision, persistence and 4 viewport widths.",
  );
} finally {
  await context.close();
  await browser.close();
}
