# Перевірка початкової реалізації

Дата: 2026-11-01.

- `uv run pytest -q` із реальним PostgreSQL 17: **50 passed**.
- `npm run test:web`: **9 passed**, транспорт, повтори й connection imports.
- `npm run test:e2e`: **16 passed**, Chromium із реальним API та PostgreSQL.
- `npm run format:web`: пройдено.
- `uv run ruff check .`: пройдено.
- `uv run ruff format --check .`: пройдено.
- `alembic upgrade head` і `alembic check`: схему створено, неврахованих змін немає.
- JSON-контракти згенеровано; тести перевіряють актуальність command/event schemas.
- Docker image зібрано; Compose запускає PostgreSQL і API від непривілейованого user.
- Через контейнерний API створено проєкт, роботу й бінарний артефакт.
- Після перезапуску API-контейнера збережено роботу, байти файла та журнал подій;
  повтор команди повернув попередній результат без нової події.

Інтеграційні тести включають два незалежні stdio MCP-процеси, реальний HTTP-сервер,
long polling, SSE replay через Last-Event-ID, конкурентні claims, прострочення
leases, відмову старому виконавцю, ідемпотентність, rollback, міжпроєктні зовнішні
ключі, ролі та відкликання доступу, історію перевірок флагів, receipts і файли.

Локальні тести виконано на Python 3.14, контейнерний запуск — на Python 3.13.
Є upstream deprecation warning Starlette щодо httpx у TestClient; тести пройшли.

Окремо пройдено [живе приймання Claude Code та Codex-субагентів](LIVE-ACCEPTANCE.md),
включно з реальною доставкою в контекст активної Claude-моделі.
Ще не перевірено: два фізичні пристрої, нативні hooks окремого Codex CLI,
інші CLI/IDE, Windows/macOS, тривале навантаження та відмова хоста/диска.
Інтеграції CTF-платформ також залишаються окремою роботою.

## Вебінтерфейс

Перевірено створення CTF і завдань, видачу agent token та експорт MCP-конфігурації,
відсутність tokens у browser storage, дві сесії зі спільними роботами, окремі стани
перевірки й відправлення флага, receipts спостерігача, binary upload/download із
перевіркою хешу, повтор після втраченої відповіді без дублювання, збереження виданих
credentials під час збою оновлення та початкового підключення, відкликання доступу,
рендеринг зовнішнього вмісту як тексту й мобільний viewport без загального overflow.

Переглянуто desktop і mobile screenshots. Статичні файли постачаються всередині
Python wheel; Docker image збирається без Node. Нових таблиць і міграцій для UI
не потрібно: він використовує наявні команди та колекції.

## Доставка агентам

Receiver і Codex hook перевіряються як окремі процеси з реальним API. Перевірено
атомарність локального запису, restart/replay, незмінність emission cursor при
паралельних hooks, повтор receipt після втраченої відповіді, фільтрацію приватних
повідомлень, обмеження розміру контексту, явне processing, відкликання доступу,
невідому версію події та збій bookkeeping після виводу hook JSON.

Встановлений Codex CLI прийняв згенеровані hook tables через strict startup config
validation. Це перевірка конфігурації без запуску моделі; приймання живого turn
саме в окремому Codex CLI залишається окремим кроком. Dev Compose перевірено через live static mount і
спостережений Python reload, зі збереженням постійних volumes.

## Різнорідні runtimes

Перевірено версійований hello з довільною назвою provider/model, повтор delivery
до явного ack після перезапуску, незалежні consumers, атомарність ack, replay,
оновлення старого кешу, відкликання доступу та інвалідацію вже processed-повідомлень.
Клієнт може задавати бюджет контексту для своєї моделі. Некоректні та завеликі
JSONL frames не зупиняють наступні запити й не повертають вміст секретного input.

Один інтеграційний прогін поєднує Python/MCP, Node/JSONL, plain HTTP і Claude hook
під окремими identities в одному CTF. Додатково реально пройдено мережевий сценарій
між двома ізольованими контейнерами: Python/MCP у Debian та Node/HTTP у Alpine.
Вони обмінялися роботою, повідомленнями, бінарним артефактом і знахідкою; перевірено
SHA-256 і видимість завершення роботи з іншого клієнта. Це різні середовища та
інтерфейси, а не підтвердження двох фізичних пристроїв або живих модельних сесій.

## Живі модельні учасники

Після мережевих тестів окремий прогін пройшов із Claude Code 2.1.252
(`claude-sonnet-5`) і двома реальними Codex collaboration subagents.
Координатор використовував MCP, виконавець — MCP і нативні Claude hooks,
незалежний спостерігач — HTTP. Файли й результати передавалися через сервер.
Перевірено `SessionStart` і доставку нового маркера через `PreToolUse` після
ініціалізації моделі, запис доказу до inbox, відновлення тієї самої роботи після
завершення процесу, `unverified → confirmed`, provenance локального checker,
зворотне приватне підтвердження та явні `processed`.

Початкова помилка allowlist `python`/`python3` була в конфігурації harness;
повторний запуск явно дозволив потрібні інтерпретатори. Обхід permissions не
вмикався. Перевіряли серверні результати, а не лише exit code CLI.
Деталі, межі й санітизований звіт: [LIVE-ACCEPTANCE.md](LIVE-ACCEPTANCE.md).

## Робочий контекст і люди без акаунтів, 0.2

Нові тести перевіряють budget/scope/privacy контексту, повні resource details,
literal search, handoff rollback/replay і старі leases, outbox receipts, приватний
connection file без змішування env, HttpOnly/path/SameSite/Secure cookies,
cookie CSRF, revoke і незалежні identities у двох вкладках.
MCP-процес із connection file передав роботу JSONL-клієнту через реальний HTTP;
використано нові typed tools і перевірено структуровану помилку stale worker.

У браузері пройдено запрошення людини без email/password, відкриття файла доступу,
refresh/restore, logout, різні ролі в одному browser context, відхилення файла
чужого сервера, receipts у Надісланих і передачу роботи з поясненням.

Додатковий живий Claude Code (`claude-sonnet-5`) почав із get_context, знайшов
evidence пошуком, прочитав повний finding за UUID й використав typed tools для
progress/release/message/ack. Parent Codex через HTTP одразу взяв ту саму роботу
й прочитав handoff, не очікуючи expiry. **13/13 контрольних умов**; 10 MCP-викликів
моделі без legacy bootstrap або повного каталогу схем. Завдання свідомо не позначали
завершеним: очікуваний людський review залишався невиконаним.
[Санітизований звіт](acceptance/agent-ux-2026-11-01.json).

## Людина, артефакти й увага, 0.3

Локально: 63 Python-тести на PostgreSQL 17, 9 Node-тестів, 20 Playwright-тестів.
Перевірено заборону байтів до рішення й explicit start, agent-owner без права
human approval/видачі human identity, передачу роботи з новим дозволом, README
із provenance, причину архівування та відновлення, прострочений lease, номінацію
людині без автоматичного claim, унікальні імена, TTL activity, точні дублікати,
ідемпотентний replay, status grouping, privacy, expiry, priority/fairness, ліміти
120/20 sends і reply chain. Attention має бюджет навіть для великих тіл; повний
ресурс зберігає весь текст і не підтверджується читанням.

Окремий MCP subprocess проходить review → рішення тестової людини через HTTP
→ start/download/report; JSONL бачить approval через messages/attention. Hook
отримує рішення перед історичною чергою, зберігає emission після перезапуску,
не змінюючи стабільний generic delivery packet. Старий сервер має FIFO fallback.

Реальний локальний demo після міграції перевірено в Chromium desktop/mobile;
старі матеріали збережені. Нові матеріали, фото, plain text, explicit approval,
README-download та archive/restore проходять браузерні тести. Два справжні
Claude Code процеси підтвердили gated bytes та аналіз випадкового маркера:
див. [живе приймання](LIVE-ACCEPTANCE.md). Human approval у цьому прогоні —
симуляція тестового стенда, не дія реальної людини.
