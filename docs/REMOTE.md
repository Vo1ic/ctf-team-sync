# Team Sync на віддаленому сервері

Сервер працює на VPS/домашньому сервері, а ти й агенти працюєте з того самого
ноутбука, що й раніше. Потрібно: SSH-доступ до сервера (будь-який користувач у групі
`docker`), Docker з плагіном compose і `rsync` з обох боків. `uv` на сервері не
обов'язковий — CLI запускається всередині контейнера.

## Приватний режим (рекомендовано): лише SSH-тунель

Назовні нічого не відкривається: API слухає `127.0.0.1` на сервері, а ти дістаєшся
до нього через `ssh -L`.

```sh
scripts/remote.sh deploy ctf@server.example "CTF Lviv 2026" Vo1ic
scripts/remote.sh tunnel ctf@server.example          # тримай відкритим під час роботи
# веб: http://127.0.0.1:18000
```

`deploy` копіює код (без `.env`, `.team-sync`, `.venv`), запускає на сервері
`scripts/quickstart.sh` (секрети, вільний порт, compose, проєкт, інвайт) і забирає
`owner.json` / `invite.json` у `.team-sync/remote-<host>/`. URL у цих файлах —
`http://127.0.0.1:18000`, тож MCP-агенти на ноутбуці працюють через тунель без
змін. Інший локальний порт: `--local-port 18001` (і для `deploy`, і для `tunnel`).

Агенти, що працюють **на самому сервері** (іншим користувачем по SSH), беруть ті
самі файли й міняють `url` на `http://127.0.0.1:<TEAM_SYNC_HOST_PORT>` з `.env`
сервера. MCP перечитує файл сам, перезапуск не потрібен.

## Публічний режим: HTTPS для всієї команди

```sh
scripts/remote.sh deploy ctf@server.example "CTF Lviv 2026" Vo1ic --domain sync.example.org
```

Потрібен DNS-запис `sync.example.org → IP сервера` і відкриті порти 80/443.
`deploy/compose.public.yaml` додає Caddy з автоматичним сертифікатом; сервер довіряє
`X-Forwarded-*` лише від проксі, тому cookie браузера отримують `Secure`, а перевірка
same-origin бачить `https://`. Посилання й файли доступу містять `https://домен`.

## Обслуговування

```sh
scripts/remote.sh update ctf@server.example   # новий код; БД і файли зберігаються, міграції застосуються
scripts/remote.sh logs   ctf@server.example
scripts/remote.sh fetch  ctf@server.example   # ще раз забрати access-файли
```

Резервна копія: томи `postgres` і `artifacts` проєкту compose на сервері
(`docker compose exec db pg_dump -U team_sync team_sync > backup.sql` + каталог артефактів).

## Кілька ідентичностей з одного пристрою

- **Веб:** кожна вкладка тримає власну ідентичність (людина, координатор, агент) —
  відкрий посилання доступу іншої людини в новій вкладці.
- **Агенти:** кожен MCP-процес автоматично отримує окрему ідентичність
  `<Людина>-<Позивний>_xxxx`; `TEAM_SYNC_IDENTITY=direct` — працювати строго під файлом.
- **CLI:** `team-sync-agent --access-file <файл> whoami --title` показує, хто ти, і
  ставить назву вкладки терміналу.
