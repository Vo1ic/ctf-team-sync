# Протокол v1

HTTP prefix: `/api/v1`. Усі проєктні запити передають
`Authorization: Bearer <actor-token>`. Токен прив'язаний до одного проєкту.
Усі часові мітки мають timezone; сервер записує UTC. Ідентифікатори — UUID.

## Початок роботи

1. `POST /projects` із bootstrap credential: `name`, `owner_name`, необов'язкові
   `profile`, `capabilities`, `rules`. Відповідь містить project, actor, token.
2. `POST /projects/{project}/actors` із owner credential: `name`, `kind`, `role`,
   необов'язкові `owner_id` і `capabilities`. Відповідь містить actor та його token.
3. Новий агент читає `GET /projects/{project}/snapshot`, колекції й inbox.
4. `POST /projects/{project}/actors/{actor}/revoke` відкликає доступ.

Видача credentials не підтримує replay секрету. Якщо відповідь втрачена, owner
знаходить identity через actors, відкликає її та створює нову. Під час init
втрачений owner token потребує втручання адміністратора сервера.

## Команди

`POST /projects/{project}/commands`:

```json
{
  "operation": "task.claim",
  "resource_id": "00000000-0000-4000-8000-000000000001",
  "arguments": {"expected_version": 1, "lease_seconds": 300},
  "idempotency_key": "a-unique-command-uuid"
}
```

Ідемпотентність scoped до `(project, actor, key)`. Повтор після невідомого результату
використовує той самий key і вміст; нова операція — новий key. Replay повертає
початковий результат, навіть якщо стан сутності вже змінився. Для актуального
стану потрібно перечитати колекцію.

| operation | resource_id | arguments |
| --- | --- | --- |
| resource.update | resource | collection, expected_version, changes, reason? |
| challenge.create | не потрібен | title, description?, category? |
| task.create | не потрібен | title, description?, challenge_id?, requested_assignee_id? |
| task.claim | task | expected_version, lease_seconds? |
| task.renew | task | expected_version, lease_seconds? |
| task.complete | task | expected_version, result, without_flag? — challenge task → `review` до підтвердження флага |
| task.release | task | expected_version, reason |
| task.assign | task | expected_version, recipient_id? (null = будь-хто) |
| task.join | task | actor_id?, note? — учасник без lease (0.6) |
| task.leave | task | actor_id? |
| task.join_many | не потрібен | category? або challenge_id?, actor_id?, note? |
| link.create | не потрібен | source_type, source_id, target_type, target_id, relation, note? (0.7) |
| link.delete | link | — (автор або координатор) |
| hint.request | не потрібен | task_id? або challenge_id?, reason, hint_ref?, cost? (потрібна власна знахідка) |
| hint.decide | hint_request | expected_version, decision approved/declined, note? — лише людина-координатор |
| hint.used | hint_request | expected_version, content — текст підказки стає знахідкою |
| actor.rename | actor | expected_version, name |
| activity.report | не потрібен | state, detail?, task_id?, ttl_seconds? |
| artifact.review.request | artifact | recipient_id? (для агента default self) |
| artifact.review.decide | task | expected_version, decision, note?; лише людина |
| artifact.review.start | task | expected_version |
| artifact.review.complete | task | expected_version, readme, verdict?, reason? |
| artifact.archive | artifact | expected_version, archived, reason; пряме рішення людини |
| finding.create | не потрібен | title, body, evidence, kind?, challenge_id?, task_id?, artifact_id?, status?, based_on[]?, refutes[]? |
| flag.create | не потрібен | value, task_id?, challenge_id? (з task), target?, round?, expires_at? |
| flag.verify | flag | expected_version, status, method? (manual), evidence? |
| submission.create | flag | outcome, evidence |
| message.send | не потрібен | body, recipient_id?, kind?, priority?, task_id?, group_key?, expires_at?, reply_to_id?, deduplicate? |
| message.ack | message | stage, expected_version? (обов'язково для редакцій >1) |

Повні аргументи та enums: `contracts/commands.schema.json` або MCP
`command_contracts(operation)` для однієї операції. `task.release` атомарно
зберігає handoff finding з причиною, звільняє власний чинний lease та збільшує
версію. `flag.verify` фіксує повідомлене підтвердження від owner чи
coordinator; сервер сам не виконує локальний checker і не звертається до платформи.
`submission.create` лише додає звіт. Відхилення відправлення не змінює перевірку
флага, а прийнятий звіт не підвищує її автоматично.

## Читання

- `GET /projects/{project}/collections/{collection}?offset=0&limit=100` — items;
  колекції: actors, challenges, tasks, findings, flags, verifications, submissions, artifacts.
- `GET /projects/{project}/inbox?offset=0&limit=100` — доступні повідомлення й
  власний ack_stage. Читання не змінює стан.
- `GET /projects/{project}/attention?offset=0&limit=20&max_chars=12000` — pending проєкція: priority,
  чергування авторів, згортання typed status, приховані expired/processed. `items`,
  `total`, `has_more`; limit ≤50, max_chars 4000–32000. Тіла скорочені до 2000
  символів із body_truncated та посиланням на повний ресурс. Наступний offset
  збільшуємо на фактичну кількість items. Читання не є ack; повна історія — inbox.
- `GET /projects/{project}/outbox?offset=0&limit=100` — лише власні вихідні;
  `recipient_ack_stage` конкретного адресата, null для broadcast/відсутнього ack.
- `GET /projects/{project}/agent-context?limit=5&max_chars=12000` — обмежений
  робочий контекст зі scope `task_id`/`challenge_id`, totals і excerpts.
- `GET /projects/{project}/resources/{collection}/{id}?offset=0&limit=10` —
  повний ресурс, `editing` з дозволеними полями та пагіновані пов'язані записи.
  Приватні повідомлення — відправнику/адресату.
- `GET /projects/{project}/edit-contracts?collection=...` — схема поля changes
  для редагування однієї колекції.
- `GET /projects/{project}/resources/{collection}/{id}/history?offset=0&limit=5&max_chars=12000`
  — bounded уривки правок із before/after, editor, причиною й changed_fields.
  Додайте `revision_id` для однієї повної правки. Читання не підтверджує message.
- `GET /projects/{project}/search?query=...&collection=...&offset=0&limit=20` —
  literal case-insensitive пошук спільних даних і власного inbox. `%`/`_` literal.
- `GET /projects/{project}/messages/{message}` — одне повідомлення, доступне цьому
  отримувачу (або broadcast), із власним ack_stage. Чужий адресат отримує 404.
- Максимальний розмір сторінки — 500. Читати наступні сторінки потрібно до
  неповної сторінки. Пагінація не є довготривалим snapshot; зміни слід узгоджувати
  через event replay за стабільними UUID.

## Браузер без акаунтів

`POST /projects/{project}/browser-session` із Bearer перевіряє проєктний доступ
і встановлює 7-денні HttpOnly/SameSite=Strict cookies, scoped до project path і
identity; HTTPS також додає Secure. У відповіді тільки project_id та actor_id.
Браузер надалі може використовувати cookie без Bearer, вибравши identity через
`X-Team-Sync-Actor`. Cookie-мутація потребує `X-Team-Sync-Browser: 1` та Origin,
що точно збігається з origin сервера. `DELETE browser-session` прибирає cookies
поточного credential. Logout не відкликає actor; revoke знецінює його cookies.
Bearer-клієнти й початкова bootstrap-ініціалізація не змінені.

## Події

- `GET /projects/{project}/events?after=0&limit=100` — items, next_cursor, head.
- `GET /projects/{project}/events/wait?after=0&timeout=25` — той самий результат,
  з очікуванням подій. Порожній результат не змінює cursor.
- `GET /projects/{project}/events/stream?after=0` — SSE, `event: change`, `id: seq`.
  При reconnect `Last-Event-ID` має пріоритет над query `after`.

Клієнт має використовувати власний останній **оброблений** курсор. Для бібліотеки,
яка автоматично оновлює Last-Event-ID при отриманні, це може випереджати обробку:
після збою слід явно відновитися зі свого persisted cursor. Немає гарантії, що
будь-який SSE-клієнт сам правильно підтвердить бізнес-обробку.

Кожен конверт містить UUID події, project_id, seq, schema_version, type, actor_id,
payload, created_at. Подія вказує на ресурс, актуальний стан читається з API.
Для повідомлення потрібно перевірити inbox; тіло не розсилається в загальному
event stream. Отримання події не підтверджує обробку повідомлення.

## Артефакти

`POST /projects/{project}/artifacts?name=sample.bin` — сирі байти, header
`Idempotency-Key`, до 64 MiB за замовчуванням. Повертає id, sha256, size, name.
Ім'я є метаданими; сервер не використовує його як шлях збереження.
Додаткові query: `requires_review=false` і `media_type=application/octet-stream`.
Старі клієнти зберігають спільне завантаження без дозволів; веб завжди передає
`requires_review=true`. Preview дозволяє лише растрові зображення та plain text.
Для нової версії додайте `supersedes_id=<previous-artifact>` та його
`expected_version`. Старі байти залишаються за старим UUID, новий вміст отримує
новий UUID і власний дозвіл. Для метаданих використовуйте resource.update.

`GET /projects/{project}/artifacts/{artifact}/content` — байти та header
`X-Content-SHA256`. Перед використанням клієнт перевіряє хеш.
Для agent identity захищений content потребує власного чинного approved review
після explicit start. Архів дає агенту 410. Людина має доступ для перегляду й
відновлення. Деталі: [ADR 0003](adr/0003-human-reviews-and-agent-attention.md).

## Помилки

401 — неправильний/відкликаний credential; 403 — недостатня роль; 404 — ресурс
відсутній або належить іншому проєкту; 409 — конфлікт версії, lease, дубль флага
чи повторне використання ключа з іншими аргументами; 413 — завеликий файл;
422 — неправильна структура чи аргументи.
429 — бюджет повідомлень вичерпано, `Retry-After: 60`; replay старого ключа та
receipts доступні. 410 — артефакт архівовано для agent identity.

Доменна помилка має `code` та `detail`. Помилки валідації HTTP framework можуть
мати стандартний `detail` без `code`; клієнт повинен враховувати обидві форми.

## Керування та логи (0.5.0)

`actor.notes` — до 10000 символів; `challenge.points` — невід’ємне число або null,
`source_url` — http(s) або null. `task.priority` — low/normal/urgent (normal за
замовчуванням), `due_at` — timezone-aware timestamp або null. Ці поля доступні
при створенні та через resource.update; повний контракт — edit-contracts.

`resource.update(force=true, reason=...)` дозволяє координатору скинути чуже
чинне призначення під час зміни status/requested_assignee_id. Порожня причина
дає 422, відсутній force — 409, недозволена роль — 403. Нова номінація не є claim.
Для artifact_review дозвіл скидається, а загальний редактор не може завершити аналіз.

`log.create`: `{body, level?: "info", task_id?: UUID, challenge_id?: UUID}`.
Рівні debug/info/warning/error, текст 1–20000 символів. Спільний незмінний запис,
до 120 записів/60 секунд/actor. Observer не створює логи. Прочитати повний текст:
`GET resources/logs/{id}`; редагування logs не підтримується.

`GET journal?limit=20&max_chars=12000`: newest-first items, has_more, next_before,
head, server_time, filters. Фільтри: actor_id, resource_id (також прив’язані логи),
collection, event_type (точний), level, query (literal-пошук тексту логів, імен,
типів дій), include_routine=false. Наступна сторінка: before=next_before;
limit 1–100, max_chars 4000–32000. Нові події не зсувають сторінки історії.
Уривки логів мають body_truncated; event payload лишається metadata-only.

## Онбординг, CTF.md і кооп (0.6.0)

- `POST /projects/{project}/invites` (owner/coordinator): `label?`, `role`
  (worker/coordinator/observer), `expires_in_hours` 1–720, `max_uses` 1–100 →
  `{invite, token}`; `GET .../invites`, `POST .../invites/{id}/revoke`.
- `POST /join` з `Authorization: Bearer <invite>`: `human_name`, `agent_name?`,
  `capabilities?`, `create_agent=true` → `{project, human:{actor,token},
  agent:{actor,token}|null, briefing}`. 401 `invalid_invite`, 409 `invite_exhausted`.
- `POST /projects/{project}/actors`: owner — будь-які доступи; worker/coordinator —
  лише `kind=agent, role=worker` для своєї людини (агент — для свого власника).
  Відповідь містить `briefing`. Ім'я агента: `<Людина>-<Позивний>_<4>`.
- `GET /projects/{project}/me` — identity; `GET .../ctf.md` (text/markdown) і
  `GET .../briefing` — CTF.md; `GET .../participants` — учасники робіт.
- `GET /health` → `{"status":"ok","service":"ctf-team-sync","version":...}`.
- Помилки можуть мати машинні поля: `current_version`, `resource_id` (409
  `version_conflict`), `existing_id` (409 `duplicate_flag`/`duplicate_challenge`/
  `duplicate_task`). Створення challenge або незавершеної task з такою самою назвою
  дає 409, якщо не передати `allow_duplicate: true`.
- `search`: усі слова запиту мають знайтися (у будь-якому полі або в назві
  пов'язаного challenge/task); відповідь містить `searched_collections`.
- `agent-context`: секція з `total > 0` завжди має хоча б один (мінімальний)
  елемент; `omitted` і `more` підказують, як дочитати. Нова секція `joined_tasks`.
  Параметри підписаних URL (`token=` тощо) приховані в уривках, але не в
  `resources/{collection}/{id}`.

## Граф знань, гіпотези та досьє (0.7.0)

- `links` — типізовані ребра: `attached_to` (artifact → challenge/task/finding),
  `leads_to` (finding/task → finding/flag/task), `supports`, `supersedes`
  (finding → finding), `refutes` (finding → finding/flag). Повтор того самого ребра
  ідемпотентний; недопустима пара типів — 422 `invalid_link`.
- `finding.status` для гіпотез: `open` (за замовчуванням) → `confirmed` | `refuted`.
  `refutes` на гіпотезу робить її `refuted`, `supports` від `result` — `confirmed`;
  зміна стану записується як звичайна ревізія.
- `finding.create(based_on=[...], refutes=[...])` створює знахідку й ребра однією командою.
- `challenge.writeup` — редагований Markdown-райтап (resource.update).
- `GET /challenges/{id}/dossier` → `{challenge, markdown, graph:{nodes, edges}}`;
  `GET /challenges/{id}/dossier.md` — те саме як text/markdown: райтап, умова, файли,
  роботи, Mermaid-граф гіпотез, хід розв'язання з доказами й зв'язками, флаги.
- `GET /collections/links`; `resources/{collection}/{id}` має `related.links`.

## Підтверджене завершення, підказки, приманки (0.8.0)

- Стан task `review`: робота завершена, але завдання не розв'язане, доки людина не
  підтвердила флаг. `flag.verify(confirmed)` → `review` → `done` (подія `task.verified`);
  відхилення останнього кандидата → `open` з номінацією попереднього виконавця
  (`task.reopened`). `resource.update(status=done)` для такої task — лише координатор.
- `kind="decoy"` для знахідок-приманок; `agent-context?challenge_id=` має `known_decoys`.
- `GET /stalled?minutes=20` — роботи без прогресу або з заблокованим виконавцем.
- `GET /collections/hint_requests`.
