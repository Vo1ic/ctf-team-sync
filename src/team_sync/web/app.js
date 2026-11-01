import { ApiClient, ApiError, commandId } from "./api.js";
import {
  el,
  icon,
  button,
  badge,
  empty,
  field,
  notify,
  modal,
  openForm,
  copy,
  saveBlob,
  date,
  size,
} from "./ui.js";

const root = document.querySelector("#root");
const sections = {
  overview: ["Огляд", "grid", "Стан команди та останні зміни"],
  challenges: [
    "Завдання CTF",
    "target",
    "Завдання змагання та пов’язані роботи",
  ],
  tasks: ["Роботи", "tasks", "Хто над чим працює і що можна взяти далі"],
  findings: [
    "Знахідки",
    "finding",
    "Спостереження, докази та перевірені гіпотези",
  ],
  flags: ["Флаги", "flag", "Кандидати, перевірки та результати відправлення"],
  messages: [
    "Повідомлення",
    "message",
    "Вхідні та повідомлення для всієї команди",
  ],
  artifacts: ["Файли", "file", "Матеріали команди з перевіркою цілісності"],
  actors: ["Команда", "team", "Учасники, агенти та їхні права доступу"],
};
const roles = {
  owner: "Власник",
  coordinator: "Координатор",
  worker: "Учасник",
  observer: "Спостерігач",
};
const kinds = {
  observation: "Спостереження",
  hypothesis: "Гіпотеза",
  result: "Результат",
  failed_attempt: "Невдала спроба",
};
const profiles = {
  jeopardy: "Jeopardy",
  attack_defense: "Attack–Defense",
  custom: "Власний формат",
};
const flagStates = {
  unverified: "Не перевірено",
  confirmed: "Підтверджено",
  rejected: "Спростовано",
};
const outcomes = {
  accepted: "Прийнято",
  rejected: "Відхилено",
  unknown: "Результат невідомий",
};
const state = {
  api: null,
  page: "overview",
  query: "",
  data: {},
  events: [],
  cursor: 0,
  sync: "loading",
  refreshQueue: Promise.resolve(),
};
let screen, pageTitle, pageSubtitle, pageAction, syncNode, nav, searchInput;
let leaseTimer;

function actorName(id) {
  return (
    state.data.actors?.find((item) => item.id === id)?.name ||
    "Невідомий учасник"
  );
}
function challengeName(id) {
  return (
    state.data.challenges?.find((item) => item.id === id)?.title ||
    "Загальна робота"
  );
}
function canWrite() {
  return state.actor?.role !== "observer";
}
function canVerify() {
  return ["owner", "coordinator"].includes(state.actor?.role);
}
function match(...values) {
  return (
    !state.query ||
    values.join(" ").toLocaleLowerCase("uk").includes(state.query)
  );
}
function expired(task) {
  return task.lease_until && new Date(task.lease_until) <= new Date();
}
function isMine(task) {
  return (
    task.assignee_id === state.actor.id &&
    task.status === "running" &&
    !expired(task)
  );
}
function taskStatus(task) {
  return task.status === "done"
    ? badge("Завершено", "green")
    : task.status === "open"
      ? badge("Вільна", "neutral")
      : expired(task)
        ? badge("Можна перевзяти", "amber")
        : badge("У роботі", "blue");
}
function flagBadge(flag) {
  return badge(
    flagStates[flag.verification_status],
    { confirmed: "green", rejected: "red" }[flag.verification_status] ||
      "amber",
  );
}
function metadata(...items) {
  return el(
    "div",
    { class: "metadata" },
    items.filter(Boolean).map((item) => el("span", {}, item)),
  );
}
function heading(title, description, action) {
  return el(
    "div",
    { class: "section-heading" },
    el("div", {}, el("h2", {}, title), description && el("p", {}, description)),
    action,
  );
}
function panel(title, content, action) {
  return el(
    "section",
    { class: "panel" },
    heading(title, null, action),
    content,
  );
}

function lastProject() {
  const hash = location.hash.match(/^#project=([0-9a-f-]{36})$/)?.[1];
  try {
    return hash || localStorage.getItem("team-sync.project") || "";
  } catch {
    return hash || "";
  }
}

function showLogin(message = "") {
  document.title = "Team Sync — CTF";
  const projectInput = field("project", "Ідентифікатор CTF", {
    required: true,
    value: lastProject(),
    placeholder: "UUID проєкту",
    autocomplete: "off",
    pattern: "[0-9a-f-]{36}",
  });
  const tokenInput = field("token", "Токен доступу", {
    type: "password",
    required: true,
    autocomplete: "off",
    placeholder: "Отримайте у власника CTF",
  });
  const formError = el(
    "p",
    { class: "form-error", role: "alert", hidden: !message },
    message,
  );
  const submit = el(
    "button",
    { class: "button primary full-width", type: "submit" },
    "Підключитися",
    icon("arrow"),
  );
  const form = el(
    "form",
    { class: "login-form" },
    projectInput,
    tokenInput,
    formError,
    submit,
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    formError.hidden = true;
    const values = new FormData(form);
    try {
      await connect(values.get("project").trim(), values.get("token").trim());
    } catch (error) {
      if (error.name !== "AbortError") {
        formError.textContent = error.message;
        formError.hidden = false;
      }
    } finally {
      submit.disabled = false;
    }
  });
  root.replaceChildren(
    el(
      "div",
      { class: "login-page" },
      el(
        "section",
        { class: "login-story", id: "main" },
        el(
          "a",
          { class: "brand", href: "/", "aria-label": "Team Sync" },
          el("img", { src: "/mark.svg", alt: "", width: "40", height: "40" }),
          el("span", {}, "team", el("strong", {}, "sync")),
        ),
        el(
          "div",
          { class: "login-intro" },
          el("div", { class: "eyebrow" }, "CTF · СПІЛЬНИЙ ПРОСТІР"),
          el("h1", {}, "Координація\nвашої команди."),
          el(
            "p",
            {},
            "Люди, агенти та результати роботи — в одному контексті. Незалежно від моделі, редактора й операційної системи.",
          ),
          el(
            "div",
            { class: "login-features" },
            [
              [
                "tasks",
                "Видимий розподіл роботи",
                "Кожна робота має виконавця та результат.",
              ],
              [
                "finding",
                "Знахідки з доказами",
                "Зберігайте вдалі рішення й невдалі спроби.",
              ],
              [
                "message",
                "Спільна історія",
                "Отримуйте зміни, навіть після втрати з’єднання.",
              ],
            ].map(([symbol, title, description]) =>
              el(
                "div",
                {},
                el("span", { class: "feature-icon" }, icon(symbol)),
                el(
                  "div",
                  {},
                  el("strong", {}, title),
                  el("p", {}, description),
                ),
              ),
            ),
          ),
        ),
        el(
          "p",
          { class: "login-footnote" },
          "Ваш сервер. Ваша команда. Спільний стан.",
        ),
      ),
      el(
        "section",
        { class: "login-connect" },
        el(
          "div",
          { class: "login-card" },
          badge("Підключення до CTF", "green"),
          el("h2", {}, "Приєднатися до команди"),
          el(
            "p",
            { class: "muted" },
            "Вкажіть проєкт і свій особистий токен доступу.",
          ),
          form,
          el(
            "p",
            { class: "privacy-note" },
            icon("shield"),
            "Токен залишається в пам’яті цієї сторінки.",
          ),
          el(
            "div",
            { class: "login-divider" },
            el("span", {}, "Створюєте новий CTF?"),
          ),
          button("Ініціалізувати проєкт", createProject, {
            tone: "secondary full-width",
            symbol: "plus",
          }),
        ),
      ),
    ),
  );
}

async function connect(projectId, token, initialized = null) {
  const api = new ApiClient(projectId, token);
  let snapshot, data, batch;
  try {
    snapshot = await api.get("snapshot");
    [data, batch] = await Promise.all([
      loadCollections(api),
      api.get(
        `events?after=${Math.max(0, snapshot.events_after - 40)}&limit=100`,
      ),
    ]);
  } catch (error) {
    api.close();
    throw error;
  }
  state.api?.close();
  Object.assign(state, {
    api,
    actor: snapshot.actor,
    project: snapshot.project,
    data,
    events: batch.items,
    cursor: batch.next_cursor,
    page: "overview",
    query: "",
    sync: "online",
    refreshQueue: Promise.resolve(),
  });
  try {
    localStorage.setItem("team-sync.project", projectId);
  } catch {
    /* Storage is optional. */
  }
  history.replaceState(null, "", `#project=${projectId}`);
  document.title = `${state.project.name} — Team Sync`;
  shell();
  liveUpdates(api);
  if (initialized) showCredential(initialized.actor, initialized.token);
}

function disconnect(message = "") {
  clearTimeout(leaseTimer);
  document.body.classList.remove("menu-open");
  state.api?.close();
  state.api = null;
  state.data = {};
  state.events = [];
  state.actor = state.project = null;
  const dialog = document.querySelector("#modal");
  if (dialog.open) dialog.close();
  showLogin(message);
}

async function loadCollections(
  api,
  names = [
    "actors",
    "challenges",
    "tasks",
    "findings",
    "flags",
    "verifications",
    "submissions",
    "artifacts",
    "messages",
  ],
) {
  const values = await Promise.all(
    names.map(async (name) => [
      name,
      await api.all(name === "messages" ? "inbox" : `collections/${name}`),
    ]),
  );
  return Object.fromEntries(values);
}

function refresh(names) {
  const api = state.api;
  const run = async () => {
    if (!api || state.api !== api) return;
    const data = await loadCollections(api, names);
    if (state.api !== api) return;
    Object.assign(state.data, data);
    const actor = state.data.actors.find((item) => item.id === state.actor.id);
    if (actor) state.actor = actor;
    if (!state.actor.active) {
      disconnect("Доступ відкликано власником CTF.");
      return;
    }
    updateNav();
    renderPage();
  };
  state.refreshQueue = state.refreshQueue.catch(() => {}).then(run);
  return state.refreshQueue;
}

async function afterWrite(names) {
  try {
    await refresh(names);
  } catch (error) {
    if (error.name !== "AbortError")
      notify(
        "Зміни збережено. Оновлення екрана повториться після відновлення зв’язку.",
        true,
      );
  }
}

async function liveUpdates(api) {
  let delay = 1000;
  while (state.api === api) {
    try {
      const batch = await api.get(
        `events/wait?after=${state.cursor}&timeout=25`,
      );
      if (state.api !== api) return;
      for (const event of batch.items) {
        if (event.schema_version !== 1)
          throw new ApiError(
            "Версія подій не підтримується. Оновіть Team Sync.",
            { status: 409, code: "unsupported_event" },
          );
      }
      if (batch.items.length) {
        const changes = new Set();
        for (const event of batch.items) {
          const entity = event.type.split(".")[0];
          const collection = {
            task: "tasks",
            challenge: "challenges",
            finding: "findings",
            flag: "flags",
            submission: "submissions",
            message: "messages",
            artifact: "artifacts",
            actor: "actors",
          }[entity];
          if (collection) changes.add(collection);
          if (event.type === "flag.verify") changes.add("verifications");
        }
        if (changes.size) await refresh([...changes]);
        if (state.api !== api) return;
        state.events = [
          ...new Map(
            [...state.events, ...batch.items].map((item) => [item.id, item]),
          ).values(),
        ].slice(-60);
        // Advance only after affected collections have been read successfully.
        state.cursor = batch.next_cursor;
        renderPage();
      }
      state.sync = "online";
      renderSync();
      delay = 1000;
    } catch (error) {
      if (state.api !== api || error.name === "AbortError") return;
      if (error.status === 401 || error.status === 404) {
        disconnect(error.message);
        return;
      }
      if (["invalid_cursor", "unsupported_event"].includes(error.code)) {
        disconnect(error.message);
        return;
      }
      state.sync = "offline";
      renderSync();
      if (
        !(error instanceof Object) ||
        ![undefined, 0, 408, 429, 500, 502, 503, 504].includes(error.status)
      ) {
        notify(error.message, true);
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, delay));
      delay = Math.min(delay * 2, 15000);
    }
  }
}

function shell() {
  nav = el("nav", { class: "navigation", "aria-label": "Розділи CTF" });
  searchInput = el("input", {
    type: "search",
    placeholder: "Пошук у розділі…",
    "aria-label": "Пошук у розділі",
    oninput: (event) => {
      state.query = event.target.value.toLocaleLowerCase("uk").trim();
      renderPage();
    },
  });
  syncNode = el("div", {
    class: "sync-status",
    role: "status",
    "aria-live": "polite",
  });
  pageTitle = el("h1", {});
  pageSubtitle = el("p", {});
  pageAction = el("div", { class: "page-actions" });
  screen = el("div", { class: "page-content" });
  const sidebar = el(
    "aside",
    { class: "sidebar" },
    el(
      "a",
      {
        class: "brand",
        href: "#",
        onclick: (event) => {
          event.preventDefault();
          navigate("overview");
        },
      },
      el("img", { src: "/mark.svg", alt: "", width: "35", height: "35" }),
      el("span", {}, "team", el("strong", {}, "sync")),
    ),
    el(
      "div",
      { class: "project-switch" },
      el(
        "span",
        { class: "project-avatar" },
        state.project.name.slice(0, 2).toUpperCase(),
      ),
      el(
        "div",
        {},
        el("strong", {}, state.project.name),
        el("small", {}, profiles[state.project.profile]),
      ),
    ),
    el("div", { class: "nav-label" }, "ПРОСТІР КОМАНДИ"),
    nav,
    el(
      "div",
      { class: "sidebar-bottom" },
      syncNode,
      el(
        "div",
        { class: "current-actor" },
        el(
          "span",
          { class: "avatar" },
          state.actor.name.slice(0, 2).toUpperCase(),
        ),
        el(
          "div",
          {},
          el("strong", {}, state.actor.name),
          el("small", {}, roles[state.actor.role]),
        ),
        button("", () => disconnect(), {
          tone: "icon-button",
          symbol: "logout",
          "aria-label": "Від’єднатися",
        }),
      ),
    ),
  );
  root.replaceChildren(
    el(
      "div",
      { class: "app-layout" },
      sidebar,
      el(
        "div",
        { class: "workspace" },
        el(
          "header",
          { class: "topbar" },
          button("", () => document.body.classList.toggle("menu-open"), {
            tone: "icon-button mobile-menu",
            symbol: "menu",
            "aria-label": "Відкрити меню",
          }),
          el(
            "div",
            { class: "breadcrumb" },
            el("span", {}, "CTF"),
            el("span", {}, "/"),
            el("strong", {}, state.project.name),
          ),
          el("div", { class: "search" }, icon("search"), searchInput),
          button("", () => guarded(() => refresh(), "Дані оновлено"), {
            tone: "icon-button",
            symbol: "refresh",
            "aria-label": "Оновити дані",
          }),
        ),
        el(
          "main",
          { id: "main", tabindex: "-1" },
          el(
            "div",
            { class: "page-heading" },
            el(
              "div",
              {},
              el("div", { class: "eyebrow" }, "СПІЛЬНИЙ КОНТЕКСТ"),
              pageTitle,
              pageSubtitle,
            ),
            pageAction,
          ),
          screen,
        ),
      ),
    ),
  );
  updateNav();
  renderSync();
  renderPage();
}

function navigate(page) {
  state.page = page;
  state.query = "";
  searchInput.value = "";
  document.body.classList.remove("menu-open");
  updateNav();
  renderPage();
}

function updateNav() {
  if (!nav) return;
  nav.replaceChildren(
    ...Object.entries(sections)
      .filter(([key]) => key !== "flags" || state.project.capabilities.flags)
      .map(([key, [title, symbol]]) => {
        const count =
          key === "messages"
            ? state.data.messages.filter(
                (item) => item.ack_stage !== "processed",
              ).length
            : state.data[key]?.length;
        return el(
          "button",
          {
            type: "button",
            class: `nav-item ${state.page === key ? "active" : ""}`,
            "aria-current": state.page === key ? "page" : false,
            onclick: () => navigate(key),
          },
          icon(symbol),
          el("span", {}, title),
          count > 0 && el("span", { class: "nav-count" }, count),
        );
      }),
  );
}

function renderSync() {
  if (!syncNode) return;
  syncNode.className = `sync-status ${state.sync}`;
  syncNode.replaceChildren(
    el("span", { class: "status-dot" }),
    el(
      "span",
      {},
      state.sync === "online"
        ? "Зміни синхронізуються"
        : "Немає зв’язку · повторюємо",
    ),
  );
}

function renderPage() {
  if (!state.api || !screen) return;
  const [title, , description] = sections[state.page];
  pageTitle.textContent =
    state.page === "overview" ? state.project.name : title;
  pageSubtitle.textContent =
    state.page === "overview"
      ? "Спільна картина роботи вашої команди."
      : description;
  const actions = {
    overview: ["Нова робота", createTask],
    tasks: ["Нова робота", createTask],
    challenges: ["Додати завдання", createChallenge],
    findings: ["Додати знахідку", createFinding],
    flags: ["Додати флаг", createFlag],
    messages: ["Написати", composeMessage],
    artifacts: ["Завантажити файл", uploadArtifact],
    actors: ["Додати учасника", enrollActor],
  };
  const [label, action] = actions[state.page];
  pageAction.replaceChildren(
    ...(canWrite() && (state.page !== "actors" || state.actor.role === "owner")
      ? [button(label, () => action(), { tone: "primary", symbol: "plus" })]
      : []),
  );
  const renderer = {
    overview: overview,
    challenges: challengeList,
    tasks: taskBoard,
    findings: findingList,
    flags: flagList,
    messages: messageList,
    artifacts: artifactList,
    actors: actorList,
  }[state.page];
  screen.replaceChildren(renderer());
  clearTimeout(leaseTimer);
  const nextExpiry = Math.min(
    ...state.data.tasks
      .filter(
        (task) =>
          task.status === "running" && task.lease_until && !expired(task),
      )
      .map((task) => new Date(task.lease_until).getTime()),
  );
  if (Number.isFinite(nextExpiry))
    leaseTimer = setTimeout(
      renderPage,
      Math.min(nextExpiry - Date.now() + 25, 2147483647),
    );
}

function overview() {
  const tasks = state.data.tasks;
  const done = tasks.filter((item) => item.status === "done").length;
  const running = tasks.filter(
    (item) => item.status === "running" && !expired(item),
  ).length;
  const confirmed = state.data.flags.filter(
    (item) => item.verification_status === "confirmed",
  ).length;
  const stats = [
    ["Роботи", tasks.length, `${done} завершено`, "tasks"],
    ["У роботі", running, "Чинні призначення", "clock"],
    [
      "Знахідки",
      state.data.findings.length,
      "Спільні спостереження",
      "finding",
    ],
  ];
  if (state.project.capabilities.flags)
    stats.push([
      "Флаги",
      confirmed,
      `із ${state.data.flags.length} кандидатів підтверджено`,
      "flag",
    ]);
  else
    stats.push([
      "Команда",
      state.data.actors.filter((item) => item.active).length,
      "Учасники з доступом",
      "team",
    ]);
  const activeTasks = tasks.filter(
    (item) => item.status !== "done" && match(item.title, item.description),
  );
  return el(
    "div",
    {},
    el(
      "div",
      { class: "stats" },
      stats.map(([title, value, note, symbol]) =>
        el(
          "article",
          { class: "stat" },
          el("div", { class: "stat-label" }, title, icon(symbol)),
          el("strong", {}, value),
          el("small", {}, note),
        ),
      ),
    ),
    el(
      "div",
      { class: "overview-grid" },
      el(
        "div",
        { class: "overview-main" },
        panel(
          "Поточні роботи",
          activeTasks.length
            ? el(
                "div",
                { class: "task-list" },
                activeTasks.slice(0, 6).map(taskRow),
              )
            : empty(
                "Поки немає відкритих робіт",
                "Додайте першу роботу, щоб команда могла розпочати.",
                canWrite() &&
                  button("Створити роботу", () => createTask(), {
                    symbol: "plus",
                  }),
              ),
          button("Усі роботи", () => navigate("tasks"), {
            tone: "ghost",
            symbol: "arrow",
          }),
        ),
        panel(
          "Останні знахідки",
          state.data.findings.length
            ? el(
                "div",
                { class: "finding-preview" },
                [...state.data.findings]
                  .reverse()
                  .filter((item) => match(item.title, item.body))
                  .slice(0, 3)
                  .map(findingCard),
              )
            : empty(
                "Знахідки з’являться тут",
                "Публікуйте докази, щоб інші учасники могли їх використати.",
              ),
          button("Переглянути", () => navigate("findings"), {
            tone: "ghost",
            symbol: "arrow",
          }),
        ),
      ),
      el(
        "div",
        { class: "overview-side" },
        panel("Події команди", eventFeed()),
        panel(
          "Про CTF",
          el(
            "div",
            { class: "project-info" },
            badge(profiles[state.project.profile], "green"),
            el(
              "p",
              { class: "pre-wrap" },
              state.project.rules ||
                "Власник ще не додав правила та контекст змагання.",
            ),
            button("Скопіювати ID проєкту", () => copy(state.project.id), {
              tone: "ghost",
              symbol: "copy",
            }),
          ),
        ),
      ),
    ),
  );
}

function eventFeed() {
  const descriptions = {
    "project.created": "Ініціалізовано CTF",
    "actor.created": "Додано учасника",
    "actor.revoked": "Відкликано доступ",
    "task.create": "Створено роботу",
    "task.claim": "Роботу взято",
    "task.renew": "Продовжено призначення",
    "task.complete": "Роботу завершено",
    "challenge.create": "Додано завдання",
    "finding.create": "Додано знахідку",
    "flag.create": "Знайдено флаг-кандидат",
    "flag.verify": "Перевірено флаг",
    "submission.create": "Записано результат відправлення",
    "message.send": "Надіслано повідомлення",
    "message.ack": "Підтверджено повідомлення",
    "artifact.published": "Опубліковано файл",
  };
  return el(
    "ol",
    { class: "event-feed" },
    [...state.events]
      .reverse()
      .slice(0, 12)
      .map((event) =>
        el(
          "li",
          {},
          el("span", { class: "event-dot" }),
          el(
            "div",
            {},
            el("strong", {}, descriptions[event.type] || event.type),
            el(
              "small",
              {},
              `${actorName(event.actor_id)} · ${date(event.created_at)}`,
            ),
          ),
        ),
      ),
  );
}

function taskActions(task) {
  if (!canWrite() || task.status === "done") return null;
  if (task.status === "open" || expired(task))
    return button(
      "Взяти роботу",
      () =>
        guarded(async () => {
          await state.api.command(
            "task.claim",
            { expected_version: task.version },
            task.id,
          );
          await refresh(["tasks"]);
        }, "Роботу взято"),
      { tone: "secondary small" },
    );
  if (isMine(task))
    return el(
      "div",
      { class: "inline-actions" },
      button("Завершити", () => completeTask(task), {
        tone: "secondary small",
        symbol: "check",
      }),
      button(
        "",
        () =>
          guarded(async () => {
            await state.api.command(
              "task.renew",
              { expected_version: task.version },
              task.id,
            );
            await refresh(["tasks"]);
          }, "Призначення продовжено"),
        {
          tone: "icon-button small",
          symbol: "clock",
          "aria-label": "Продовжити призначення",
        },
      ),
    );
  return null;
}

function taskRow(task) {
  return el(
    "article",
    { class: "task-row", "data-task-id": task.id },
    el(
      "div",
      { class: "task-title-group" },
      button(task.title, () => taskDetail(task), { tone: "text-button" }),
      metadata(
        challengeName(task.challenge_id),
        task.assignee_id && actorName(task.assignee_id),
      ),
    ),
    taskStatus(task),
    taskActions(task),
  );
}

function taskBoard() {
  const tasks = state.data.tasks.filter((item) =>
    match(item.title, item.description, actorName(item.assignee_id)),
  );
  if (!state.data.tasks.length)
    return empty(
      "Розділіть роботу між учасниками",
      "Створіть кілька конкретних робіт для людей та агентів.",
      canWrite() &&
        button("Нова робота", () => createTask(), {
          tone: "primary",
          symbol: "plus",
        }),
    );
  return el(
    "div",
    { class: "task-board" },
    [
      ["open", "Доступні", "neutral"],
      ["running", "У роботі", "blue"],
      ["done", "Завершені", "green"],
    ].map(([status, title, tone]) => {
      const group = tasks.filter((item) => item.status === status);
      return el(
        "section",
        { class: "board-column" },
        el(
          "header",
          { class: "board-heading" },
          el("h2", {}, el("span", { class: `column-dot ${tone}` }), title),
          badge(group.length),
        ),
        group.map((task) =>
          el(
            "article",
            { class: "task-card", "data-task-id": task.id },
            el(
              "div",
              { class: "card-topline" },
              el(
                "span",
                { class: "overline" },
                challengeName(task.challenge_id),
              ),
              taskStatus(task),
            ),
            button(task.title, () => taskDetail(task), { tone: "text-button" }),
            task.description &&
              el("p", { class: "line-clamp" }, task.description),
            task.assignee_id &&
              metadata(
                actorName(task.assignee_id),
                task.lease_until && `До ${date(task.lease_until)}`,
              ),
            taskActions(task),
          ),
        ),
        !group.length && el("p", { class: "column-empty" }, "Немає робіт"),
      );
    }),
  );
}

function taskDetail(task) {
  const content = el(
    "div",
    { class: "detail-content" },
    taskStatus(task),
    metadata(
      challengeName(task.challenge_id),
      task.assignee_id && actorName(task.assignee_id),
    ),
    el(
      "p",
      { class: "pre-wrap" },
      task.description || "Без додаткового опису.",
    ),
    task.result &&
      el(
        "section",
        {},
        el("h3", {}, "Результат"),
        el("p", { class: "pre-wrap" }, task.result),
      ),
    task.lease_until &&
      el("p", { class: "muted" }, `Призначення до ${date(task.lease_until)}`),
    canWrite() &&
      button(
        "Додати знахідку",
        () => createFinding(task.challenge_id, task.id),
        { symbol: "finding" },
      ),
  );
  modal(task.title, content);
}

function challengeList() {
  const items = state.data.challenges.filter((item) =>
    match(item.title, item.description, item.category),
  );
  if (!state.data.challenges.length)
    return empty(
      "Додайте завдання змагання",
      "Для кожного завдання можна вести окремі роботи, знахідки й флаги.",
      canWrite() &&
        button("Додати завдання", () => createChallenge(), {
          tone: "primary",
          symbol: "plus",
        }),
    );
  return el(
    "div",
    { class: "card-grid" },
    items.map((challenge) => {
      const tasks = state.data.tasks.filter(
        (item) => item.challenge_id === challenge.id,
      );
      const done = tasks.filter((item) => item.status === "done").length;
      return el(
        "article",
        { class: "challenge-card" },
        el(
          "div",
          { class: "card-topline" },
          badge(challenge.category || "Без категорії"),
          icon("target"),
        ),
        button(challenge.title, () => challengeDetail(challenge), {
          tone: "text-button",
        }),
        el(
          "p",
          { class: "line-clamp" },
          challenge.description || "Опис ще не додано.",
        ),
        el(
          "div",
          { class: "challenge-progress" },
          el("span", {}, `${done} / ${tasks.length} робіт завершено`),
          el("progress", {
            max: Math.max(1, tasks.length),
            value: done,
            "aria-label": "Завершені роботи",
          }),
        ),
        metadata(
          `${state.data.findings.filter((item) => item.challenge_id === challenge.id).length} знахідок`,
          state.project.capabilities.flags &&
            `${state.data.flags.filter((item) => item.challenge_id === challenge.id).length} флагів`,
        ),
        canWrite() &&
          button("Додати роботу", () => createTask(challenge.id), {
            tone: "ghost",
            symbol: "plus",
          }),
      );
    }),
  );
}

function challengeDetail(challenge) {
  const tasks = state.data.tasks.filter(
    (item) => item.challenge_id === challenge.id,
  );
  const findings = state.data.findings.filter(
    (item) => item.challenge_id === challenge.id,
  );
  modal(
    challenge.title,
    el(
      "div",
      { class: "detail-content" },
      badge(challenge.category || "Без категорії"),
      el("p", { class: "pre-wrap" }, challenge.description || "Без опису."),
      heading(
        "Роботи",
        null,
        canWrite() &&
          button("Додати", () => createTask(challenge.id), { symbol: "plus" }),
      ),
      tasks.length
        ? tasks.map(taskRow)
        : el("p", { class: "muted" }, "Робіт ще немає."),
      heading("Знахідки"),
      findings.length
        ? findings.map(findingCard)
        : el("p", { class: "muted" }, "Знахідок ще немає."),
    ),
    { wide: true },
  );
}

function findingCard(finding) {
  const linkedArtifacts = [
    ...new Set(
      [...finding.evidence.matchAll(/artifact:([0-9a-f-]{36})/g)].map(
        (match) => match[1],
      ),
    ),
  ]
    .map((id) => state.data.artifacts.find((artifact) => artifact.id === id))
    .filter(Boolean);
  return el(
    "article",
    { class: "finding-card" },
    el(
      "div",
      { class: "card-topline" },
      badge(
        kinds[finding.kind],
        finding.kind === "failed_attempt"
          ? "amber"
          : finding.kind === "result"
            ? "green"
            : "neutral",
      ),
      el("small", { class: "muted" }, date(finding.created_at)),
    ),
    el("h3", {}, finding.title),
    el("p", { class: "pre-wrap" }, finding.body),
    el(
      "div",
      { class: "evidence" },
      el("span", { class: "overline" }, "ДОКАЗИ / ДЖЕРЕЛО"),
      el("p", { class: "pre-wrap mono" }, finding.evidence),
      linkedArtifacts.length > 0 &&
        el(
          "div",
          { class: "inline-actions" },
          linkedArtifacts.map((artifact) =>
            button(artifact.name, () => downloadArtifact(artifact), {
              tone: "ghost small",
              symbol: "download",
            }),
          ),
        ),
    ),
    metadata(actorName(finding.author_id), challengeName(finding.challenge_id)),
  );
}

function findingList() {
  const items = state.data.findings
    .filter((item) => match(item.title, item.body, item.evidence))
    .reverse();
  return items.length
    ? el("div", { class: "findings-grid" }, items.map(findingCard))
    : empty(
        state.query ? "Знахідок за запитом немає" : "Зберігайте спільні знання",
        "Додайте спостереження з доказами або опишіть підхід, який не спрацював.",
      );
}

function flagList() {
  const items = state.data.flags
    .filter((item) =>
      match(
        item.value,
        item.target,
        item.round,
        challengeName(item.challenge_id),
      ),
    )
    .reverse();
  if (!items.length)
    return empty(
      state.query ? "Флагів за запитом немає" : "Флагів поки немає",
      "Кожен новий кандидат спочатку має статус «Не перевірено».",
    );
  return el(
    "div",
    { class: "table-wrap panel" },
    el(
      "table",
      {},
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          [
            "Флаг / завдання",
            "Перевірка",
            "Відправлення",
            "Ціль / раунд",
            "",
          ].map((title) => el("th", { scope: "col" }, title)),
        ),
      ),
      el(
        "tbody",
        {},
        items.map((flag) => {
          const submissions = state.data.submissions.filter(
            (item) => item.flag_id === flag.id,
          );
          const outcome = submissions.at(-1)?.outcome;
          return el(
            "tr",
            { "data-flag-id": flag.id },
            el(
              "td",
              {},
              button(flag.value, () => flagDetail(flag), {
                tone: "text-button mono flag-value",
              }),
              el(
                "small",
                { class: "muted block" },
                challengeName(flag.challenge_id),
              ),
            ),
            el(
              "td",
              {},
              flagBadge(flag),
              flag.expires_at &&
                new Date(flag.expires_at) <= new Date() &&
                el("small", { class: "muted block" }, "Строк дії минув"),
            ),
            el(
              "td",
              {},
              outcome
                ? badge(
                    outcomes[outcome],
                    outcome === "accepted" ? "green" : "neutral",
                  )
                : el("span", { class: "muted" }, "Не записано"),
            ),
            el(
              "td",
              {},
              flag.target || "—",
              el(
                "small",
                { class: "muted block" },
                flag.round ? `Раунд ${flag.round}` : "Без раунду",
              ),
            ),
            el(
              "td",
              {},
              button("", () => copy(flag.value), {
                tone: "icon-button",
                symbol: "copy",
                "aria-label": "Скопіювати флаг",
              }),
            ),
          );
        }),
      ),
    ),
  );
}

function flagDetail(flag) {
  const checks = state.data.verifications.filter(
    (item) => item.flag_id === flag.id,
  );
  const submissions = state.data.submissions.filter(
    (item) => item.flag_id === flag.id,
  );
  modal(
    "Картка флага",
    el(
      "div",
      { class: "detail-content" },
      el("div", { class: "flag-display mono" }, flag.value),
      flagBadge(flag),
      metadata(
        challengeName(flag.challenge_id),
        flag.target,
        flag.round && `Раунд ${flag.round}`,
        flag.expires_at && `Діє до ${date(flag.expires_at)}`,
      ),
      el(
        "div",
        { class: "inline-actions" },
        canVerify() &&
          button("Перевірити флаг", () => verifyFlag(flag), {
            symbol: "shield",
          }),
        canWrite() &&
          button("Записати відправлення", () => recordSubmission(flag), {
            symbol: "flag",
          }),
      ),
      heading("Історія перевірок"),
      checks.length
        ? checks.map((item) =>
            el(
              "article",
              { class: "history-item" },
              badge(
                flagStates[item.status],
                item.status === "confirmed" ? "green" : "red",
              ),
              el("p", { class: "pre-wrap" }, item.evidence),
              metadata(
                actorName(item.author_id),
                item.method,
                date(item.created_at),
              ),
            ),
          )
        : el("p", { class: "muted" }, "Флаг ще не перевіряли."),
      heading("Відправлення на платформу"),
      submissions.length
        ? submissions.map((item) =>
            el(
              "article",
              { class: "history-item" },
              badge(outcomes[item.outcome]),
              el("p", { class: "pre-wrap" }, item.evidence),
              metadata(actorName(item.author_id), date(item.created_at)),
            ),
          )
        : el("p", { class: "muted" }, "Спроб відправлення не записано."),
    ),
  );
}

function messageList() {
  const messages = state.data.messages
    .filter((item) => match(item.body, actorName(item.sender_id)))
    .reverse();
  if (!messages.length)
    return empty(
      "Вхідних повідомлень немає",
      "Тут з’являються повідомлення для вас і для всієї команди.",
    );
  return el(
    "div",
    { class: "messages" },
    messages.map((message) =>
      el(
        "article",
        {
          class: `message-card ${message.ack_stage === "processed" ? "processed" : ""}`,
          "data-message-id": message.id,
        },
        el(
          "div",
          { class: "card-topline" },
          el(
            "div",
            { class: "message-author" },
            el(
              "span",
              { class: "avatar" },
              actorName(message.sender_id).slice(0, 2).toUpperCase(),
            ),
            el(
              "div",
              {},
              el("strong", {}, actorName(message.sender_id)),
              el(
                "small",
                { class: "muted block" },
                message.recipient_id ? "Особисте повідомлення" : "Усій команді",
              ),
            ),
          ),
          el("small", { class: "muted" }, date(message.created_at)),
        ),
        el("p", { class: "pre-wrap" }, message.body),
        el(
          "footer",
          { class: "card-footer" },
          badge(
            { received: "Отримано", processed: "Опрацьовано" }[
              message.ack_stage
            ] || "Нове",
            message.ack_stage === "processed" ? "green" : "blue",
          ),
          el(
            "div",
            { class: "inline-actions" },
            !message.ack_stage &&
              button("Отримано", () => ackMessage(message, "received"), {
                tone: "ghost small",
              }),
            message.ack_stage !== "processed" &&
              button("Опрацьовано", () => ackMessage(message, "processed"), {
                tone: "secondary small",
                symbol: "check",
              }),
          ),
        ),
      ),
    ),
  );
}

function artifactList() {
  const artifacts = state.data.artifacts
    .filter((item) => match(item.name, item.sha256))
    .reverse();
  return el(
    "div",
    {},
    canWrite() &&
      el(
        "button",
        { type: "button", class: "upload-zone", onclick: uploadArtifact },
        icon("upload"),
        el("strong", {}, "Додати файл до спільного простору"),
        el("span", {}, "Бінарники, архіви, скрипти та логи · до 64 MiB"),
      ),
    artifacts.length
      ? el(
          "div",
          { class: "artifact-list" },
          artifacts.map((artifact) =>
            el(
              "article",
              { class: "artifact-card" },
              el("span", { class: "file-symbol" }, icon("file")),
              el(
                "div",
                { class: "artifact-info" },
                el("h3", {}, artifact.name),
                metadata(
                  size(artifact.size),
                  actorName(artifact.author_id),
                  date(artifact.created_at),
                ),
                el(
                  "code",
                  { class: "checksum", title: artifact.sha256 },
                  `SHA-256 ${artifact.sha256}`,
                ),
              ),
              el(
                "div",
                { class: "inline-actions" },
                button("Завантажити", () => downloadArtifact(artifact), {
                  symbol: "download",
                }),
                button("", () => copy(`artifact:${artifact.id}`), {
                  tone: "icon-button",
                  symbol: "copy",
                  "aria-label": "Скопіювати посилання на файл",
                }),
              ),
            ),
          ),
        )
      : empty(
          "Спільних файлів ще немає",
          "Опублікуйте матеріали, щоб команда могла завантажити їх на свої пристрої.",
        ),
  );
}

function downloadArtifact(artifact) {
  return guarded(async () => {
    const blob = await state.api.download(artifact);
    saveBlob(blob, artifact.name);
  }, "Файл перевірено й завантажено");
}

function actorList() {
  const actors = state.data.actors.filter((item) =>
    match(item.name, item.role, item.capabilities.join(" ")),
  );
  return el(
    "div",
    { class: "card-grid" },
    actors.map((actor) =>
      el(
        "article",
        { class: "actor-card" },
        el(
          "div",
          { class: "card-topline" },
          el(
            "span",
            { class: `avatar large ${actor.kind}` },
            actor.kind === "agent"
              ? icon("tasks")
              : actor.name.slice(0, 2).toUpperCase(),
          ),
          badge(
            actor.active ? "Доступ активний" : "Доступ відкликано",
            actor.active ? "green" : "neutral",
          ),
        ),
        el(
          "h3",
          {},
          actor.name,
          actor.id === state.actor.id &&
            el("small", { class: "muted" }, " · ви"),
        ),
        metadata(
          actor.kind === "agent" ? "Агент" : "Людина",
          roles[actor.role],
        ),
        actor.capabilities.length > 0 &&
          el(
            "div",
            { class: "tags" },
            actor.capabilities.map((item) => badge(item)),
          ),
        el(
          "small",
          { class: "muted" },
          `${state.data.tasks.filter((task) => task.assignee_id === actor.id && task.status === "running" && !expired(task)).length} чинних призначень`,
        ),
        el(
          "div",
          { class: "inline-actions actor-actions" },
          button("Написати", () => composeMessage(actor.id), {
            tone: "ghost small",
            symbol: "message",
            disabled: !canWrite() || !actor.active,
          }),
          state.actor.role === "owner" &&
            actor.active &&
            actor.id !== state.actor.id &&
            button("Відкликати доступ", () => revokeActor(actor), {
              tone: "danger-link small",
            }),
        ),
      ),
    ),
  );
}

function challengeOptions() {
  return [
    ["", "Без прив’язки до завдання"],
    ...state.data.challenges.map((item) => [item.id, item.title]),
  ];
}

function createProject() {
  openForm(
    "Новий CTF",
    [
      field("name", "Назва CTF", {
        required: true,
        maxlength: 200,
        placeholder: "Наприклад, Autumn CTF",
      }),
      field("owner_name", "Ваше ім’я", { required: true, maxlength: 200 }),
      field("profile", "Формат", { options: Object.entries(profiles) }),
      field("flags", "У цьому CTF є флаги", {
        type: "checkbox",
        checked: true,
      }),
      field("rules", "Правила та контекст", {
        type: "textarea",
        placeholder: "Час, посилання на змагання, домовленості команди…",
        maxlength: 100000,
      }),
      field("bootstrap", "Ключ ініціалізації сервера", {
        type: "password",
        required: true,
        autocomplete: "off",
      }),
    ],
    "Створити CTF",
    async (values) => {
      const bootstrap = new ApiClient(null, values.bootstrap.trim());
      let created;
      try {
        created = await bootstrap.request("/api/v1/projects", {
          method: "POST",
          body: {
            name: values.name,
            owner_name: values.owner_name,
            profile: values.profile,
            capabilities: { flags: values.flags === "on" },
            rules: values.rules,
          },
        });
      } finally {
        bootstrap.close();
      }
      try {
        await connect(created.project.id, created.token, created);
      } catch {
        showCredential(created.actor, created.token, created.project.id);
        notify(
          "CTF створено. Збережіть токен і підключіться після відновлення зв’язку.",
          true,
        );
      }
    },
    {
      hint: "Ключ ініціалізації видає адміністратор сервера. Для створеного CTF ви отримаєте окремий особистий токен.",
      nonReplayable: true,
    },
  );
}

function createChallenge() {
  openForm(
    "Додати завдання CTF",
    [
      field("title", "Назва завдання", { required: true, maxlength: 200 }),
      field("category", "Категорія", {
        placeholder: "Web, Pwn, Crypto…",
        maxlength: 100,
      }),
      field("description", "Опис", { type: "textarea", maxlength: 100000 }),
    ],
    "Додати завдання",
    async (values) => {
      await state.api.command("challenge.create", {
        ...values,
        category: values.category || null,
      });
      await afterWrite(["challenges"]);
      notify("Завдання додано");
    },
  );
}

function createTask(challengeId = "") {
  openForm(
    "Нова робота",
    [
      field("title", "Що потрібно зробити", {
        required: true,
        maxlength: 200,
        placeholder: "Конкретна робота для одного виконавця",
      }),
      field("challenge_id", "Завдання CTF", {
        options: challengeOptions(),
        value: challengeId || "",
      }),
      field("description", "Контекст і очікуваний результат", {
        type: "textarea",
        maxlength: 100000,
      }),
    ],
    "Створити роботу",
    async (values) => {
      await state.api.command("task.create", {
        ...values,
        challenge_id: values.challenge_id || null,
      });
      await afterWrite(["tasks"]);
      notify("Роботу створено");
    },
  );
}

function completeTask(task) {
  openForm(
    "Завершити роботу",
    [
      field("result", "Результат роботи", {
        type: "textarea",
        required: true,
        maxlength: 100000,
        placeholder: "Що зроблено, який результат і де докази?",
      }),
    ],
    "Завершити",
    async (values) => {
      await state.api.command(
        "task.complete",
        { expected_version: task.version, result: values.result },
        task.id,
      );
      await afterWrite(["tasks"]);
      notify("Роботу завершено");
    },
    { hint: task.title },
  );
}

function createFinding(challengeId = "", taskId = "") {
  const challengeField = field("challenge_id", "Завдання CTF", {
    options: challengeOptions(),
    value: challengeId || "",
  });
  const taskField = field("task_id", "Пов’язана робота", {
    options: [
      ["", "Без прив’язки до роботи"],
      ...state.data.tasks.map((item) => [item.id, item.title]),
    ],
    value: taskId || "",
  });
  taskField.querySelector("select").addEventListener("change", (event) => {
    const task = state.data.tasks.find(
      (item) => item.id === event.target.value,
    );
    if (task)
      challengeField.querySelector("select").value = task.challenge_id || "";
  });
  openForm(
    "Додати знахідку",
    [
      field("title", "Назва", { required: true, maxlength: 200 }),
      field("kind", "Тип знахідки", { options: Object.entries(kinds) }),
      challengeField,
      taskField,
      field("body", "Що дізналися", {
        type: "textarea",
        required: true,
        maxlength: 100000,
      }),
      field("evidence", "Докази та джерела", {
        type: "textarea",
        required: true,
        maxlength: 100000,
        placeholder: "Посилання, вивід інструменту або artifact:<UUID>",
      }),
    ],
    "Опублікувати",
    async (values) => {
      await state.api.command("finding.create", {
        ...values,
        challenge_id: values.challenge_id || null,
        task_id: values.task_id || null,
      });
      await afterWrite(["findings"]);
      notify("Знахідку опубліковано");
    },
  );
}

function createFlag() {
  openForm(
    "Додати флаг-кандидат",
    [
      field("value", "Значення флага", {
        required: true,
        maxlength: 4096,
        autocomplete: "off",
        spellcheck: "false",
      }),
      field("challenge_id", "Завдання CTF", { options: challengeOptions() }),
      field("target", "Ціль / команда", {
        maxlength: 200,
        placeholder: "Необов’язково",
      }),
      field("round", "Раунд", { maxlength: 100, placeholder: "Необов’язково" }),
      field("expires_at", "Строк дії", {
        type: "datetime-local",
        hint: "Час у вашому локальному часовому поясі. Необов’язково.",
      }),
    ],
    "Додати флаг",
    async (values) => {
      await state.api.command("flag.create", {
        ...values,
        challenge_id: values.challenge_id || null,
        target: values.target || null,
        round: values.round || null,
        expires_at: values.expires_at
          ? new Date(values.expires_at).toISOString()
          : null,
      });
      await afterWrite(["flags"]);
      notify("Флаг додано як неперевірений");
    },
  );
}

function verifyFlag(flag) {
  openForm(
    "Перевірити флаг",
    [
      field("status", "Висновок", {
        options: [
          ["confirmed", "Підтверджено"],
          ["rejected", "Спростовано"],
        ],
      }),
      field("method", "Спосіб перевірки", {
        options: [
          ["manual", "Ручна перевірка"],
          ["local_checker", "Локальний перевіряльник"],
          ["platform", "Відповідь CTF-платформи"],
        ],
      }),
      field("evidence", "Підстава та докази", {
        type: "textarea",
        required: true,
        maxlength: 100000,
      }),
    ],
    "Зберегти перевірку",
    async (values) => {
      await state.api.command(
        "flag.verify",
        { ...values, expected_version: flag.version },
        flag.id,
      );
      await afterWrite(["flags", "verifications"]);
      notify("Перевірку збережено");
    },
    {
      hint: "Ви записуєте результат уже виконаної перевірки. Попередні перевірки залишаються в історії.",
    },
  );
}

function recordSubmission(flag) {
  openForm(
    "Записати результат відправлення",
    [
      field("outcome", "Відповідь платформи", {
        options: Object.entries(outcomes),
      }),
      field("evidence", "Відповідь або обставини спроби", {
        type: "textarea",
        required: true,
        maxlength: 100000,
      }),
    ],
    "Зберегти результат",
    async (values) => {
      await state.api.command("submission.create", values, flag.id);
      await afterWrite(["submissions"]);
      notify("Результат відправлення збережено");
    },
    {
      hint: "Ця дія записує спробу, яку ви вже виконали. Сам флаг на платформу не надсилається.",
    },
  );
}

function composeMessage(recipientId = "") {
  openForm(
    "Написати повідомлення",
    [
      field("recipient_id", "Кому", {
        options: [
          ["", "Усій команді"],
          ...state.data.actors
            .filter((item) => item.active)
            .map((item) => [item.id, item.name]),
        ],
        value: recipientId || "",
      }),
      field("body", "Повідомлення", {
        type: "textarea",
        required: true,
        maxlength: 100000,
        placeholder: "Передайте контекст, знахідку або конкретне доручення.",
      }),
    ],
    "Надіслати",
    async (values) => {
      await state.api.command("message.send", {
        ...values,
        recipient_id: values.recipient_id || null,
      });
      await afterWrite(["messages"]);
      notify("Повідомлення надіслано");
    },
  );
}

function ackMessage(message, stage) {
  guarded(
    async () => {
      await state.api.command("message.ack", { stage }, message.id);
      await refresh(["messages"]);
    },
    stage === "processed"
      ? "Повідомлення опрацьовано"
      : "Отримання підтверджено",
  );
}

function uploadArtifact() {
  const key = commandId();
  openForm(
    "Опублікувати файл",
    [
      field("file", "Виберіть файл", {
        type: "file",
        required: true,
        hint: "До 64 MiB за стандартних налаштувань сервера.",
      }),
    ],
    "Опублікувати",
    async (values) => {
      if (values.file.size > 64 * 1024 * 1024)
        throw new Error("Файл перевищує 64 MiB.");
      await state.api.upload(values.file, key);
      await afterWrite(["artifacts"]);
      notify("Файл доступний команді");
    },
  );
}

function enrollActor() {
  openForm(
    "Додати учасника або агента",
    [
      field("name", "Ім’я / назва агента", { required: true, maxlength: 200 }),
      field("kind", "Тип", {
        options: [
          ["agent", "Агент"],
          ["human", "Людина"],
        ],
      }),
      field("role", "Роль", {
        options: Object.entries(roles),
        value: "worker",
      }),
      field("owner_id", "Власник агента", {
        options: [
          ["", "Без прив’язки"],
          ...state.data.actors
            .filter((item) => item.kind === "human")
            .map((item) => [item.id, item.name]),
        ],
        value: state.actor.kind === "human" ? state.actor.id : "",
      }),
      field("capabilities", "Спеціалізації", {
        placeholder: "web, reverse, crypto — через кому",
        maxlength: 1000,
      }),
    ],
    "Видати доступ",
    async (values) => {
      const issued = await state.api.request(state.api.path("actors"), {
        method: "POST",
        body: {
          ...values,
          owner_id: values.kind === "agent" ? values.owner_id || null : null,
          capabilities: values.capabilities
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
        },
      });
      showCredential(issued.actor, issued.token);
      await afterWrite(["actors"]);
    },
    {
      hint: "Кожному незалежному агенту потрібен окремий токен. Координатор може планувати роботи та перевіряти флаги.",
      nonReplayable: true,
    },
  );
}

function showCredential(actor, token, projectId = state.project.id) {
  const tokenField = field("issued-token", "Особистий токен", {
    type: "password",
    readonly: true,
    value: token,
    autocomplete: "off",
  });
  const mcpConfig = {
    mcpServers: {
      "team-sync": {
        command: "uv",
        args: ["run", "--directory", "/path/to/team-sync", "team-sync-mcp"],
        env: {
          TEAM_SYNC_URL: location.origin,
          TEAM_SYNC_PROJECT_ID: projectId,
          TEAM_SYNC_TOKEN: token,
        },
      },
    },
  };
  const dialog = modal(
    `Доступ для ${actor.name}`,
    el(
      "div",
      { class: "detail-content" },
      badge(roles[actor.role], "green"),
      el(
        "p",
        {},
        "Збережіть токен перед закриттям. Сервер показує його лише під час видачі.",
      ),
      field("issued-project", "Ідентифікатор CTF", {
        readonly: true,
        value: projectId,
      }),
      tokenField,
      el(
        "div",
        { class: "inline-actions" },
        button("Скопіювати токен", () => copy(token), { symbol: "copy" }),
        button(
          "Показати / приховати",
          () => {
            const input = tokenField.querySelector("input");
            input.type = input.type === "password" ? "text" : "password";
          },
          { tone: "ghost" },
        ),
      ),
      actor.kind === "agent" &&
        el(
          "section",
          {},
          el("h3", {}, "Підключення MCP"),
          el(
            "p",
            { class: "muted" },
            "У конфігурації замініть /path/to/team-sync на шлях до репозиторію на пристрої учасника. Файл містить цей особистий токен.",
          ),
          button(
            "Зберегти MCP-конфігурацію",
            () =>
              saveBlob(
                new Blob([JSON.stringify(mcpConfig, null, 2)], {
                  type: "application/json",
                }),
                "team-sync-mcp.json",
              ),
            { symbol: "download" },
          ),
        ),
      button("Я зберіг токен", () => dialog.close(), {
        tone: "primary full-width",
      }),
    ),
    {
      onClose: () => {
        token = "";
        mcpConfig.mcpServers["team-sync"].env.TEAM_SYNC_TOKEN = "";
      },
    },
  );
}

function revokeActor(actor) {
  openForm(
    "Відкликати доступ",
    [],
    "Відкликати",
    async () => {
      await state.api.request(state.api.path(`actors/${actor.id}/revoke`), {
        method: "POST",
      });
      await afterWrite(["actors"]);
      notify("Доступ відкликано");
    },
    {
      hint: `Токен ${actor.name} перестане працювати. Вже запущені локальні процеси ця дія не зупиняє.`,
    },
  );
}

async function guarded(action, success) {
  try {
    await action();
    if (success) notify(success);
  } catch (error) {
    if (error.name === "AbortError") return;
    notify(
      error.message +
        (error.uncertain
          ? " Результат невідомий; повторіть дію з тим самим вмістом."
          : ""),
      true,
    );
    if (error.status === 401) disconnect(error.message);
    else if (error.status === 409) await refresh().catch(() => {});
  }
}

showLogin();
