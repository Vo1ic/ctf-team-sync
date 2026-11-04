export class ApiError extends Error {
  constructor(
    message,
    { status = 0, code = "network_error", uncertain = false } = {},
  ) {
    super(message);
    this.status = status;
    this.code = code;
    this.uncertain = uncertain;
  }
}

export function commandId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonical(value[key])]),
    );
  }
  return value;
}

function errorMessage(body, status) {
  const labels = {
    invalid_credential:
      "Доступ не підтверджено. Перевірте токен або зверніться до власника CTF.",
    forbidden: "Вашій ролі недоступна ця дія.",
    not_found:
      "Не знайдено ресурс у цьому CTF. Перевірте ідентифікатор і доступ.",
    version_conflict:
      "Інший учасник уже змінив цей запис. Оновіть дані й повторіть дію.",
    task_unavailable: "Цю роботу вже взяли або завершили.",
    lease_invalid: "Ваше призначення вже не діє. Оновіть стан роботи.",
    duplicate_flag: "Такий флаг уже є для цього завдання, цілі та раунду.",
    capability_disabled: "Флаги вимкнено для цього CTF.",
    idempotency_conflict: "Цей ключ запиту вже використано з іншим вмістом.",
    last_owner: "Не можна відкликати доступ останнього власника.",
    artifact_too_large:
      "Файл перевищує ліміт сервера (за замовчуванням 64 MiB).",
    receipt_regression: "Повідомлення вже позначене як опрацьоване.",
    scope_mismatch:
      "Завдання знахідки не збігається із завданням вибраної роботи.",
    invalid_cursor: "Курсор не відповідає журналу CTF. Підключіться повторно.",
    browser_origin_required:
      "Відкрийте Team Sync з адреси цього сервера й повторіть дію.",
    task_in_progress:
      "Робота має активного виконавця. Координатор може явно звільнити призначення з поясненням.",
    override_forbidden:
      "Змінювати чуже активне призначення може лише координатор або власник.",
    override_reason_required:
      "Додайте пояснення до зміни активного призначення.",
    review_workflow_required:
      "Для аналізу артефакта використовуйте дозвіл, початок аналізу та звіт.",
    log_rate_limit: "Забагато записів журналу. Повторіть через хвилину.",
    task_reserved: "Роботу адресовано іншому учаснику.",
    analysis_not_approved:
      "Агент має отримати дозвіл людини й розпочати аналіз.",
    artifact_archived: "Артефакт архівовано. Людина може його відновити.",
    review_in_progress: "Цей артефакт уже аналізує інший агент.",
    message_rate_limit: "Забагато повідомлень. Повторіть через хвилину.",
    urgent_rate_limit: "Ліміт термінових повідомлень вичерпано на хвилину.",
  };
  if (labels[body?.code]) return labels[body.code];
  if (Array.isArray(body?.detail)) {
    return body.detail
      .map((item) => `${(item.loc || []).join(" · ")}: ${item.msg}`)
      .join("\n");
  }
  return typeof body?.detail === "string"
    ? body.detail
    : `Сервер повернув помилку ${status}.`;
}

export class ApiClient {
  constructor(
    projectId,
    token,
    { fetcher = globalThis.fetch.bind(globalThis) } = {},
  ) {
    this.projectId = projectId;
    this.token = token;
    this.actorId = null;
    this.fetcher = fetcher;
    this.controller = new AbortController();
    this.pending = new Map();
  }

  close() {
    this.controller.abort();
    this.token = "";
    this.pending.clear();
  }

  async request(
    path,
    { method = "GET", body, headers = {}, raw = false, timeout = 35000 } = {},
  ) {
    const abort = new AbortController();
    const cancel = () => abort.abort();
    this.controller.signal.addEventListener("abort", cancel, { once: true });
    if (this.controller.signal.aborted) abort.abort();
    const timer = setTimeout(cancel, timeout);
    let response;
    try {
      response = await this.fetcher(path, {
        method,
        cache: "no-store",
        credentials: "same-origin",
        signal: abort.signal,
        headers: {
          ...(this.token
            ? { Authorization: `Bearer ${this.token}` }
            : { "X-Team-Sync-Browser": "1" }),
          ...(this.actorId ? { "X-Team-Sync-Actor": this.actorId } : {}),
          ...(body !== undefined && !raw
            ? { "Content-Type": "application/json" }
            : {}),
          ...headers,
        },
        body:
          body === undefined ? undefined : raw ? body : JSON.stringify(body),
      });
      if (!response.ok) {
        let details;
        try {
          details = await response.json();
        } catch {
          details = {};
        }
        throw new ApiError(errorMessage(details, response.status), {
          status: response.status,
          code: details.code,
          uncertain:
            method !== "GET" &&
            (response.status >= 500 || response.status === 408),
        });
      }
      return raw && method === "GET"
        ? await response.arrayBuffer()
        : await response.json();
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (this.controller.signal.aborted)
        throw new DOMException("Disconnected", "AbortError");
      throw new ApiError(
        "Не вдалося отримати відповідь сервера. Перевірте з’єднання.",
        {
          uncertain: method !== "GET",
        },
      );
    } finally {
      clearTimeout(timer);
      this.controller.signal.removeEventListener("abort", cancel);
    }
  }

  path(suffix) {
    return `/api/v1/projects/${encodeURIComponent(this.projectId)}/${suffix}`;
  }

  get(suffix) {
    return this.request(this.path(suffix));
  }

  async all(suffix) {
    const items = [];
    for (let offset = 0; ; offset += 500) {
      const page = await this.get(`${suffix}?offset=${offset}&limit=500`);
      items.push(...page.items);
      if (page.items.length < 500) return items;
    }
  }

  async command(operation, arguments_, resourceId = null) {
    const signature = JSON.stringify(
      canonical([operation, resourceId, arguments_]),
    );
    if (!this.pending.has(signature)) this.pending.set(signature, commandId());
    const action = {
      operation,
      resource_id: resourceId,
      arguments: arguments_,
      idempotency_key: this.pending.get(signature),
    };
    try {
      const response = await this.request(this.path("commands"), {
        method: "POST",
        body: action,
      });
      this.pending.delete(signature);
      return response;
    } catch (error) {
      if (!error.uncertain) this.pending.delete(signature);
      throw error;
    }
  }

  async upload(
    file,
    key,
    { requiresReview = false, supersedesId, expectedVersion } = {},
  ) {
    const revision = supersedesId
      ? `&supersedes_id=${encodeURIComponent(supersedesId)}&expected_version=${expectedVersion}`
      : "";
    return this.request(
      this.path(
        `artifacts?name=${encodeURIComponent(file.name)}&requires_review=${requiresReview}&media_type=${encodeURIComponent(file.type || "application/octet-stream")}${revision}`,
      ),
      {
        method: "POST",
        raw: true,
        body: file,
        headers: {
          "Content-Type": "application/octet-stream",
          "Idempotency-Key": key,
        },
        timeout: 120000,
      },
    );
  }

  async download(artifact) {
    if (!crypto.subtle)
      throw new ApiError(
        "Для перевірки файла відкрийте Team Sync через HTTPS або localhost.",
      );
    const bytes = await this.request(
      this.path(`artifacts/${artifact.id}/content`),
      { raw: true },
    );
    const hash = await crypto.subtle.digest("SHA-256", bytes);
    const actual = [...new Uint8Array(hash)]
      .map((value) => value.toString(16).padStart(2, "0"))
      .join("");
    if (actual !== artifact.sha256)
      throw new ApiError(
        "Контрольна сума файла не збігається. Завантаження скасовано.",
      );
    return new Blob([bytes], { type: "application/octet-stream" });
  }
}
