import { el, button, badge, field, date, saveBlob, modal } from "./ui.js";

export const eventLabels = {
  "project.created": "CTF створено",
  "project.update": "Налаштування CTF змінено",
  "actor.created": "Учасника додано",
  "actor.update": "Учасника змінено",
  "actor.rename": "Учасника перейменовано",
  "actor.revoked": "Доступ відкликано",
  "challenge.create": "Завдання додано",
  "challenge.update": "Завдання змінено",
  "task.create": "Роботу створено",
  "task.update": "Роботу змінено",
  "task.claim": "Роботу прийнято",
  "task.complete": "Роботу завершено",
  "task.release": "Роботу звільнено",
  "task.assign": "Роботу доручено",
  "task.override": "Координатор змінив активне призначення",
  "task.renew": "Призначення продовжено",
  "finding.create": "Знахідку опубліковано",
  "finding.update": "Знахідку змінено",
  "flag.create": "Флаг додано",
  "flag.update": "Флаг змінено",
  "flag.verify": "Флаг перевірено",
  "verification.update": "Перевірку змінено",
  "submission.create": "Відправлення записано",
  "submission.update": "Відправлення змінено",
  "message.send": "Повідомлення надіслано",
  "message.update": "Повідомлення змінено",
  "message.ack": "Повідомлення підтверджено",
  "message.duplicate": "Повтор повідомлення об’єднано",
  "artifact.published": "Артефакт опубліковано",
  "artifact.update": "Артефакт змінено",
  "artifact.archive": "Артефакт архівовано",
  "artifact.restore": "Артефакт відновлено",
  "artifact.review.request": "Аналіз запрошено",
  "artifact.review.decide": "Рішення людини щодо аналізу",
  "artifact.review.start": "Аналіз почато",
  "artifact.review.complete": "Звіт аналізу опубліковано",
  "activity.report": "Поточний крок оновлено",
  "activity.update": "Поточний крок змінено",
  "log.create": "Запис журналу",
};
const collectionLabels = {
  projects: "CTF",
  actors: "Учасники",
  challenges: "Завдання CTF",
  tasks: "Роботи",
  findings: "Знахідки",
  flags: "Флаги",
  verifications: "Перевірки",
  submissions: "Відправлення",
  messages: "Повідомлення",
  artifacts: "Файли",
  activities: "Статуси",
  logs: "Логи",
};
const tones = {
  debug: "neutral",
  info: "blue",
  warning: "amber",
  error: "red",
};

export class JournalView {
  constructor(getState, render, openResource, history) {
    Object.assign(this, {
      getState,
      render,
      openResource,
      history,
      filters: {},
      previous: [],
      serial: 0,
    });
  }
  resetRecord(collection, id) {
    this.filters = { resource_id: id };
    this.before = null;
    this.previous = [];
    this.data = null;
  }
  async load(reset = false) {
    const state = this.getState();
    if (!state.api) return;
    if (reset) {
      this.before = null;
      this.previous = [];
    }
    const serial = ++this.serial;
    this.loading = true;
    this.error = null;
    const params = new URLSearchParams({ limit: 20, max_chars: 30000 });
    for (const [key, value] of Object.entries(this.filters))
      if (value) params.set(key, value);
    if (this.before) params.set("before", this.before);
    if (state.query) params.set("query", state.query);
    try {
      const data = await state.api.get(`journal?${params}`);
      if (this.getState().api === state.api && serial === this.serial)
        this.data = data;
    } catch (error) {
      if (serial === this.serial) this.error = error.message;
    } finally {
      if (serial === this.serial) {
        this.loading = false;
        if (this.getState().page === "journal") this.render();
      }
    }
  }
  view() {
    const state = this.getState();
    if (this.api !== state.api) {
      this.error = null;
      this.loading = false;
      this.api = state.api;
      this.data = null;
      this.before = null;
      this.previous = [];
      this.filters = {};
    }
    if (this.query !== state.query) {
      this.query = state.query;
      this.data = null;
      this.before = null;
      this.previous = [];
      this.load();
    } else if (!this.data && !this.loading && !this.error) this.load();
    const select = (name, label, options) => {
      const control = field(name, label, {
        options: [["", "Усі"], ...options],
        value: this.filters[name] || "",
      });
      control.querySelector("select").addEventListener("change", (event) => {
        this.filters[name] = event.target.value;
        this.load(true);
        this.render();
      });
      return control;
    };
    const routine = field(
      "include_routine",
      "Показувати продовження, підтвердження і статуси",
      {
        type: "checkbox",
        checked: Boolean(this.filters.include_routine),
      },
    );
    routine.querySelector("input").addEventListener("change", (event) => {
      this.filters.include_routine = event.target.checked || "";
      this.load(true);
      this.render();
    });
    return el(
      "div",
      { class: "journal-view" },
      el(
        "div",
        { class: "journal-filters" },
        select(
          "actor_id",
          "Учасник",
          state.data.actors.map((a) => [a.id, a.name]),
        ),
        select("collection", "Розділ", Object.entries(collectionLabels)),
        select("event_type", "Дія", Object.entries(eventLabels)),
        select(
          "level",
          "Рівень лога",
          Object.keys(tones).map((level) => [level, level]),
        ),
      ),
      routine,
      el(
        "div",
        { class: "inline-actions" },
        button(
          "Оновити",
          () => {
            this.load();
            this.render();
          },
          { disabled: this.loading, symbol: "refresh" },
        ),
        button(
          "Найновіші",
          () => {
            this.load(true);
            this.render();
          },
          { disabled: this.loading || !this.before },
        ),
        button(
          "Зберегти сторінку JSON",
          () =>
            saveBlob(
              new Blob([JSON.stringify(this.data, null, 2)], {
                type: "application/json",
              }),
              "team-sync-journal.json",
            ),
          { disabled: !this.data, symbol: "download" },
        ),
        this.filters.resource_id &&
          button("Скинути запис", () => {
            delete this.filters.resource_id;
            this.load(true);
            this.render();
          }),
        this.loading &&
          el("small", { class: "muted", role: "status" }, "Оновлюємо журнал…"),
      ),
      this.error && el("p", { role: "alert" }, this.error),
      !this.loading &&
        this.data &&
        !this.data.items.length &&
        el("p", { class: "muted" }, "За цими фільтрами записів немає."),
      el(
        "div",
        { class: "journal-list", "aria-busy": this.loading },
        (this.data?.items || []).map((entry) =>
          el(
            "article",
            { class: "journal-entry", "data-journal-seq": entry.seq },
            el(
              "header",
              { class: "card-topline" },
              el("strong", {}, eventLabels[entry.type] || entry.type),
              entry.log && badge(entry.log.level, tones[entry.log.level]),
            ),
            el(
              "small",
              { class: "muted" },
              `${entry.actor.name} · ${date(entry.created_at)} · #${entry.seq}`,
            ),
            entry.log
              ? el(
                  "pre",
                  { class: "journal-body" },
                  entry.log.body,
                  entry.log.body_truncated ? "\n… Повний текст у записі." : "",
                )
              : el("p", {}, entry.resource.label),
            el(
              "div",
              { class: "inline-actions" },
              entry.resource.id &&
                entry.resource.readable &&
                button(
                  "Відкрити запис",
                  () =>
                    this.openResource(
                      entry.resource.collection,
                      entry.resource.id,
                    ),
                  { tone: "ghost small" },
                ),
              entry.revision_id &&
                entry.resource.readable &&
                button(
                  "Історія правок",
                  () =>
                    this.history(entry.resource.collection, entry.resource.id),
                  { tone: "ghost small" },
                ),
              el("code", { class: "muted journal-type" }, entry.type),
            ),
          ),
        ),
      ),
      el(
        "div",
        { class: "inline-actions" },
        button(
          "Новіші",
          () => {
            this.before = this.previous.pop();
            this.load();
            this.render();
          },
          { disabled: this.loading || !this.previous.length },
        ),
        button(
          "Старіші",
          () => {
            this.previous.push(this.before);
            this.before = this.data.next_before;
            this.load();
            this.render();
          },
          { disabled: this.loading || !this.data?.has_more },
        ),
      ),
      el(
        "p",
        { class: "muted" },
        "Історія дій та явні записи учасників. Приватні тексти повідомлень у журнал не потрапляють. Пошук: текст логів, ім’я учасника або тип дії.",
      ),
    );
  }
}

export async function logDetail(state, id) {
  const { item } = await state.api.get(`resources/logs/${id}`);
  const author = state.data.actors.find((a) => a.id === item.author_id);
  modal(
    "Запис журналу",
    el(
      "div",
      { class: "detail-content" },
      badge(item.level, tones[item.level]),
      el(
        "small",
        { class: "muted" },
        `${author?.name || "Учасник"} · ${date(item.created_at)}`,
      ),
      el("pre", { class: "journal-body" }, item.body),
      el(
        "p",
        { class: "muted" },
        "Записи журналу незмінні. Виправлення публікують окремим записом.",
      ),
    ),
    { wide: true },
  );
}
