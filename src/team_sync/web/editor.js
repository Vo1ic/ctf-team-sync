import { el, button, field, openForm, modal, notify, date } from "./ui.js";

const labels = {
  name: "Назва",
  notes: "Нотатки",
  points: "Бали",
  source_url: "Посилання на платформі",
  due_at: "Дедлайн",
  title: "Назва",
  description: "Опис",
  rules: "Правила та контекст",
  profile: "Формат CTF",
  capabilities: "Спеціалізації",
  role: "Роль",
  owner_id: "Власник агента",
  active: "Доступ активний",
  challenge_id: "Завдання CTF",
  task_id: "Робота",
  artifact_id: "Артефакт",
  requested_assignee_id: "Кому доручено",
  status: "Стан",
  result: "Результат",
  body: "Текст",
  evidence: "Докази",
  kind: "Тип",
  value: "Значення флага",
  target: "Ціль",
  round: "Раунд",
  expires_at: "Діє до",
  method: "Спосіб перевірки",
  outcome: "Результат відправлення",
  priority: "Важливість",
  group_key: "Група статусів",
  media_type: "Тип файла",
  requires_review: "Аналіз лише з дозволу людини",
  state: "Поточний стан",
  detail: "Поточний крок",
  ttl_seconds: "Актуальність статусу, секунд",
  category: "Категорія",
};
const collections = {
  projects: "CTF",
  actors: "учасника",
  challenges: "завдання CTF",
  tasks: "роботу",
  findings: "знахідку / README",
  flags: "флаг",
  verifications: "перевірку",
  submissions: "відправлення",
  messages: "повідомлення",
  artifacts: "артефакт",
  activities: "поточний статус",
};
const enumLabels = {
  owner: "Власник",
  coordinator: "Координатор",
  worker: "Учасник",
  observer: "Спостерігач",
  jeopardy: "Jeopardy",
  attack_defense: "Attack–Defense",
  custom: "Власний формат",
  open: "Вільна",
  running: "У роботі",
  done: "Завершена",
  confirmed: "Підтверджено",
  rejected: "Відхилено",
  accepted: "Прийнято",
  unknown: "Невідомо",
  unverified: "Не перевірено",
  observation: "Спостереження",
  hypothesis: "Гіпотеза",
  result: "Результат",
  failed_attempt: "Невдала спроба",
  manual: "Вручну",
  local_checker: "Локальний checker",
  platform: "Платформа",
  low: "Низька",
  normal: "Звичайне",
  urgent: "Термінове",
  note: "Нотатка",
  question: "Запитання",
  assignment: "Доручення",
  status: "Статус",
  approval: "Дозвіл",
  idle: "Вільний",
  working: "Працює",
  waiting_human: "Чекає людини",
  blocked: "Заблокований",
  ready: "Готовий до наступного кроку",
};
const multiline = new Set([
  "notes",
  "description",
  "rules",
  "result",
  "body",
  "evidence",
  "detail",
]);
const links = {
  challenge_id: "challenges",
  task_id: "tasks",
  artifact_id: "artifacts",
  owner_id: "actors",
  requested_assignee_id: "actors",
};

function definition(schema, property) {
  if (property.$ref) return schema.$defs[property.$ref.split("/").at(-1)];
  if (property.anyOf)
    return definition(
      schema,
      property.anyOf.find((item) => item.type !== "null"),
    );
  return property;
}
function options(state, key, current) {
  let rows = state.data[links[key]] || [];
  if (key === "owner_id")
    rows = rows.filter((item) => item.kind === "human" && item.active);
  if (key === "requested_assignee_id")
    rows = rows.filter((item) => item.active && item.role !== "observer");
  const items = [
    ["", "Без прив’язки"],
    ...rows.map((item) => [item.id, item.title || item.name]),
  ];
  if (current && !items.some(([id]) => id === current))
    items.push([current, "Поточне посилання"]);
  return items;
}
function display(value) {
  return value == null
    ? "Без значення"
    : typeof value === "string"
      ? value
      : JSON.stringify(value, null, 2);
}

export async function editRecord(state, collection, resourceId, saved) {
  const [resource, contract] = await Promise.all([
    state.api.get(`resources/${collection}/${resourceId}`),
    state.api.get(`edit-contracts?collection=${collection}`),
  ]);
  const { item, editing } = resource;
  if (!editing.allowed)
    throw new Error("Вашому доступу недоступне редагування цього запису.");
  const conversions = new Map();
  const initialValues = new Map();
  const fields = editing.fields.map((key) => {
    const property = definition(
      contract.changes,
      contract.changes.properties[key],
    );
    const value = item[key];
    let control;
    if (collection === "projects" && key === "capabilities") {
      control = field(key, "У цьому CTF є флаги", {
        type: "checkbox",
        checked: Boolean(value?.flags),
      });
      conversions.set(key, (raw) => ({ ...value, flags: Boolean(raw) }));
    } else if (key === "capabilities") {
      control = field(key, labels[key], {
        value: (value || []).join(", "),
        hint: "Через кому",
      });
      conversions.set(key, (raw) =>
        raw
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      );
    } else if (links[key]) {
      control = field(key, labels[key], {
        options: options(state, key, value),
        value: value || "",
      });
      conversions.set(key, (raw) => raw || null);
    } else if (property.enum) {
      const choices = property.enum.map((entry) => [
        entry,
        enumLabels[entry] || entry,
      ]);
      if (value && !property.enum.includes(value))
        choices.unshift([value, enumLabels[value] || value]);
      control = field(key, labels[key], { options: choices, value });
      conversions.set(key, (raw) => raw);
    } else if (property.type === "boolean") {
      control = field(key, labels[key], {
        type: "checkbox",
        checked: Boolean(value),
      });
      conversions.set(key, (raw) => Boolean(raw));
    } else if (property.type === "integer") {
      control = field(key, labels[key], {
        type: "number",
        min: property.minimum,
        max: property.maximum,
        value: value ?? (key === "ttl_seconds" ? 120 : ""),
      });
      conversions.set(key, (raw) => (raw === "" ? null : Number(raw)));
    } else if (property.format === "date-time") {
      const local = value
        ? new Date(
            new Date(value).getTime() -
              new Date(value).getTimezoneOffset() * 60000,
          )
            .toISOString()
            .slice(0, 16)
        : "";
      control = field(key, labels[key], {
        type: "datetime-local",
        value: local,
      });
      conversions.set(key, (raw) => (raw ? new Date(raw).toISOString() : null));
    } else {
      const nullable = contract.changes.properties[key].anyOf?.some(
        (entry) => entry.type === "null",
      );
      control = field(key, labels[key] || key, {
        type: multiline.has(key) ? "textarea" : "text",
        value: value ?? "",
        maxlength: property.maxLength,
        required: Boolean(property.minLength) && !nullable,
      });
      conversions.set(key, (raw) => (raw === "" && nullable ? null : raw));
    }
    const input = control.querySelector("input,select,textarea");
    initialValues.set(
      key,
      input.type === "checkbox" ? input.checked : input.value,
    );
    return control;
  });
  if (editing.can_override && item.status === "running")
    fields.push(
      field(
        "force",
        "Звільнити активне призначення для зміни стану або адресата",
        {
          type: "checkbox",
          hint: "Потрібне пояснення. Старий агент втратить право завершити роботу на сервері. Новий адресат має сам прийняти її. Це не зупиняє процес на його пристрої.",
        },
      ),
    );
  fields.push(
    field("edit_reason", "Коментар до правки (необов’язково)", {
      type: "textarea",
      maxlength: 10000,
    }),
  );
  openForm(
    `Редагувати ${collections[collection]}`,
    fields,
    "Зберегти зміни",
    async (values) => {
      const changes = {};
      for (const [key, convert] of conversions) {
        const input = fields
          .find((control) => control.querySelector(`[name="${key}"]`))
          ?.querySelector(`[name="${key}"]`);
        const raw =
          input.type === "checkbox"
            ? Boolean(values[key])
            : (values[key] ?? "");
        if (key !== "ttl_seconds" && raw === initialValues.get(key)) continue;
        const value = convert(values[key] ?? "");
        const same =
          key === "expires_at" && value && item[key]
            ? new Date(value).getTime() === new Date(item[key]).getTime()
            : JSON.stringify(value) === JSON.stringify(item[key]);
        if (!same) changes[key] = value;
      }
      if (!Object.keys(changes).length) {
        notify("Змін немає");
        return;
      }
      await state.api.command(
        "resource.update",
        {
          collection,
          expected_version: item.version,
          changes,
          reason: values.edit_reason,
          force: Boolean(values.force),
        },
        item.id,
      );
      await saved();
      notify("Зміни збережено. Попередня версія доступна в історії.");
    },
    {
      hint:
        collection === "flags"
          ? "Зміна значення, цілі або раунду скине підтвердження. Історія перевірок збереже попередній флаг."
          : collection === "messages"
            ? "Автор і адресат зберігаються. Відредагований текст потребуватиме нового підтвердження адресата."
            : "Зберігаємо автора та попередні версії. Якщо запис паралельно змінився, сервер повідомить про конфлікт.",
    },
  );
}

export async function showHistory(state, collection, resourceId, offset = 0) {
  const data = await state.api.get(
    `resources/${collection}/${resourceId}/history?offset=${offset}&limit=5`,
  );
  const name = (id) =>
    state.data.actors.find((item) => item.id === id)?.name || "Учасник";
  const renderRevision = (revision) =>
    el(
      "article",
      { class: "revision-card" },
      el(
        "h3",
        {},
        `${name(revision.editor_id)} · версія ${revision.after.version}`,
      ),
      el("small", { class: "muted" }, date(revision.created_at)),
      revision.reason && el("p", { class: "pre-wrap" }, revision.reason),
      (
        revision.changed_fields ||
        Object.keys(revision.after).filter(
          (key) =>
            JSON.stringify(revision.before[key]) !==
            JSON.stringify(revision.after[key]),
        )
      ).map((key) =>
        el(
          "section",
          {},
          el("h4", {}, labels[key] || key),
          el(
            "div",
            { class: "revision-diff" },
            el(
              "div",
              {},
              el("small", { class: "muted" }, "Було"),
              el("pre", {}, display(revision.before[key])),
            ),
            el(
              "div",
              {},
              el("small", { class: "muted" }, "Стало"),
              el("pre", {}, display(revision.after[key])),
            ),
          ),
        ),
      ),
      data.excerpts &&
        !revision._full &&
        button(
          "Повна правка",
          async () => {
            try {
              const full = await state.api.get(
                `resources/${collection}/${resourceId}/history?revision_id=${revision.id}`,
              );
              modal(
                "Повна правка",
                el(
                  "div",
                  { class: "detail-content" },
                  renderRevision({
                    ...full,
                    changed_fields: revision.changed_fields,
                    _full: true,
                  }),
                ),
                { wide: true },
              );
            } catch (error) {
              notify(error.message, true);
            }
          },
          { tone: "ghost small" },
        ),
    );
  modal(
    "Історія правок",
    el(
      "div",
      { class: "detail-content" },
      !data.items.length && el("p", {}, "Запис ще не редагували."),
      data.items.map(renderRevision),
      el(
        "div",
        { class: "inline-actions" },
        offset > 0 &&
          button("Попередні", () =>
            showHistory(state, collection, resourceId, Math.max(0, offset - 5)),
          ),
        data.has_more &&
          button("Наступні", () =>
            showHistory(
              state,
              collection,
              resourceId,
              offset + data.items.length,
            ),
          ),
      ),
    ),
    { wide: true },
  );
}
