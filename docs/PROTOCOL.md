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
| challenge.create | не потрібен | title, description?, category? |
| task.create | не потрібен | title, description?, challenge_id? |
| task.claim | task | expected_version, lease_seconds? |
| task.renew | task | expected_version, lease_seconds? |
| task.complete | task | expected_version, result |
| finding.create | не потрібен | title, body, evidence, kind?, challenge_id?, task_id? |
| flag.create | не потрібен | value, challenge_id?, target?, round?, expires_at? |
| flag.verify | flag | expected_version, status, method, evidence |
| submission.create | flag | outcome, evidence |
| message.send | не потрібен | body, recipient_id? (null = broadcast) |
| message.ack | message | stage |

Повні аргументи та enums: `contracts/commands.schema.json` або MCP
`command_contracts`. `flag.verify` фіксує повідомлене підтвердження від owner чи
coordinator; сервер сам не виконує локальний checker і не звертається до платформи.
`submission.create` лише додає звіт. Відхилення відправлення не змінює перевірку
флага, а прийнятий звіт не підвищує її автоматично.

## Читання

- `GET /projects/{project}/collections/{collection}?offset=0&limit=100` — items;
  колекції: actors, challenges, tasks, findings, flags, verifications, submissions, artifacts.
- `GET /projects/{project}/inbox?offset=0&limit=100` — доступні повідомлення й
  власний ack_stage. Читання не змінює стан.
- `GET /projects/{project}/messages/{message}` — одне повідомлення, доступне цьому
  отримувачу (або broadcast), із власним ack_stage. Чужий адресат отримує 404.
- Максимальний розмір сторінки — 500. Читати наступні сторінки потрібно до
  неповної сторінки. Пагінація не є довготривалим snapshot; зміни слід узгоджувати
  через event replay за стабільними UUID.

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

`GET /projects/{project}/artifacts/{artifact}/content` — байти та header
`X-Content-SHA256`. Перед використанням клієнт перевіряє хеш.

## Помилки

401 — неправильний/відкликаний credential; 403 — недостатня роль; 404 — ресурс
відсутній або належить іншому проєкту; 409 — конфлікт версії, lease, дубль флага
чи повторне використання ключа з іншими аргументами; 413 — завеликий файл;
422 — неправильна структура чи аргументи.

Доменна помилка має `code` та `detail`. Помилки валідації HTTP framework можуть
мати стандартний `detail` без `code`; клієнт повинен враховувати обидві форми.
