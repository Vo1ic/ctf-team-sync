import { test, expect } from "@playwright/test";
import { randomUUID, createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { accessLink } from "../../src/team_sync/web/access.js";

async function project(request, options = {}) {
  const response = await request.post("/api/v1/projects", {
    headers: { Authorization: "Bearer browser-test-bootstrap-secret" },
    data: {
      name: `CTF ${randomUUID().slice(0, 6)}`,
      owner_name: "Captain",
      ...options,
    },
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function enroll(request, ctf, role = "worker") {
  const response = await request.post(
    `/api/v1/projects/${ctf.project.id}/actors`,
    {
      headers: { Authorization: `Bearer ${ctf.token}` },
      data: {
        name: "Agent Alpha",
        kind: "agent",
        role,
        owner_id: ctf.actor.id,
      },
    },
  );
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function command(
  request,
  ctf,
  operation,
  arguments_,
  resourceId = null,
  token = ctf.token,
) {
  const response = await request.post(
    `/api/v1/projects/${ctf.project.id}/commands`,
    {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        operation,
        arguments: arguments_,
        resource_id: resourceId,
        idempotency_key: randomUUID(),
      },
    },
  );
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function join(page, ctf, token = ctf.token) {
  const hash = new URL(accessLink("http://localhost", ctf.project.id, token))
    .hash;
  await page.goto(`/${hash}`);
  await expect(
    page.getByRole("heading", { name: ctf.project.name, exact: true }),
  ).toBeVisible();
}

async function section(page, name) {
  if (
    await page
      .getByRole("button", { name: "Відкрити меню", exact: true })
      .isVisible()
  ) {
    await page
      .getByRole("button", { name: "Відкрити меню", exact: true })
      .click();
  }
  await page
    .getByRole("navigation")
    .getByRole("button", { name: new RegExp(`^${name}`) })
    .click();
}

test("create a CTF and issue an agent credential through the web", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "Ініціалізувати проєкт" }).click();
  await page
    .getByLabel("Назва CTF", { exact: true })
    .fill("Browser-created CTF");
  await page.getByLabel("Ваше ім’я", { exact: true }).fill("Captain");
  await page
    .getByLabel("Ключ ініціалізації сервера")
    .fill("browser-test-bootstrap-secret");
  await page.getByRole("button", { name: "Створити CTF", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Доступ для Captain" }),
  ).toBeVisible();
  const ownerToken = await page
    .getByLabel("Особистий токен", { exact: true })
    .inputValue();
  expect(ownerToken.length).toBeGreaterThan(30);
  await page.getByRole("button", { name: "Готово" }).click();
  await section(page, "Завдання CTF");
  await page
    .getByRole("button", { name: "Додати завдання", exact: true })
    .first()
    .click();
  await page.getByLabel("Назва завдання", { exact: true }).fill("Web Portal");
  await page.getByLabel("Категорія", { exact: true }).fill("Web");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Додати завдання", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Web Portal", exact: true }),
  ).toBeVisible();
  await section(page, "Команда");
  await page
    .getByRole("button", { name: "Підключити агента", exact: true })
    .click();
  await page.getByLabel("Ім’я / назва агента").fill("Web Agent");
  await page.getByLabel("Спеціалізації").fill("web, reverse");
  await page
    .getByRole("button", { name: "Видати доступ", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Доступ для Web Agent" }),
  ).toBeVisible();
  const agentToken = await page
    .getByLabel("Особистий токен", { exact: true })
    .inputValue();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Зберегти MCP-конфігурацію" }).click();
  const download = await downloadPromise;
  const config = JSON.parse(await readFile(await download.path(), "utf8"));
  expect(config.mcpServers["team-sync"].env.TEAM_SYNC_TOKEN).toBe(agentToken);
  expect(config.mcpServers["team-sync"].command).toBe("uvx");
  expect(config.mcpServers["team-sync"].args[1]).toContain("@v0.3.0");
  await page.getByLabel("Запуск MCP", { exact: true }).selectOption("local");
  await page
    .getByLabel("Репозиторій на пристрої агента")
    .fill("C:\\CTF\\team-sync");
  const localPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Зберегти MCP-конфігурацію" }).click();
  const local = JSON.parse(
    await readFile(await (await localPromise).path(), "utf8"),
  );
  expect(local.mcpServers["team-sync"].command).toBe("uv");
  expect(local.mcpServers["team-sync"].args[2]).toBe("C:\\CTF\\team-sync");
  const promptPromise = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Зберегти стартову інструкцію" })
    .click();
  const prompt = await readFile(await (await promptPromise).path(), "utf8");
  expect(prompt).toContain("get_context");
  expect(prompt).toContain("update_task");
  expect(prompt).not.toContain(agentToken);
  await page.getByRole("button", { name: "Готово" }).click();
  await expect(
    page.getByRole("heading", { name: "Web Agent", exact: true }),
  ).toBeVisible();
  const storage = await page.evaluate(() =>
    JSON.stringify({ ...localStorage, ...sessionStorage }),
  );
  expect(storage).not.toContain(ownerToken);
  expect(storage).not.toContain(agentToken);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Browser-created CTF", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("two clients coordinate a task and show external changes without reload", async ({
  page,
  context,
  request,
}) => {
  const ctf = await project(request);
  const agent = await enroll(request, ctf);
  await join(page, ctf);
  await page
    .getByRole("button", { name: "Нова робота", exact: true })
    .first()
    .click();
  await page.getByLabel("Що потрібно зробити").fill("Analyze authentication");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Створити роботу", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Analyze authentication", exact: true }),
  ).toBeVisible();
  const worker = await context.newPage();
  await join(worker, ctf, agent.token);
  await section(worker, "Роботи");
  await worker
    .getByRole("button", { name: "Взяти роботу", exact: true })
    .click();
  await expect(
    page.locator(".task-row").filter({ hasText: "Analyze authentication" }),
  ).toContainText("Agent Alpha");
  await worker.getByRole("button", { name: "Завершити", exact: true }).click();
  await worker
    .getByLabel("Результат роботи")
    .fill("Reviewed session flow; evidence in shared logs.");
  await worker
    .getByRole("dialog")
    .getByRole("button", { name: "Завершити", exact: true })
    .click();
  await section(page, "Роботи");
  await expect(
    page
      .locator(".board-column")
      .filter({ has: page.getByRole("heading", { name: "Завершені" }) }),
  ).toContainText("Analyze authentication");
  await worker.close();
});

test("flag verification and submission outcome stay separate", async ({
  page,
  request,
}) => {
  const ctf = await project(request);
  await join(page, ctf);
  await section(page, "Флаги");
  await page.getByRole("button", { name: "Додати флаг", exact: true }).click();
  await page.getByLabel("Значення флага").fill("FLAG{browser_candidate}");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Додати флаг", exact: true })
    .click();
  await expect(page.locator("tbody tr")).toContainText("Не перевірено");
  await page
    .getByRole("button", { name: "FLAG{browser_candidate}", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Перевірити флаг", exact: true })
    .click();
  await page
    .getByLabel("Підстава та докази")
    .fill("Verified checker output manually.");
  await page
    .getByRole("button", { name: "Зберегти перевірку", exact: true })
    .click();
  await expect(page.locator("tbody tr")).toContainText("Підтверджено");
  await page
    .getByRole("button", { name: "FLAG{browser_candidate}", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Записати відправлення", exact: true })
    .click();
  await page.getByLabel("Відповідь платформи").selectOption("unknown");
  await page
    .getByLabel("Відповідь або обставини спроби")
    .fill("Connection lost before response.");
  await page
    .getByRole("button", { name: "Зберегти результат", exact: true })
    .click();
  await expect(page.locator("tbody tr")).toContainText("Підтверджено");
  await expect(page.locator("tbody tr")).toContainText("Результат невідомий");
});

test("observer receives a private message and explicitly acknowledges it", async ({
  page,
  request,
}) => {
  const ctf = await project(request);
  const observer = await enroll(request, ctf, "observer");
  await join(page, ctf, observer.token);
  await expect(
    page.getByRole("button", { name: "Нова робота", exact: true }),
  ).toHaveCount(0);
  await section(page, "Повідомлення");
  await command(request, ctf, "message.send", {
    recipient_id: observer.actor.id,
    body: "Please review the evidence.",
  });
  const card = page.locator(".message-card");
  await expect(card).toContainText("Please review the evidence.");
  await expect(card).toContainText("Нове");
  await card.getByRole("button", { name: "Опрацьовано", exact: true }).click();
  await expect(card).toContainText("Опрацьовано");
  await expect(
    card.getByRole("button", { name: "Опрацьовано", exact: true }),
  ).toHaveCount(0);
});

test("upload and download a binary artifact with integrity verification", async ({
  page,
  request,
}) => {
  const ctf = await project(request);
  await join(page, ctf);
  await section(page, "Файли");
  await page
    .getByRole("button", { name: "Завантажити файл", exact: true })
    .click();
  const payload = Buffer.from([0, 1, 255, 67, 84, 70]);
  await page.getByLabel("Виберіть файл").setInputFiles({
    name: "sample.bin",
    mimeType: "application/octet-stream",
    buffer: payload,
  });
  await page.getByRole("button", { name: "Опублікувати", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "sample.bin", exact: true }),
  ).toBeVisible();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Завантажити", exact: true }).click();
  const downloaded = await downloadPromise;
  expect(await readFile(await downloaded.path())).toEqual(payload);
  const checksum = createHash("sha256").update(payload).digest("hex");
  await expect(page.locator(".checksum")).toContainText(checksum);
  await page.route("**/artifacts/*/content", (route) =>
    route.fulfill({ body: "modified" }),
  );
  await page.getByRole("button", { name: "Завантажити", exact: true }).click();
  await expect(
    page
      .getByRole("status")
      .filter({ has: page.getByText(/Контрольна сума файла не збігається/) }),
  ).toBeVisible();
});

test("a lost command response retries safely without duplicating a task", async ({
  page,
  request,
}) => {
  const ctf = await project(request);
  await join(page, ctf);
  const keys = [];
  await page.route("**/commands", async (route) => {
    const action = route.request().postDataJSON();
    keys.push(action.idempotency_key);
    if (keys.length === 1) {
      await route.fetch();
      await route.abort("failed");
    } else await route.continue();
  });
  await page
    .getByRole("button", { name: "Нова робота", exact: true })
    .first()
    .click();
  await page.getByLabel("Що потрібно зробити").fill("Create exactly once");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Створити роботу", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Повторити запит", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Що потрібно зробити")).toBeDisabled();
  await page
    .getByRole("button", { name: "Повторити запит", exact: true })
    .click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  const listing = await request.get(
    `/api/v1/projects/${ctf.project.id}/collections/tasks`,
    { headers: { Authorization: `Bearer ${ctf.token}` } },
  );
  expect((await listing.json()).items).toHaveLength(1);
});

test("untrusted findings render as text and the mobile page stays within the viewport", async ({
  page,
  request,
}) => {
  const ctf = await project(request);
  const payload = '<img src=x onerror="window.ctfInjected=true">';
  await command(request, ctf, "finding.create", {
    title: "Untrusted response",
    body: payload,
    evidence: "External HTTP response",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await join(page, ctf);
  await section(page, "Знахідки");
  await expect(page.locator(".finding-card")).toContainText(payload);
  await expect(page.locator(".finding-card img")).toHaveCount(0);
  expect(await page.evaluate(() => window.ctfInjected)).toBeUndefined();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await expect(page.locator("body")).not.toHaveClass(/menu-open/);
  await page.screenshot({
    path: "test-results/mobile-findings.png",
    fullPage: true,
    animations: "disabled",
  });
});

test("received credentials remain visible when refreshing the workspace fails", async ({
  page,
  request,
}) => {
  const ctf = await project(request);
  await join(page, ctf);
  await section(page, "Команда");
  await page.route("**/collections/actors?**", (route) =>
    route.abort("failed"),
  );
  await page
    .getByRole("button", { name: "Підключити агента", exact: true })
    .click();
  await page.getByLabel("Ім’я / назва агента").fill("Preserve credential");
  await page
    .getByRole("button", { name: "Видати доступ", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Доступ для Preserve credential" }),
  ).toBeVisible();
  const token = await page
    .getByLabel("Особистий токен", { exact: true })
    .inputValue();
  const result = await request.get(
    `/api/v1/projects/${ctf.project.id}/snapshot`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  expect(result.ok()).toBeTruthy();
  expect((await result.json()).actor.name).toBe("Preserve credential");
});

test("revoking access disconnects the browser and clears shared content", async ({
  page,
  request,
}) => {
  const ctf = await project(request);
  const agent = await enroll(request, ctf);
  await join(page, ctf, agent.token);
  const response = await request.post(
    `/api/v1/projects/${ctf.project.id}/actors/${agent.actor.id}/revoke`,
    { headers: { Authorization: `Bearer ${ctf.token}` } },
  );
  expect(response.ok()).toBeTruthy();
  await expect(
    page.getByRole("button", { name: "Відкрити файл доступу", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("navigation")).toHaveCount(0);
  await expect(page.getByLabel("Токен доступу", { exact: true })).toHaveValue(
    "",
  );
});

test("new project credentials survive a failed initial connection", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.route("**/snapshot", (route) => route.abort("failed"));
  await page.getByRole("button", { name: "Ініціалізувати проєкт" }).click();
  await page
    .getByLabel("Назва CTF", { exact: true })
    .fill("Created before disconnect");
  await page.getByLabel("Ваше ім’я", { exact: true }).fill("Captain");
  await page
    .getByLabel("Ключ ініціалізації сервера")
    .fill("browser-test-bootstrap-secret");
  await page.getByRole("button", { name: "Створити CTF", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Доступ для Captain" }),
  ).toBeVisible();
  const token = await page
    .getByLabel("Особистий токен", { exact: true })
    .inputValue();
  const id = await page
    .getByLabel("Ідентифікатор CTF", { exact: true })
    .last()
    .inputValue();
  const response = await request.get(`/api/v1/projects/${id}/snapshot`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.ok()).toBeTruthy();
  expect((await response.json()).project.name).toBe(
    "Created before disconnect",
  );
});

test("desktop overview presents actual shared project state", async ({
  page,
  request,
}) => {
  const ctf = await project(request, {
    name: "Autumn CTF 2026",
    rules:
      "Додавайте докази до знахідок. Перед початком беріть роботу на себе. Для нових агентів створюйте окремий доступ.",
  });
  const agent = await enroll(request, ctf);
  const web = await command(request, ctf, "challenge.create", {
    title: "Identity Portal",
    category: "Web",
    description: "Дослідити поведінку сервісу авторизації.",
  });
  const crypto = await command(request, ctf, "challenge.create", {
    title: "Lost in Numbers",
    category: "Crypto",
    description: "Проаналізувати надані матеріали та перевірити гіпотезу.",
  });
  for (const title of [
    "Розібрати перевірку сесії",
    "Порівняти відповіді сервера",
    "Зібрати логи запитів",
  ]) {
    await command(request, ctf, "task.create", { title, challenge_id: web.id });
  }
  const task = await command(request, ctf, "task.create", {
    title: "Перевірити гіпотезу щодо ключа",
    challenge_id: crypto.id,
  });
  await command(
    request,
    ctf,
    "task.claim",
    { expected_version: 1 },
    task.id,
    agent.token,
  );
  await command(request, ctf, "finding.create", {
    title: "Відповідь залежить від стану сесії",
    body: "Для свіжої та завершеної сесії сервіс повертає різні коди відповіді. Потрібна додаткова перевірка.",
    evidence: "Порівняно відповіді HTTP 200 і HTTP 401 у локальному журналі.",
    kind: "observation",
    challenge_id: web.id,
  });
  const flag = await command(request, ctf, "flag.create", {
    value: "FLAG{test_fixture}",
    challenge_id: crypto.id,
  });
  await command(
    request,
    ctf,
    "flag.verify",
    {
      expected_version: 1,
      status: "confirmed",
      method: "manual",
      evidence: "Перевірено за контрольним результатом.",
    },
    flag.id,
  );
  await join(page, ctf);
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await expect(page.locator(".task-list .task-row")).toHaveCount(4);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(1440);
  await page.screenshot({
    path: "test-results/desktop-overview.png",
    fullPage: true,
    animations: "disabled",
  });
  expect(errors).toEqual([]);
});

test("a human opens an access file without an account, reloads, and disconnects", async ({
  page,
  request,
  browser,
}) => {
  const ctf = await project(request);
  await join(page, ctf);
  await section(page, "Підключення");
  await page
    .getByRole("button", { name: "Запросити людину", exact: true })
    .click();
  await page
    .getByLabel("Ім’я / псевдонім", { exact: true })
    .fill("Human reviewer");
  await page.getByLabel("Роль", { exact: true }).selectOption("observer");
  await page
    .getByRole("button", { name: "Видати доступ", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Доступ для Human reviewer",
      exact: true,
    }),
  ).toBeVisible();
  const downloadPromise = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Зберегти файл доступу", exact: true })
    .click();
  const file = JSON.parse(
    await readFile(await (await downloadPromise).path(), "utf8"),
  );
  expect(file.actor.kind).toBe("human");
  expect(file.actor.role).toBe("observer");
  const reviewerContext = await browser.newContext();
  const reviewer = await reviewerContext.newPage();
  await reviewer.goto(new URL(page.url()).origin);
  await reviewer.getByLabel("Файл доступу Team Sync").setInputFiles({
    name: "access.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(file)),
  });
  await expect(
    reviewer.getByRole("heading", { name: ctf.project.name, exact: true }),
  ).toBeVisible();
  await expect(reviewer.locator(".current-actor")).toContainText(
    "Human reviewer",
  );
  const stored = await reviewer.evaluate(() =>
    JSON.stringify({ ...localStorage, ...sessionStorage }),
  );
  expect(stored).not.toContain(file.token);
  expect(await reviewer.evaluate(() => document.cookie)).not.toContain(
    file.token,
  );
  await reviewer.reload();
  await expect(
    reviewer.getByRole("heading", { name: ctf.project.name, exact: true }),
  ).toBeVisible();
  await section(reviewer, "Підключення");
  await expect(
    reviewer.getByRole("button", { name: "Запросити людину", exact: true }),
  ).toHaveCount(0);
  await reviewer
    .getByRole("button", { name: "Від’єднатися", exact: true })
    .click();
  await expect(
    reviewer.getByRole("button", {
      name: "Відкрити файл доступу",
      exact: true,
    }),
  ).toBeVisible();
  await reviewer.reload();
  await expect(
    reviewer.getByRole("button", {
      name: "Відкрити файл доступу",
      exact: true,
    }),
  ).toBeVisible();
  const response = await reviewer.request.get(
    `/api/v1/projects/${ctf.project.id}/snapshot`,
  );
  expect(response.status()).toBe(401);
  await reviewerContext.close();
});

test("two identities in one browser keep their own actor after reload", async ({
  page,
  context,
  request,
}) => {
  const ctf = await project(request);
  const agent = await enroll(request, ctf);
  await join(page, ctf);
  const worker = await context.newPage();
  await join(worker, ctf, agent.token);
  await page.reload();
  await worker.reload();
  await expect(page.locator(".current-actor")).toContainText("Captain");
  await expect(worker.locator(".current-actor")).toContainText("Agent Alpha");
  await page.getByRole("button", { name: "Від’єднатися", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Відкрити файл доступу", exact: true }),
  ).toBeVisible();
  await worker.reload();
  await expect(worker.locator(".current-actor")).toContainText("Agent Alpha");
  expect(worker.url()).not.toContain("join=");
  expect(await worker.evaluate(() => document.cookie)).not.toContain(
    agent.token,
  );
  await worker.close();
});

test("connection import rejects another server before authenticating", async ({
  page,
}) => {
  await page.goto("/");
  let attempts = 0;
  page.on("request", (r) => {
    if (r.url().includes("browser-session")) attempts++;
  });
  await page.getByLabel("Файл доступу Team Sync").setInputFiles({
    name: "foreign.json",
    mimeType: "application/json",
    buffer: Buffer.from(
      JSON.stringify({
        url: "https://foreign.invalid",
        project_id: randomUUID(),
        token: "synthetic-connection-credential",
      }),
    ),
  });
  await expect(page.getByRole("alert")).toContainText("іншому серверу");
  expect(attempts).toBe(0);
});

test("a sender sees recipient receipts in the outbox without exposing other conversations", async ({
  page,
  request,
}) => {
  const ctf = await project(request);
  const peer = await enroll(request, ctf);
  await join(page, ctf);
  await section(page, "Повідомлення");
  await page.getByRole("button", { name: "Написати", exact: true }).click();
  await page.getByLabel("Кому", { exact: true }).selectOption(peer.actor.id);
  await page
    .getByLabel("Повідомлення", { exact: true })
    .fill("Review this synthetic evidence");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Надіслати", exact: true })
    .click();
  const card = page
    .locator(".message-card")
    .filter({ hasText: "Review this synthetic evidence" });
  await expect(card).toContainText("Чекає підтвердження адресата");
  const inbox = await request.get(`/api/v1/projects/${ctf.project.id}/inbox`, {
    headers: { Authorization: `Bearer ${peer.token}` },
  });
  const message = (await inbox.json()).items[0];
  await command(
    request,
    ctf,
    "message.ack",
    { stage: "processed" },
    message.id,
    peer.token,
  );
  await expect(card).toContainText("Адресат опрацював");
  await expect(
    card.getByRole("button", { name: "Опрацьовано", exact: true }),
  ).toHaveCount(0);
});

test("a task can be handed off with preserved context and reclaimed", async ({
  page,
  request,
}) => {
  const ctf = await project(request);
  const task = await command(request, ctf, "task.create", {
    title: "Recover after handoff",
  });
  await join(page, ctf);
  await section(page, "Роботи");
  await page.getByRole("button", { name: "Взяти роботу", exact: true }).click();
  await page
    .getByRole("button", { name: "Recover after handoff", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Передати роботу далі", exact: true })
    .click();
  await page
    .getByLabel("Контекст для наступного виконавця")
    .fill("Header parsed. Continue by checking timezone.");
  await page
    .getByRole("button", { name: "Звільнити роботу", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Взяти роботу", exact: true }),
  ).toBeVisible();
  await section(page, "Знахідки");
  await expect(page.locator(".finding-card")).toContainText(
    "Header parsed. Continue by checking timezone.",
  );
  const detail = await request.get(
    `/api/v1/projects/${ctf.project.id}/resources/tasks/${task.id}`,
    { headers: { Authorization: `Bearer ${ctf.token}` } },
  );
  expect((await detail.json()).item.status).toBe("open");
});

test("human uploads evidence, approves a named agent, and reads its README", async ({
  page,
  request,
}) => {
  const ctf = await project(request);
  const agent = await enroll(request, ctf);
  await join(page, ctf);
  await section(page, "Файли");
  await page
    .getByRole("button", { name: "Завантажити файл", exact: true })
    .click();
  await page.getByLabel("Виберіть файл").setInputFiles({
    name: "human-note.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Synthetic human evidence"),
  });
  await page
    .getByLabel("Кому запропонувати аналіз")
    .selectOption(agent.actor.id);
  await page.getByRole("button", { name: "Опублікувати", exact: true }).click();
  const card = page
    .locator(".artifact-card")
    .filter({ hasText: "human-note.txt" });
  await expect(card).toBeVisible();
  const artifactId = await card.getAttribute("data-artifact-id");
  const contentPath = `/api/v1/projects/${ctf.project.id}/artifacts/${artifactId}/content`;
  expect(
    (
      await request.get(contentPath, {
        headers: { Authorization: `Bearer ${agent.token}` },
      })
    ).status(),
  ).toBe(403);
  const review = await command(
    request,
    ctf,
    "artifact.review.request",
    {},
    artifactId,
    agent.token,
  );
  await section(page, "Огляд");
  await expect(
    page.getByRole("heading", { name: "Потрібна ваша участь" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Переглянути запит", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Agent Alpha взяв артефакт" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Дозволити аналіз", exact: true })
    .click();
  await page
    .getByLabel("Коментар / обмеження")
    .fill("Лише аналіз вмісту, без запуску команд із файла.");
  await page.getByRole("button", { name: "Дозволити", exact: true }).click();
  const resource = `/api/v1/projects/${ctf.project.id}/resources/tasks/${review.id}`;
  const readTask = async () =>
    (
      await (
        await request.get(resource, {
          headers: { Authorization: `Bearer ${agent.token}` },
        })
      ).json()
    ).item;
  await expect
    .poll(async () => (await readTask()).approval_status)
    .toBe("approved");
  let task = await readTask();
  expect(
    (
      await request.get(contentPath, {
        headers: { Authorization: `Bearer ${agent.token}` },
      })
    ).status(),
  ).toBe(403);
  task = await command(
    request,
    ctf,
    "artifact.review.start",
    { expected_version: task.version },
    task.id,
    agent.token,
  );
  expect(
    (
      await request.get(contentPath, {
        headers: { Authorization: `Bearer ${agent.token}` },
      })
    ).ok(),
  ).toBeTruthy();
  await command(
    request,
    ctf,
    "artifact.review.complete",
    {
      expected_version: task.version,
      readme:
        "# README\nEvidence is synthetic. No executable instructions followed.",
      verdict: "useful",
    },
    task.id,
    agent.token,
  );
  await section(page, "Файли");
  await expect(card).toContainText("README · Agent Alpha");
  await card.getByRole("button", { name: "Відкрити", exact: true }).click();
  await expect(page.locator("#modal pre")).toContainText(
    "No executable instructions followed.",
  );
  const downloading = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Завантажити .md", exact: true })
    .click();
  expect(await readFile(await (await downloading).path(), "utf8")).toContain(
    "# README",
  );
  await page.getByRole("button", { name: "Закрити", exact: true }).click();
  await section(page, "Команда");
  const actor = page.locator(".actor-card").filter({ hasText: "Agent Alpha" });
  await expect(actor).toContainText("Вільний");
  await expect(actor).toContainText("Останній звіт");
});

test("text artifacts stay plain text and archives can be restored by a human", async ({
  page,
  request,
}) => {
  const ctf = await project(request);
  await join(page, ctf);
  await section(page, "Файли");
  await page.getByRole("button", { name: "Додати текст", exact: true }).click();
  await page.getByLabel("Назва", { exact: true }).fill("Suspicious note");
  await page
    .getByLabel("Текст", { exact: true })
    .fill("<script>window.artifactExecuted=true</script>\nSynthetic CTF note");
  await page
    .getByRole("button", { name: "Додати й запропонувати аналіз", exact: true })
    .click();
  const card = page
    .locator(".artifact-card")
    .filter({ hasText: "Suspicious note.txt" });
  await card.getByRole("button", { name: "Відкрити", exact: true }).click();
  await page
    .getByRole("button", { name: "Попередній перегляд", exact: true })
    .click();
  await expect(page.locator(".artifact-preview pre")).toContainText("<script>");
  expect(await page.evaluate(() => window.artifactExecuted)).toBeUndefined();
  await page.getByRole("button", { name: "Архівувати", exact: true }).click();
  await page
    .getByLabel("Причина", { exact: true })
    .fill("Неправильний матеріал, зберігаємо історію");
  await page.getByRole("button", { name: "Архівувати", exact: true }).click();
  await expect(card).toHaveCount(0);
  await page
    .getByRole("button", { name: "Показати архів", exact: true })
    .click();
  await expect(card).toContainText("В архіві");
  await card.getByRole("button", { name: "Відкрити", exact: true }).click();
  await page.getByRole("button", { name: "Відновити", exact: true }).click();
  await page
    .getByLabel("Причина", { exact: true })
    .fill("Потрібна друга думка");
  await page.getByRole("button", { name: "Відновити", exact: true }).click();
  await expect(card).not.toContainText("В архіві");
});

test("photo upload previews raster bytes with the project cookie", async ({
  page,
  request,
}) => {
  const ctf = await project(request);
  await join(page, ctf);
  const data = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 64;
    canvas.height = 64;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#8bcf81";
    ctx.fillRect(0, 0, 64, 64);
    return canvas.toDataURL("image/png").split(",")[1];
  });
  await section(page, "Файли");
  await page
    .getByRole("button", { name: "Завантажити файл", exact: true })
    .click();
  await page.getByLabel("Виберіть файл").setInputFiles({
    name: "synthetic-photo.png",
    mimeType: "image/png",
    buffer: Buffer.from(data, "base64"),
  });
  await page
    .getByLabel("Попросити агента проаналізувати", { exact: true })
    .uncheck();
  await page.getByRole("button", { name: "Опублікувати", exact: true }).click();
  await page
    .locator(".artifact-card")
    .getByRole("button", { name: "Відкрити", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Попередній перегляд", exact: true })
    .click();
  const img = page.getByRole("img", {
    name: "synthetic-photo.png",
    exact: true,
  });
  await expect(img).toBeVisible();
  await expect.poll(() => img.evaluate((node) => node.naturalWidth)).toBe(64);
});

test("attention excerpts open complete messages without acknowledging them", async ({
  page,
  request,
}) => {
  const ctf = await project(request);
  const agent = await enroll(request, ctf);
  await command(
    request,
    ctf,
    "message.send",
    {
      body: "Long evidence ".repeat(500) + "ACTUAL-TAIL-MARKER",
      recipient_id: ctf.actor.id,
    },
    null,
    agent.token,
  );
  await join(page, ctf);
  await section(page, "Повідомлення");
  await page
    .getByRole("button", { name: "Прочитати повний текст", exact: true })
    .click();
  await expect(page.locator("#modal")).toContainText("ACTUAL-TAIL-MARKER");
  const inbox = await (
    await request.get(`/api/v1/projects/${ctf.project.id}/inbox`, {
      headers: { Authorization: `Bearer ${ctf.token}` },
    })
  ).json();
  expect(inbox.items[0].ack_stage).toBeNull();
});
