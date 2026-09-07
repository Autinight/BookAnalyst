const { chromium } = require(
  process.env.BOOKANALYST_PLAYWRIGHT || "playwright",
);
const fs = require("fs");
(async () => {
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  const errors = [],
    requests = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => requests.push(r.url()));
  const base = process.env.BOOKANALYST_URL || "http://127.0.0.1:8766";
  const rid =
    process.env.BOOKANALYST_RUN ||
    JSON.parse(fs.readFileSync("tmp/v6-real-run.json", "utf8")).id;
  try {
    await page.goto(base, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "书库", exact: true }).waitFor();
    await page.screenshot({
      path: ".bookanalyst/rebuild-library.png",
      fullPage: true,
    });
    await page.locator("[data-new]").first().click();
    await page
      .locator('select[name="model_id"] option[value="gpt-6-astra"]')
      .waitFor({ state: "attached" });
    if ((await page.locator('[name="pages_per_task"]').inputValue()) !== "3")
      throw Error("Page default");
    if (
      (await page.locator('[name="reasoning_effort"]').inputValue()) !==
      "medium"
    )
      throw Error("Effort control");
    if (await page.locator('[name$="_context"],[name$="_output"]').count())
      throw Error("Removed token fields present");
    await page.locator("[data-close]").click();
    const start = Date.now();
    await page.goto(base + "/?run=" + rid, { waitUntil: "networkidle" });
    await page.locator("#page-tex").waitFor();
    const loadMs = Date.now() - start;
    await page.evaluate(() => {
      window.testImage = document.querySelector("#source-image");
      window.testInput = document.querySelector("#page-number");
      window.testMain = document.querySelector("#main");
      window.longTasks = [];
      new PerformanceObserver((list) =>
        window.longTasks.push(...list.getEntries().map((x) => x.duration)),
      ).observe({ entryTypes: ["longtask"] });
    });
    const before = requests.filter((x) => x.includes("/image?")).length;
    await page.locator("#page-number").fill("4");
    await page.waitForTimeout(7000);
    const stability = await page.evaluate(() => ({
      sameImage: window.testImage === document.querySelector("#source-image"),
      sameInput: window.testInput === document.querySelector("#page-number"),
      input: document.querySelector("#page-number").value,
      longTasks: window.longTasks,
    }));
    if (!stability.sameImage || !stability.sameInput || stability.input !== "4")
      throw Error("Polling replaced or reset interactive DOM");
    const idleImages =
      requests.filter((x) => x.includes("/image?")).length - before;
    await page.locator("#page-number").press("Tab");
    await page.locator("#source-image").evaluate((img) => img.decode());
    // Exercise an actual in-flight batch while conversion continues on the server.
    let transition = null;
    if (process.env.BOOKANALYST_WAIT_PROGRESS === "1") {
      const response = await page.request.get(
        base + "/api/runs/" + rid + "/tasks",
      );
      const active = (await response.json()).find(
        (t) => t.stage === "convert" && t.state === "RUNNING",
      );
      if (active) {
        const current = active.pages[0],
          typed = String(current + 1);
        await page.locator("#page-number").fill(String(current));
        await page.locator("#page-number").press("Tab");
        await page.locator("#source-image").evaluate((img) => img.decode());
        await page.locator("#page-number").fill(typed);
        await page.waitForFunction(
          (id) => document.getElementById(id)?.classList.contains("PASSED"),
          active.id,
          { timeout: 240000 },
        );
        await page.waitForTimeout(1000);
        const actual = await page.locator("#page-number").inputValue();
        if (actual !== typed)
          throw Error("Task completion overwrote active page input");
        transition = { task: active.id, preservedInput: actual };
      }
    }
    await page.locator("#requests-details summary").click();
    await page.locator("#requests-content table").waitFor();
    await page.screenshot({
      path: ".bookanalyst/rebuild-run.png",
      fullPage: true,
    });
    if (errors.length) throw Error(errors.join("\n"));
    const report = {
      loadMs,
      stability,
      transition,
      imageRequestsDuringIdle: idleImages,
      totalRequests: requests.length,
    };
    fs.writeFileSync(
      ".bookanalyst/rebuild-browser-report.json",
      JSON.stringify(report, null, 2),
    );
    console.log(report);
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
