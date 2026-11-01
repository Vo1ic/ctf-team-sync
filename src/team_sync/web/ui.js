// Untrusted project content is always inserted through textContent, never HTML.
export function el(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== false && value != null)
      node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child != null && child !== false)
      node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

const paths = {
  grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  target: "M12 3a9 9 0 1 0 9 9 M12 7a5 5 0 1 0 5 5 M12 12l9-9 M16 3h5v5",
  tasks: "M8 5h13 M8 12h13 M8 19h13 M3 5h.01 M3 12h.01 M3 19h.01",
  finding: "M9 18h6 M10 22h4 M8.5 14.5a6 6 0 1 1 7 0L15 17H9z",
  flag: "M5 22V3 M5 3c5-4 9 4 15 0v11c-6 4-10-4-15 0",
  message:
    "M21 11.5a8.5 8.5 0 0 1-8.5 8.5H5l-3 2v-9.5A8.5 8.5 0 0 1 10.5 4H13a8 8 0 0 1 8 7.5z M7 10h10 M7 14h6",
  file: "M14 2H5v20h14V7z M14 2v6h5 M8 13h8 M8 17h5",
  team: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M22 21v-2a4 4 0 0 0-3-3.87 M16 3.13a4 4 0 0 1 0 7.75",
  plus: "M12 5v14 M5 12h14",
  arrow: "M5 12h14 M13 6l6 6-6 6",
  refresh:
    "M20 7v5h-5 M4 17v-5h5 M6 7a7 7 0 0 1 12-2l2 2 M18 17a7 7 0 0 1-12 2l-2-2",
  search: "M10 17a7 7 0 1 0 0-14 7 7 0 0 0 0 14 M15 15l6 6",
  logout: "M9 3H3v18h6 M9 12h12 M16 7l5 5-5 5",
  close: "M6 6l12 12 M6 18 18 6",
  copy: "M9 9h12v12H9z M15 5V3H3v12h2",
  check: "m5 12 4 4L19 6",
  download: "M12 3v12 M7 10l5 5 5-5 M4 17v4h16v-4",
  upload: "M12 16V4 M7 9l5-5 5 5 M4 17v4h16v-4",
  clock: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20 M12 6v6l4 2",
  menu: "M4 6h16 M4 12h16 M4 18h16",
  shield: "m12 2 9 4v6c0 6-9 10-9 10S3 18 3 12V6z M8 12l3 3 5-6",
};

export function icon(name) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [key, value] of Object.entries({
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    "stroke-width": "1.6",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
    "aria-hidden": "true",
  }))
    node.setAttribute(key, value);
  const path = document.createElementNS(node.namespaceURI, "path");
  path.setAttribute("d", paths[name] || paths.grid);
  node.append(path);
  return node;
}

export function button(
  label,
  action,
  { tone = "secondary", symbol, ...attributes } = {},
) {
  return el(
    "button",
    { type: "button", class: `button ${tone}`, onclick: action, ...attributes },
    symbol && icon(symbol),
    label,
  );
}

export function badge(label, tone = "neutral") {
  return el("span", { class: `badge ${tone}` }, label);
}

export function empty(title, description, action) {
  return el(
    "div",
    { class: "empty" },
    el("div", { class: "empty-icon" }, icon("target")),
    el("h3", {}, title),
    el("p", {}, description),
    action,
  );
}

export function field(
  name,
  label,
  { type = "text", options, value, hint, ...attributes } = {},
) {
  const id = `field-${name}`;
  let input;
  if (options) {
    input = el(
      "select",
      { id, name, ...attributes },
      options.map(([key, text]) => el("option", { value: key }, text)),
    );
  } else if (type === "textarea") {
    input = el("textarea", { id, name, rows: 4, ...attributes });
  } else {
    input = el("input", { id, name, type, ...attributes });
  }
  if (value != null) input.value = value;
  return el(
    "div",
    { class: `field ${type === "checkbox" ? "checkbox-field" : ""}` },
    el("label", { for: id }, label),
    input,
    hint && el("small", { id: `${id}-hint` }, hint),
  );
}

export function notify(message, error = false) {
  const node = el(
    "div",
    { class: `notice ${error ? "error" : ""}` },
    icon(error ? "message" : "check"),
    el("span", {}, message),
  );
  document.querySelector("#notifications").append(node);
  setTimeout(() => node.remove(), error ? 9000 : 4500);
}

export function modal(title, content, { wide = false, onClose } = {}) {
  const dialog = document.querySelector("#modal");
  dialog.className = wide ? "wide" : "";
  dialog.replaceChildren(
    el(
      "header",
      { class: "modal-header" },
      el("h2", { id: "modal-title" }, title),
      button("", () => dialog.close(), {
        tone: "icon-button",
        symbol: "close",
        "aria-label": "Закрити",
      }),
    ),
    content,
  );
  dialog.onclose = () => {
    if (dialog.open) return;
    dialog.replaceChildren();
    onClose?.();
  };
  if (!dialog.open) dialog.showModal();
  return dialog;
}

export function openForm(
  title,
  fields,
  submitLabel,
  submit,
  { hint, nonReplayable = false } = {},
) {
  const errorBox = el("div", {
    class: "form-error",
    role: "alert",
    hidden: true,
  });
  const submitButton = el(
    "button",
    { class: "button primary", type: "submit" },
    submitLabel,
  );
  const form = el(
    "form",
    { class: "modal-form" },
    hint && el("p", { class: "muted" }, hint),
    fields,
    errorBox,
    el(
      "footer",
      { class: "modal-footer" },
      button("Скасувати", () => dialog.close(), { tone: "ghost" }),
      submitButton,
    ),
  );
  const dialog = modal(title, form);
  let retryData = null;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submitButton.disabled = true;
    errorBox.hidden = true;
    const values = retryData || Object.fromEntries(new FormData(form));
    try {
      await submit(values);
      if (dialog.open && form.isConnected) dialog.close();
    } catch (error) {
      if (error.name === "AbortError") return;
      errorBox.textContent = error.message;
      errorBox.hidden = false;
      if (error.uncertain) {
        errorBox.append(
          document.createTextNode(
            nonReplayable
              ? " Запит міг виконатися. Перевірте створені записи перед новою видачею доступу."
              : " Результат запиту невідомий. Повтор використає той самий ключ і не створить дубль.",
          ),
        );
        retryData = values;
        for (const input of form.querySelectorAll("input, select, textarea"))
          input.disabled = true;
        submitButton.textContent = "Повторити запит";
        if (nonReplayable) {
          submitButton.textContent = "Перевірте стан проєкту";
          return;
        }
      }
    } finally {
      if (!(nonReplayable && retryData)) submitButton.disabled = false;
    }
  });
  return dialog;
}

export async function copy(value) {
  try {
    await navigator.clipboard.writeText(value);
    notify("Скопійовано");
  } catch {
    notify("Не вдалося скопіювати. Виділіть і скопіюйте текст вручну.", true);
  }
}

export function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = el("a", { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function date(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("uk-UA", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function size(bytes) {
  return bytes < 1024
    ? `${bytes} B`
    : bytes < 1048576
      ? `${(bytes / 1024).toFixed(1)} KiB`
      : `${(bytes / 1048576).toFixed(1)} MiB`;
}
