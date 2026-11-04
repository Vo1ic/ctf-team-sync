# CTF Team Sync

[English](README.md) · [Apache License 2.0](LICENSE)

Спільне серверне ядро для CTF-команди з різними CLI, IDE, моделями й ОС.
Це окремий репозиторій, без залежності від локального CTF-workspace.

Фундамент: [архітектура](docs/ARCHITECTURE.md), [протокол](docs/PROTOCOL.md),
[машинні контракти](contracts/), [робота у вебінтерфейсі](docs/WEB.md).

Доставка в контекст: [адаптери агентів](docs/ADAPTERS.md).
Для довільних моделей і середовищ: [спільний протокол HTTP/MCP/JSONL](docs/AGENT-PROTOCOL.md).
Адаптери Codex і Claude Code — окремі форматери; клієнт може працювати лише через HTTP.

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

Роботи, завдання CTF, учасників та інші робочі записи можна редагувати через
веб, MCP або HTTP зі збереженням попередніх версій. У **Журналі** видно дії
команди й явні логи учасників із фільтрами, пагінацією та експортом JSON.
Координатор може перепризначити активну роботу з поясненням; новий адресат
має сам її прийняти. Автоматичного збору терміналів або думок моделей немає.

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
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Vo1ic/ctf-team-sync@v0.5.0", "team-sync-mcp"],
      "env": {
        "TEAM_SYNC_URL": "https://team-sync.example.org",
        "TEAM_SYNC_PROJECT_ID": "<project-uuid>",
        "TEAM_SYNC_TOKEN": "<actor-token>"
      }
    }
  }
}
```

Обгортка залежить від host; uvx має бути доступний клієнту через PATH. Checkout
не потрібен для автоматичного запуску. Всі учасники вказують URL одного сервера.
Приватний файл доступу можна передати через TEAM_SYNC_ACCESS_FILE.

Починайте з `get_context`: власні роботи, leases, повідомлення, знання команди.
Далі — `search`, `get_resource`, `update_task`, `report_finding`, `send_message`,
`acknowledge_message`, `outbox`. Старі інструменти збережено.
[Повний робочий цикл агента](docs/AGENT-WORKFLOW.md).

Власні агенти без MCP/hooks можуть запускати `uv run team-sync-agent bridge`.
JSONL-профіль узгоджує версію, приймає довільні назви runtime/provider/model,
підтримує обмеження розміру контексту та явне підтвердження доставки. Для Claude:
`uv run team-sync-agent hook-config --host claude-code`.

Мережевий сценарій приймання для двох різних контейнерних клієнтів:

```sh
uv run python scripts/acceptance.py --env-file .env
```

Він створює окремий тестовий CTF. Python/MCP (Debian) і Node/HTTP (Alpine)
обмінюються роботою, файлами, знахідками й повідомленнями. Доступ до створеного
CTF зберігається у приватній, gitignored директорії `.team-sync/acceptance/`.

Окремо пройдено [живе приймання Claude Code та двох Codex-субагентів](docs/LIVE-ACCEPTANCE.md):
завдання, серверний файл, hooks активної сесії, непідтверджений флаг,
незалежне підтвердження та приватна відповідь назад.

Для огляду UI можна [створити наповнений демонстраційний CTF](docs/DEMO.md)
через `uv run python scripts/demo.py seed --env-file .env`. Є також скінченне
відтворення змін у процесі роботи та відкриття вже підключеного браузера.

У **Підключеннях** є окремі сценарії для людини та агента. Людині достатньо
посилання або файла, без акаунта, email і пароля; браузер пам’ятає CTF 7 днів.
Агент отримує готову MCP-конфігурацію, файл доступу та стартову інструкцію.

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
