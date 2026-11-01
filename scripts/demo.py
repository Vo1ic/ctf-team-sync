"""Create synthetic CTF data or replay simulated team activity through the real API."""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEMO_MARKER = "TEAM_SYNC_DEMO_V1"


def save_private(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


class Demo:
    def __init__(self, access):
        self.access = access
        self.api = httpx.Client(
            base_url=f"{access['url'].rstrip('/')}/api/v1/projects/{access['project_id']}/",
            timeout=30,
        )

    def request(self, method, path, actor=None, **kwargs):
        response = self.api.request(
            method,
            path,
            headers={"Authorization": "Bearer " + (actor or self.access)["token"]},
            **kwargs,
        )
        response.raise_for_status()
        return response.json()

    def command(self, operation, arguments, resource=None, actor=None):
        return self.request(
            "POST",
            "commands",
            actor,
            json={
                "operation": operation,
                "resource_id": resource,
                "arguments": arguments,
                "idempotency_key": str(uuid4()),
            },
        )

    def file(self, name, content, actor):
        data = content.encode() if isinstance(content, str) else content
        response = self.api.post(
            "artifacts",
            params={"name": name},
            content=data,
            headers={
                "Authorization": "Bearer " + actor["token"],
                "Idempotency-Key": str(uuid4()),
                "Content-Type": "application/octet-stream",
            },
        )
        response.raise_for_status()
        artifact = response.json()
        if artifact["sha256"] != hashlib.sha256(data).hexdigest():
            raise ValueError("Artifact integrity mismatch")
        return artifact

    def message(self, body, actor, recipient=None):
        return self.command(
            "message.send", {"body": "[ДЕМО] " + body, "recipient_id": recipient}, actor=actor
        )

    def finding(self, title, body, kind, evidence, actor, challenge=None, task=None):
        return self.command(
            "finding.create",
            {
                "title": title,
                "body": body,
                "kind": kind,
                "evidence": evidence,
                "challenge_id": challenge,
                "task_id": task,
            },
            actor=actor,
        )

    def close(self):
        self.api.close()


def seed(args):
    values = dict(os.environ)
    if args.env_file:
        for line in args.env_file.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key in {"TEAM_SYNC_URL", "TEAM_SYNC_BOOTSTRAP_TOKEN"}:
                values.setdefault(key, value.strip().strip("\"'"))
    url = args.url or values.get("TEAM_SYNC_URL", "http://localhost:8000")
    with httpx.Client(timeout=30) as api:
        response = api.post(
            url.rstrip("/") + "/api/v1/projects",
            headers={"Authorization": "Bearer " + values["TEAM_SYNC_BOOTSTRAP_TOKEN"]},
            json={
                "name": "DEMO · Nightshift CTF",
                "owner_name": "Volic · капітан",
                "profile": "jeopardy",
                "capabilities": {"flags": True},
                "rules": f"{DEMO_MARKER}\nДемонстрація командної роботи людей і різних агентів. "
                "Усі завдання, флаги, відправлення та дії агентів — синтетичні. "
                "Моделі не запускаються; зовнішні цілі не використовуються.\n"
                "Спочатку беріть роботу на себе. Публікуйте докази й файли. "
                "Кандидат у флаг потребує незалежної перевірки координатора. "
                "Статус перевірки та результат відправлення ведуться окремо.",
            },
        )
        response.raise_for_status()
        created = response.json()
    directory = ROOT / ".team-sync" / "demo" / created["project"]["id"]
    directory.mkdir(parents=True, mode=0o700)
    access = {"url": url, "project_id": created["project"]["id"], "token": created["token"]}
    save_private(directory / "access.json", access)
    demo = Demo(access)
    try:
        actors = {"captain": {"id": created["actor"]["id"], "token": created["token"]}}
        people = [
            ("atlas", "Atlas · координатор", "agent", "coordinator", ["планування", "перевірка"]),
            ("olena", "Олена · аналітик", "human", "worker", ["forensics", "network"]),
            ("danylo", "Данило · reverse", "human", "worker", ["reverse", "debugging"]),
            ("claude", "Claude Code · Linux", "agent", "worker", ["web", "MCP", "hooks"]),
            ("codex", "Codex CLI · Windows", "agent", "worker", ["crypto", "MCP"]),
            ("cursor", "Cursor · macOS", "agent", "worker", ["forensics", "HTTP API"]),
            ("gemini", "Gemini CLI · Linux", "agent", "worker", ["recon", "JSONL"]),
            ("opencode", "OpenCode · Linux", "agent", "worker", ["reverse", "HTTP API"]),
            ("observer", "Гість · спостерігач", "human", "observer", ["огляд"]),
            ("legacy", "Legacy agent · старий доступ", "agent", "worker", ["архів"]),
        ]
        for key, name, kind, role, skills in people:
            result = demo.request(
                "POST",
                "actors",
                json={
                    "name": name,
                    "kind": kind,
                    "role": role,
                    "owner_id": actors["captain"]["id"] if kind == "agent" else None,
                    "capabilities": skills,
                },
            )
            actors[key] = {"id": result["actor"]["id"], "token": result["token"]}
        save_private(directory / "identities.json", actors)
        challenges = {}
        for key, title, category, description in [
            (
                "web",
                "Session Vault · 300",
                "Web",
                (
                    "Синтетичний кабінет vault.demo.invalid. Дослідити модель доступу та "
                    "зібрати відтворювані докази."
                ),
            ),
            (
                "crypto",
                "Split Signal · 250",
                "Crypto",
                (
                    "Три фрагменти повідомлення з різними кодуваннями. Зібрати відповідь "
                    "і звірити локальним checker."
                ),
            ),
            (
                "forensics",
                "After Hours · 400",
                "Forensics",
                "Відновити послідовність подій із демонстраційного CSV мережевих метаданих.",
            ),
            (
                "reverse",
                "Clockwork · 350",
                "Reverse",
                (
                    "Проаналізувати синтетичні нотатки про байткод; перевірити гіпотезу "
                    "щодо порядку операцій."
                ),
            ),
            (
                "misc",
                "Welcome Channel · 50",
                "Misc",
                "Ознайомитися з правилами, знайти демо-флаг і зафіксувати результат.",
            ),
        ]:
            challenges[key] = demo.command(
                "challenge.create",
                {"title": title, "category": category, "description": description},
            )["id"]
        files = {}
        fixtures = [
            (
                "briefing",
                "team-briefing.md",
                (
                    "# Nightshift CTF — DEMO\n\nФіктивне змагання для огляду "
                    "інтерфейсу.\n\n- Atlas координує.\n- Люди й агенти працюють "
                    "паралельно.\n- Докази та файли публікуємо перед передачею роботи.\n-"
                    " DEMO{...} — синтетичні флаги.\n"
                ),
                "atlas",
            ),
            (
                "web",
                "web-observations.json",
                json.dumps(
                    {
                        "demo": True,
                        "host": "vault.demo.invalid",
                        "observations": [
                            {"route": "/profile", "status": 200},
                            {"route": "/admin", "status": 403},
                        ],
                        "conclusion": "Потрібна незалежна перевірка моделі доступу.",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "claude",
            ),
            (
                "crypto",
                "split-signal.json",
                json.dumps(
                    {
                        "demo": True,
                        "fragments": [
                            {"order": 1, "encoding": "base64", "value": "dGVhbQ=="},
                            {"order": 2, "encoding": "hex", "value": "73796e63"},
                            {"order": 3, "encoding": "rot13", "value": "jbexf"},
                        ],
                        "decoded": "team|sync|works",
                    },
                    indent=2,
                ),
                "codex",
            ),
            (
                "traffic",
                "traffic-summary.csv",
                (
                    "demo,time_utc,source,destination,protocol,bytes\ntrue,12:04:01,workstation.demo.invalid,archive.demo.invalid,TLS,840\ntrue,12:04:03,workstation.demo.invalid,dns.demo.invalid,DNS,96\ntrue,12:04:08,workstation.demo.invalid,archive.demo.invalid,TLS,16384\ntrue,12:04:11,analyst.demo.invalid,archive.demo.invalid,TLS,2048\n"
                ),
                "olena",
            ),
            (
                "reverse",
                "clockwork-notes.txt",
                (
                    "DEMO — синтетичний результат аналізу, програми тут немає.\nblock_0: "
                    "load 3 fragments\nblock_1: reorder [2, 0, 1]\nblock_2: compare "
                    "checksum\nГіпотеза: порядок блоків важливіший за кодування рядків.\n"
                ),
                "opencode",
            ),
            (
                "checker",
                "checker-output.txt",
                (
                    "DEMO LOCAL CHECKER\nSplit Signal: PASS\nWelcome Channel: "
                    "PASS\nSession Vault candidate: pending independent "
                    "reproduction\nЖодних звернень до реальної CTF-платформи.\n"
                ),
                "atlas",
            ),
        ]
        for key, name, content, author in fixtures:
            files[key] = demo.file(name, content, actors[author])["id"]
        tasks = {}
        definitions = [
            ("web-map", "Карта маршрутів і модель доступу", "web", "claude", "done"),
            ("web-check", "Незалежно відтворити web-кандидат", "web", "gemini", "running"),
            ("web-notes", "Зібрати фінальні докази для Session Vault", "web", "claude", "running"),
            ("crypto-decode", "Декодувати три фрагменти Split Signal", "crypto", "codex", "done"),
            ("crypto-check", "Перевірити відповідь локальним checker", "crypto", "atlas", "done"),
            (
                "crypto-variant",
                "Перевірити альтернативний порядок фрагментів",
                "crypto",
                None,
                "open",
            ),
            (
                "traffic-extract",
                "Виділити метадані мережевого потоку",
                "forensics",
                "olena",
                "done",
            ),
            (
                "traffic-timeline",
                "Побудувати часову шкалу After Hours",
                "forensics",
                "cursor",
                "running",
            ),
            ("traffic-review", "Звірити часові зони й пропуски журналу", "forensics", None, "open"),
            (
                "reverse-strings",
                "Розмітити блоки байткоду Clockwork",
                "reverse",
                "opencode",
                "running",
            ),
            (
                "reverse-check",
                "Перевірити порядок блоків у debugger",
                "reverse",
                "danylo",
                "running",
            ),
            ("reverse-report", "Підготувати короткий reverse write-up", "reverse", None, "open"),
            ("welcome", "Прочитати правила й зафіксувати welcome-флаг", "misc", "captain", "done"),
        ]
        for key, title, challenge, worker, status in definitions:
            task = demo.command(
                "task.create",
                {
                    "title": title,
                    "description": (
                        "Демонстраційна робота. Перед завершенням додати знахідку з доказами "
                        "й повідомити команду."
                    ),
                    "challenge_id": challenges[challenge],
                },
                actor=actors["atlas"],
            )
            if worker:
                task = demo.command(
                    "task.claim",
                    {"expected_version": task["version"], "lease_seconds": 3600},
                    task["id"],
                    actors[worker],
                )
                if status == "done":
                    task = demo.command(
                        "task.complete",
                        {
                            "expected_version": task["version"],
                            "result": (
                                "ДЕМО: роботу завершено, матеріали опубліковано в артефактах і "
                                "пов'язаних знахідках."
                            ),
                        },
                        task["id"],
                        actors[worker],
                    )
            tasks[key] = task["id"]
        findings = [
            (
                "Карта web-маршрутів готова",
                (
                    "Виділено профіль і адміністративний розділ. Різні відповіді самі по "
                    "собі ще не доводять проблему доступу."
                ),
                "observation",
                "web",
                "web-map",
                "claude",
                "web",
            ),
            (
                "Web-кандидат потребує другого погляду",
                (
                    "Гіпотеза щодо перевірки ролей. Gemini відтворює сценарій незалежно; "
                    "до підтвердження флаг не вважаємо розв'язанням."
                ),
                "hypothesis",
                "web",
                "web-check",
                "gemini",
                "web",
            ),
            (
                "Сліпа підстановка ідентифікаторів не дала результату",
                (
                    "Попередній підхід не підтвердив гіпотезу. Зберігаємо невдалу спробу,"
                    " щоб інші її не повторювали."
                ),
                "failed_attempt",
                "web",
                "web-map",
                "claude",
                "web",
            ),
            (
                "Split Signal: фрагменти зібрано",
                (
                    "base64 → team; hex → sync; rot13 → works. Обрано порядок 1 → 2 → 3; "
                    "результат передано координатору."
                ),
                "result",
                "crypto",
                "crypto-decode",
                "codex",
                "crypto",
            ),
            (
                "Checker підтвердив криптографічний результат",
                (
                    "Незалежна локальна перевірка пройшла. Підтвердження флага окреме від"
                    " звіту про відправлення."
                ),
                "result",
                "crypto",
                "crypto-check",
                "atlas",
                "checker",
            ),
            (
                "Великий TLS-потік о 12:04:08",
                (
                    "У синтетичному CSV є помітне збільшення обсягу даних. Це "
                    "спостереження, а не висновок про вміст зашифрованого трафіку."
                ),
                "observation",
                "forensics",
                "traffic-extract",
                "olena",
                "traffic",
            ),
            (
                "Можлива похибка часової зони",
                (
                    "Зіставляємо UTC і локальний час. Перед фінальним висновком потрібно "
                    "звірити джерела."
                ),
                "hypothesis",
                "forensics",
                "traffic-timeline",
                "cursor",
                "traffic",
            ),
            (
                "Гіпотеза про прихований DNS-вміст не підтвердилася",
                (
                    "Демонстраційні метадані DNS не містять потрібного payload. "
                    "Переходимо до побудови часової шкали."
                ),
                "failed_attempt",
                "forensics",
                "traffic-extract",
                "olena",
                "traffic",
            ),
            (
                "Clockwork: виділено три блоки",
                (
                    "Порядок [2, 0, 1] може впливати на підсумкову перевірку. Данило "
                    "перевіряє цю гіпотезу окремо."
                ),
                "hypothesis",
                "reverse",
                "reverse-strings",
                "opencode",
                "reverse",
            ),
            (
                "Прямий порядок блоків не пройшов",
                (
                    "Локальна демонстраційна перевірка відхилила варіант [0, 1, 2]. "
                    "Зафіксовано негативний результат."
                ),
                "failed_attempt",
                "reverse",
                "reverse-check",
                "danylo",
                "reverse",
            ),
            (
                "Welcome Channel завершено",
                (
                    "Демо-флаг із правил збережено, локальну перевірку й приклад "
                    "результату відправлення записано."
                ),
                "result",
                "misc",
                "welcome",
                "captain",
                "briefing",
            ),
        ]
        for title, body, kind, challenge, task, author, file in findings:
            demo.finding(
                title,
                body,
                kind,
                f"ДЕМО. Джерело: artifact:{files[file]}",
                actors[author],
                challenges[challenge],
                tasks[task],
            )
        flags = {}
        flag_definitions = [
            ("welcome", "DEMO{welcome_to_nightshift}", "misc", "captain", "confirmed", "accepted"),
            ("crypto", "DEMO{team_sync_works}", "crypto", "codex", "confirmed", "accepted"),
            ("web", "DEMO{vault_candidate_review_me}", "web", "claude", None, None),
            (
                "forensics",
                "DEMO{timeline_still_incomplete}",
                "forensics",
                "cursor",
                None,
                "unknown",
            ),
            ("wrong", "DEMO{wrong_block_order}", "reverse", "opencode", "rejected", "rejected"),
            ("expired", "DEMO{round_01_expired}", "web", "gemini", "confirmed", "rejected"),
        ]
        for key, value, challenge, author, verification, outcome in flag_definitions:
            args_flag = {
                "value": value,
                "challenge_id": challenges[challenge],
                "target": "demo.invalid",
                "round": "01" if key == "expired" else "02",
            }
            if key == "expired":
                args_flag["expires_at"] = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
            flag = demo.command("flag.create", args_flag, actor=actors[author])
            if verification:
                conclusion = (
                    "варіант не збігається з очікуваним результатом"
                    if verification == "rejected"
                    else "локальне звіряння пройдено"
                )
                flag = demo.command(
                    "flag.verify",
                    {
                        "expected_version": flag["version"],
                        "status": verification,
                        "method": "local_checker",
                        "evidence": f"ДЕМО: {conclusion}. artifact:{files['checker']}",
                    },
                    flag["id"],
                    actors["atlas"],
                )
            if outcome:
                demo.command(
                    "submission.create",
                    {
                        "outcome": outcome,
                        "evidence": (
                            "ДЕМО: імітований результат відправлення. Реальна CTF-платформа не "
                            "викликалася."
                        ),
                    },
                    flag["id"],
                    actors["captain"],
                )
            flags[key] = flag["id"]
        # Preserve a visible history of a corrected human assessment.
        flag = next(
            f
            for f in demo.request("GET", "collections/flags")["items"]
            if f["id"] == flags["crypto"]
        )
        for status, note in [
            ("rejected", "Потрібно повторно звірити порядок фрагментів."),
            ("confirmed", "Повторне незалежне звіряння підтвердило початкову відповідь."),
        ]:
            flag = demo.command(
                "flag.verify",
                {
                    "expected_version": flag["version"],
                    "status": status,
                    "method": "manual",
                    "evidence": "ДЕМО: " + note,
                },
                flag["id"],
                actors["atlas"],
            )
        messages = [
            (
                "atlas",
                None,
                (
                    "Командо, беремо роботи перед стартом. Не дублюємо аналіз; файли й "
                    "негативні результати публікуємо одразу."
                ),
                "processed",
            ),
            (
                "claude",
                "captain",
                (
                    "Session Vault: карта маршрутів готова, кандидат уже у Флагах. Gemini"
                    " проводить незалежну перевірку."
                ),
                "received",
            ),
            (
                "codex",
                "captain",
                (
                    "Split Signal розв'язано. Фрагменти та checker-output доступні у "
                    "Файлах; Atlas підтвердив результат."
                ),
                "processed",
            ),
            (
                "olena",
                None,
                (
                    "Оновила traffic-summary.csv. Cursor, використай серверний артефакт "
                    "для часової шкали."
                ),
                "received",
            ),
            (
                "cursor",
                "captain",
                (
                    "Є питання щодо часової зони After Hours. Залишила гіпотезу; "
                    "остаточного висновку ще немає."
                ),
                None,
            ),
            (
                "opencode",
                None,
                (
                    "Clockwork: прямий порядок блоків не спрацював. Данило перевіряє "
                    "альтернативу; не повторюйте початкову спробу."
                ),
                None,
            ),
            (
                "atlas",
                "captain",
                (
                    "Зверни увагу: round_01_expired локально коректний, але відхилений у "
                    "демо-відправленні через минулий раунд. Це різні статуси."
                ),
                None,
            ),
            (
                "gemini",
                "claude",
                (
                    "Підхопив незалежну перевірку Session Vault. Повний контекст беру з "
                    "опублікованого JSON."
                ),
                None,
            ),
            (
                "captain",
                None,
                (
                    "ДЕМО можна вільно оглядати: відкрийте картку завдання, історію "
                    "флага, скачайте файл і перевірте статуси повідомлень."
                ),
                None,
            ),
        ]
        for sender, recipient, body, stage in messages:
            message = demo.message(
                body, actors[sender], actors[recipient]["id"] if recipient else None
            )
            if stage:
                demo.command("message.ack", {"stage": stage}, message["id"], actors["captain"])
        demo.request("POST", f"actors/{actors['legacy']['id']}/revoke")
        save_private(
            directory / "manifest.json",
            {
                "marker": DEMO_MARKER,
                "challenges": challenges,
                "tasks": tasks,
                "files": files,
                "flags": flags,
            },
        )
        summary = {
            "project_id": access["project_id"],
            "url": url + "/#project=" + access["project_id"],
            "access_file": str(directory / "access.json"),
            "counts": {
                "actors": len(actors),
                "challenges": len(challenges),
                "tasks": len(tasks),
                "findings": len(findings),
                "flags": len(flags),
                "files": len(files),
                "messages": len(messages),
            },
        }
        save_private(directory / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        demo.close()


def play(args):
    access = json.loads(args.access_file.read_text())
    directory = args.access_file.parent
    manifest = json.loads((directory / "manifest.json").read_text())
    actors = json.loads((directory / "identities.json").read_text())
    demo = Demo(access)
    try:
        snapshot = demo.request("GET", "snapshot")
        if DEMO_MARKER not in snapshot["project"]["rules"] or manifest["marker"] != DEMO_MARKER:
            raise ValueError("Playback requires a generated demo project")
        playback = uuid4().hex[:8]

        def pause(stage):
            print(json.dumps({"episode": episode, "stage": stage}, ensure_ascii=False), flush=True)
            time.sleep(args.interval)

        for episode in range(1, args.episodes + 1):
            challenge, worker, subject = [
                ("web", "claude", "Звірити докази Session Vault"),
                ("forensics", "cursor", "Уточнити часову шкалу After Hours"),
                ("reverse", "opencode", "Повторити перевірку блоків Clockwork"),
                ("crypto", "codex", "Перевірити новий набір Split Signal"),
            ][(episode - 1) % 4]
            task = demo.command(
                "task.create",
                {
                    "title": f"Демо #{episode} · {subject}",
                    "description": (
                        "Імітація поточної роботи. Цей сценарій виконує скрипт, модель не "
                        "запускається."
                    ),
                    "challenge_id": manifest["challenges"][challenge],
                },
                actor=actors["atlas"],
            )
            demo.message(
                (
                    f"Починаємо демонстраційний епізод #{episode}: {subject}. Нову роботу "
                    f"видно на дошці."
                ),
                actors["atlas"],
            )
            pause("Роботу створено")
            task = demo.command(
                "task.claim",
                {"expected_version": task["version"], "lease_seconds": 3600},
                task["id"],
                actors[worker],
            )
            pause("Агент взяв роботу")
            artifact = demo.file(
                f"demo-{playback}-{episode}.json",
                json.dumps(
                    {
                        "demo": True,
                        "episode": episode,
                        "check": "passed",
                        "note": "Згенеровані дані для огляду UI, не результат роботи моделі.",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                actors[worker],
            )
            demo.finding(
                f"Демо #{episode}: матеріали опубліковано",
                (
                    "Перевірка завершена в імітаційному сценарії; файл із результатом "
                    "доступний усім учасникам проєкту."
                ),
                "result",
                f"ДЕМО. artifact:{artifact['id']}",
                actors[worker],
                manifest["challenges"][challenge],
                task["id"],
            )
            pause("Файл і знахідка з'явилися")
            flag = demo.command(
                "flag.create",
                {
                    "value": f"DEMO{{{playback}_episode_{episode}}}",
                    "challenge_id": manifest["challenges"][challenge],
                    "target": "demo.invalid",
                    "round": "demo",
                },
                actor=actors[worker],
            )
            demo.message(
                (
                    f"Демо #{episode}: флаг-кандидат опубліковано; чекаємо незалежного "
                    f"підтвердження Atlas."
                ),
                actors[worker],
                actors["captain"]["id"],
            )
            pause("Флаг ще не перевірений")
            demo.command(
                "flag.verify",
                {
                    "expected_version": flag["version"],
                    "status": "confirmed",
                    "method": "local_checker",
                    "evidence": f"ДЕМО: імітована незалежна перевірка. artifact:{artifact['id']}",
                },
                flag["id"],
                actors["atlas"],
            )
            pause("Координатор підтвердив флаг")
            demo.command(
                "task.complete",
                {
                    "expected_version": task["version"],
                    "result": (
                        f"ДЕМО: матеріали збережено, кандидат підтверджено. artifact:"
                        f"{artifact['id']}"
                    ),
                },
                task["id"],
                actors[worker],
            )
            demo.command(
                "submission.create",
                {
                    "outcome": "accepted",
                    "evidence": "ДЕМО: імітований позитивний звіт, без звернення до платформи.",
                },
                flag["id"],
                actors["atlas"],
            )
            demo.message(
                (
                    f"Епізод #{episode} завершено: робота закрита, флаг підтверджено, "
                    f"результат відправлення записано окремо."
                ),
                actors["atlas"],
            )
            pause("Роботу завершено")
        print("Демонстраційне відтворення завершено.", flush=True)
    finally:
        demo.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser(
        "seed", help="Create a new demo CTF; existing projects stay intact"
    )
    create.add_argument("--env-file", type=Path)
    create.add_argument("--url")
    replay = commands.add_parser("play", help="Simulate visible activity in a generated demo CTF")
    replay.add_argument("--access-file", type=Path, required=True)
    replay.add_argument("--episodes", type=int, choices=range(1, 21), default=4)
    replay.add_argument("--interval", type=float, default=15)
    args = parser.parse_args()
    if args.command == "play" and not 1 <= args.interval <= 120:
        parser.error("--interval must be between 1 and 120 seconds")
    try:
        seed(args) if args.command == "seed" else play(args)
    except KeyboardInterrupt:
        print("Демонстрацію зупинено; створені дані збережено.", file=sys.stderr)
        return 130
    except Exception as error:
        # Never echo credentials, request headers, or provider configuration.
        print(
            (
                f"Demo stopped ({type(error).__name__}); any issued access remains in "
                f".team-sync/demo/."
            ),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
