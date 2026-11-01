// Open a separate browser already connected to a locally generated demo CTF.
import { readFile } from "node:fs/promises";
import { parseArgs } from "node:util";
import { chromium } from "@playwright/test";

const { values } = parseArgs({
  options: {
    "access-file": { type: "string" },
    screenshot: { type: "string" },
    headless: { type: "boolean", default: false },
  },
});

if (!values["access-file"]) {
  throw new Error("Provide --access-file from scripts/demo.py seed");
}

let browser;
try {
  const access = JSON.parse(await readFile(values["access-file"], "utf8"));
  const url = new URL(access.url);
  if (!["http:", "https:"].includes(url.protocol)) {
    throw new Error("Unsupported URL scheme");
  }
  browser = await chromium.launch({ headless: values.headless });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  await page.goto(`${url.origin}/#project=${access.project_id}`);
  await page.getByLabel("Ідентифікатор CTF").fill(access.project_id);
  await page.getByLabel("Токен доступу").fill(access.token);
  await page.getByRole("button", { name: "Підключитися", exact: true }).click();
  await page
    .getByRole("heading", { name: "DEMO · Nightshift CTF", exact: true })
    .waitFor();
  if (values.screenshot) {
    await page.screenshot({ path: values.screenshot, fullPage: true });
  }
  console.log(`Demo opened: ${url.origin}/#project=${access.project_id}`);
  if (values.headless) {
    await browser.close();
  } else {
    process.on("SIGINT", () => browser.close());
    process.on("SIGTERM", () => browser.close());
    await new Promise((resolve) => browser.on("disconnected", resolve));
  }
} catch (error) {
  // Do not echo credentials, browser state, or page content.
  console.error(`Demo preview stopped (${error.name}).`);
  await browser?.close();
  process.exitCode = 1;
}
