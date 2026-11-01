# ADR 0002: Контекст агента, передача роботи та браузер без акаунтів

Статус: прийнято, 2026-11-01. Версія застосунку 0.2.0; доменний протокол v1.

## Проблема

Агент витрачав кілька запитів на bootstrap, всі command schemas, inbox,
колекції та пошук актуальної версії. Після зупинки він міг лише чекати expiry
lease, щоб передати роботу іншому runtime. Відправник не бачив receipts доручень.
Людина вводила UUID і токен після кожного refresh; роль облікового запису
плуталася з роллю учасника конкретного CTF.

## Рішення

- Read-only workspace, resource details і search будуються в authenticated Core
  context під коротким shared project lock. Вони не змінюють доменні receipts,
  leases або курсори доставки. Є бюджет, excerpts, totals і явна пагінація.
- MCP має typed wrappers для частих дій, bounds у schemas, read-only annotations
  і вузький `command_contracts(operation)`. Обгортки використовують той самий
  command layer, ключі й version/lease checks.
- `task.release` дозволений власнику чинного lease з поточною версією. Створення
  handoff finding, дві події, перехід `running → open` і command replay record
  належать одній транзакції. Старі виконавці залишаються fenced. Зовнішній процес
  цим не зупиняється. Окремого blocked state у цій зміні немає.
- Outbox повертає лише повідомлення, створені поточною identity, і stage
  конкретного адресата. Inbox і resource/messages зберігають recipient-only
  поведінку. Чужі приватні діалоги не стають видимими owner чи observer.
- Приватний connection JSON спільний для браузера, MCP, JSONL і receiver.
  File values не змішуються зі старими env credentials. У hooks передається шлях,
  не секрет. За замовчуванням експорт MCP використовує uvx із versioned Git tag;
  локальний checkout залишається альтернативою.

## Людина та доступ

Глобальних users, email, password чи OAuth немає. Людина має проєктний доступ
із псевдонімом для provenance й конкретною роллю. Owner створює її так само,
як раніше створював human actor. Веб пропонує окремі дії для людини та агента.

Посилання доступу містить credential у fragment. Сторінка прибирає fragment
через replaceState до мережевих запитів. Після перевірки Bearer сервер встановлює
HttpOnly, SameSite=Strict, project-path-scoped cookies на 7 днів, Secure на HTTPS.
Клієнт прибирає Bearer із ApiClient і надалі звертається з cookie. Токени не
пишуться в localStorage/sessionStorage; там лише UUID identity/проєкту й назви.
Файли з інших origins не надсилаються поточному серверу.

Для незалежних вкладок cookie також scoped за actor ID, а вкладка передає
несекретний X-Team-Sync-Actor. Refresh відновлює identity своєї вкладки. Останній
доступ є default лише для нового вікна без вибраної identity. Logout прибирає
cookies поточного credential, не іншої identity. Відкликання серверного actor
одразу робить усі його browser cookies недійсними.

Cookie-мутації потребують точного same-origin Origin і X-Team-Sync-Browser: 1.
Bearer API для будь-яких CLI/IDE збережено; bootstrap creation не приймає
project cookies. Logout не відкликає доступ агента. Поділ ролей і проєктна
ізоляція перевіряються тим самим Core незалежно від способу подачі credential.

## Сумісність і наслідки

Нових таблиць або колонок немає; міграція БД не потрібна. Старі CLI/env, MCP tools,
HTTP endpoints, JSONL methods і event schema version 1 працюють далі. Нові клієнти
бачать додатковий command/event type task.release; старі споживачі можуть його
ігнорувати, не ігноруючи невідому schema_version.

Людина більше не вводить секрет після кожного refresh. Браузерний профіль тепер
зберігає доступ до CTF до logout, expiry або revoke; доступ спільного пристрою
вимагає завершення підключення. Немає обіцянки idle wake-up, external execution
fencing, повного folder sync або автоматичної відправки флагів.

Перевірки покривають budget/privacy, literal search, resource scope, handoff
rollback/replay/fencing, cookie CSRF/path/roles/revoke, паралельні browser identities,
відновлення, вихід, file import, outbox і реальні MCP/JSONL subprocesses.
