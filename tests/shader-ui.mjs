import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";

// An isolated software GPU makes the actual shader path testable on CI machines.
const browser = await chromium.launch({
  ...(process.env.CHROME_PATH
    ? { executablePath: process.env.CHROME_PATH }
    : {}),
  headless: true,
  args: [
    "--enable-unsafe-webgpu",
    "--use-angle=swiftshader",
    "--enable-features=Vulkan",
    "--use-vulkan=swiftshader",
  ],
});
const baseUrl = process.env.TEST_URL || "http://127.0.0.1:5173/";
const errors = [];
const externalRequests = [];
const shaderRequests = (requests) =>
  requests.filter((url) => /shaders|\/js-[^/]+\.js/.test(url));
const createContext = async (options = {}) => {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    ...options,
  });
  context.on("page", (page) => {
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (request) => {
      if (new URL(request.url()).origin !== new URL(baseUrl).origin)
        externalRequests.push(request.url());
    });
  });
  return context;
};

await mkdir("output", { recursive: true });
try {
  const context = await createContext();
  // Count real submissions so a frozen effect cannot masquerade as a paused GPU.
  await context.addInitScript(() => {
    if (typeof GPUQueue === "undefined") return;
    window.shaderSubmits = 0;
    const submit = GPUQueue.prototype.submit;
    GPUQueue.prototype.submit = function (commands) {
      window.shaderSubmits++;
      return submit.call(this, commands);
    };
  });
  const page = await context.newPage();
  await page.goto(baseUrl);
  await page.locator('.torque-surface[data-state="running"]').waitFor();
  const card = page.locator(".torque-reading");
  const first = await card.screenshot();
  await page.waitForTimeout(1800); // Sample an actual animation interval.
  const second = await card.screenshot();
  assert.notDeepEqual(first, second, "the real shader renders changing pixels");

  await page.getByRole("button", { name: "开始记录", exact: true }).click();
  await page.locator('.torque-surface[data-state="paused"]').waitFor();
  await page.waitForTimeout(300); // Flush a submitted GPU frame before comparison.
  const pausedSubmits = await page.evaluate(() => window.shaderSubmits);
  await page.waitForTimeout(1000);
  assert.equal(
    await page.evaluate(() => window.shaderSubmits),
    pausedSubmits,
    "recording stops GPU animation submissions",
  );
  assert.match(
    await page.locator(".record-heading p").innerText(),
    /[1-9]\d* 个数据点/,
  );
  await page.getByRole("button", { name: "停止记录", exact: true }).click();
  await page.locator('.torque-surface[data-state="running"]').waitFor();

  await page.getByRole("button", { name: "视觉动效", exact: true }).click();
  await page
    .locator('.torque-surface[data-enabled="false"][data-state="paused"]')
    .waitFor();
  await page.reload();
  assert.equal(
    await page
      .getByRole("button", { name: "视觉动效", exact: true })
      .getAttribute("aria-pressed"),
    "false",
  );
  await page.getByRole("button", { name: "视觉动效", exact: true }).click();
  await page.locator('.torque-surface[data-state="running"]').waitFor();

  await page
    .getByRole("button", { name: /试验记录/ })
    .first()
    .click();
  assert.equal(await page.locator(".torque-surface").count(), 0);
  await page.getByRole("button", { name: "试验工作台", exact: true }).click();
  await page.locator('.torque-surface[data-state="running"]').waitFor();
  await page.screenshot({
    path: "output/shader-workbench.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  await page.screenshot({ path: "output/shader-mobile.png", fullPage: true });
  await context.close();

  for (const scenario of ["reduced-motion", "no-webgpu", "no-adapter"]) {
    const fallbackContext = await createContext({
      reducedMotion: scenario === "reduced-motion" ? "reduce" : "no-preference",
    });
    if (scenario === "no-webgpu")
      await fallbackContext.addInitScript(() =>
        Object.defineProperty(navigator, "gpu", { value: undefined }),
      );
    if (scenario === "no-adapter")
      await fallbackContext.addInitScript(() =>
        Object.defineProperty(navigator, "gpu", {
          value: { requestAdapter: async () => null },
        }),
      );
    const fallbackPage = await fallbackContext.newPage();
    const requests = [];
    fallbackPage.on("request", (request) => requests.push(request.url()));
    await fallbackPage.goto(baseUrl);
    await fallbackPage
      .locator(
        `.torque-surface[data-state="${scenario === "reduced-motion" ? "static" : "fallback"}"]`,
      )
      .waitFor();
    if (scenario !== "no-adapter")
      assert.deepEqual(
        shaderRequests(requests),
        [],
        `${scenario} never downloads the GPU library`,
      );
    if (scenario === "reduced-motion")
      assert.equal(
        await fallbackPage
          .getByRole("button", { name: "视觉动效" })
          .isDisabled(),
        true,
      );
    assert.equal(
      await fallbackPage
        .locator(".torque-surface canvas")
        .evaluate((canvas) => getComputedStyle(canvas).opacity),
      "0",
    );
    await fallbackPage
      .getByRole("button", { name: "运行加载演示", exact: true })
      .click();
    await fallbackPage
      .getByRole("button", { name: "开始记录", exact: true })
      .click();
    await fallbackPage
      .getByRole("button", { name: "停止记录", exact: true })
      .waitFor();
    await fallbackContext.close();
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(
    externalRequests,
    [],
    "no telemetry or other external requests",
  );
  console.log(
    "Shader UI passed: real GPU animation, recording pause, preference, remount, mobile layout and three static fallbacks.",
  );
} finally {
  await browser.close();
}
