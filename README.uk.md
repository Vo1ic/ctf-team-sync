# CTF Team Sync

[English](README.md) · [Apache License 2.0](LICENSE)

Спільне серверне ядро для CTF-команди з різними CLI, IDE, моделями й ОС.
Це окремий репозиторій, без залежності від локального CTF-workspace.

Фундамент: [архітектура](docs/ARCHITECTURE.md), [протокол](docs/PROTOCOL.md),
[машинні контракти](contracts/), [робота у вебінтерфейсі](docs/WEB.md).

Доставка в контекст: [адаптери агентів](docs/ADAPTERS.md).

## Що вже працює

Ініціалізація CTF, проєктні identities та ролі, окремі challenges і tasks,
атомарні task leases, версії та ідемпотентні команди, знахідки з доказами,
флаги з окремими перевірками й звітами відправлення, inbox із receipts,
події з replay/long polling/SSE та незмінні артефакти з SHA-256.

До HTTP API підключається stdio MCP bridge. Всі його дані зберігаються на сервері.
MCP-агент читає inbox або очікує події між кроками. Автоматичне передавання в
активну сесію підтримує окремий Codex hook adapter у точках lifecycle; пробудження
неактивної сесії він не виконує. Вебінтерфейс доступний
за `/`: створення CTF, роботи, знахідки, флаги, повідомлення, файли й команда.
Він використовує той самий API; зміни від агентів з’являються автоматично.

## Запуск через Docker Compose

```sh
cp .env.example .env
# Встановити TEAM_SYNC_BOOTSTRAP_TOKEN та TEAM_SYNC_DB_PASSWORD у .env.
# Для DB password використати випадковий hex-рядок (без URL-special characters).
docker compose up --build -d
```

Вебінтерфейс: `http://localhost:8000`, API explorer: `http://localhost:8000/docs`.
API за замовчуванням прив'язаний до loopback; для команди потрібен доступний
сервер із TLS reverse proxy. Міграція БД виконується під час старту контейнера.

Приклад ініціалізації (відповідь міститиме owner token, зберегти його):

```sh
curl http://localhost:8000/api/v1/projects \
  -H "Authorization: Bearer $TEAM_SYNC_BOOTSTRAP_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Team CTF","owner_name":"Captain","profile":"jeopardy","capabilities":{"flags":true}}'
```

Змінна bootstrap token у shell має відповідати `.env`; Compose читає цей файл,
але автоматично не експортує його у поточний shell.

## MCP для учасника

Owner видає окремий actor token для кожного одночасного агента через
`POST /api/v1/projects/{project}/actors`. На пристрої учасника потрібні Python
та uv; PostgreSQL і Docker там не потрібні.

Для клієнта, що підтримує stdio MCP, конфігурація має такий зміст:

```json
{
  "mcpServers": {
    "team-sync": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/team-sync", "team-sync-mcp"],
      "env": {
        "TEAM_SYNC_URL": "https://team-sync.example.org",
        "TEAM_SYNC_PROJECT_ID": "<project-uuid>",
        "TEAM_SYNC_TOKEN": "<actor-token>"
      }
    }
  }
}
```

Обгортка конфігурації залежить від host. Локальний checkout містить лише код
bridge; всі учасники вказують URL одного сервера. На Windows вказати свій шлях.

Інструменти: `bootstrap`, `list_items`, `command_contracts`, `command`, `inbox`,
`wait_events`, `publish_artifact`, `fetch_artifact`.

Власник може створювати identities у розділі **Команда** та одразу завантажувати
MCP-конфігурацію з виданим токеном. Шлях до checkout учасник замінює на власний.

## Розробка й перевірка

```sh
uv sync --frozen
export TEAM_SYNC_DATABASE_URL='postgresql+psycopg://user:password@localhost/team_sync'
export TEAM_SYNC_BOOTSTRAP_TOKEN='<long-random-bootstrap-token>'
uv run alembic upgrade head
uv run uvicorn team_sync.api:create_app --factory --reload
```

Тести використовують реальний PostgreSQL. Вони створюють і видаляють власну
випадкову schema, не чіпаючи інші схеми; потрібен користувач із правом CREATE SCHEMA.

```sh
export TEAM_SYNC_TEST_DATABASE_URL='postgresql+psycopg://user:password@localhost/team_sync_test'
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run team-sync-contracts
```

Вебчастина — HTML/CSS/JS усередині Python-пакета: Node не потрібен для запуску
сервера або складання Docker image. Для frontend-перевірок потрібні Node і npm:

```sh
npm ci
npm run test:web
npx playwright install chromium
npm run test:e2e
npm run format:web
```

Browser-тести використовують той самий `TEAM_SYNC_TEST_DATABASE_URL`, запускають
ізольований сервер і очищають власну schema після завершення. Порт за замовчуванням
8941; його можна змінити через `TEAM_SYNC_WEB_TEST_PORT`.

Для редагування вебу без перескладання після кожної зміни:

```sh
docker compose -f compose.yaml -f compose.dev.yaml up --build -d
```

Python автоматично перезапускається, HTML/CSS/JS оновлюються після refresh браузера.
База та артефакти залишаються у тих самих volumes. Після зміни залежностей потрібна
збірка; повернення до звичайного режиму: `docker compose up --build -d`.

Без test URL інтеграційні тести пропускаються. Тести MCP запускають два окремі
процеси й HTTP-сервер, перевіряючи спільний стан, повідомлення та передачу файла.

## Подальший розвиток

Перевірити підключення на двох пристроях і в реальних клієнтах команди.
Далі: приймання Codex hooks у реальній сесії, інші host adapters, залежності робіт та інтеграція
з CTF-платформою. Вони використовують наявне ядро.
