"""Add clearly labeled human/agent workflow examples to a generated demo project."""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from demo import DEMO_MARKER, ROOT, Demo, save_private


def seed(access_file):
    access = json.loads(access_file.read_text())
    demo = Demo(access)
    output = ROOT / ".team-sync" / "demo" / access["project_id"] / "workflows.json"
    try:
        snapshot = demo.request("GET", "snapshot")
        if DEMO_MARKER not in snapshot["project"]["rules"]:
            raise ValueError("Use only a generated synthetic demo project")
        if output.exists():
            print("Workflow examples already exist in this demo project.")
            return
        output.parent.mkdir(parents=True, exist_ok=True)
        actors = {}
        for name in ["Nova", "Atlas", "Kira"]:
            issued = demo.request(
                "POST",
                "actors",
                json={
                    "name": name,
                    "owner_id": snapshot["actor"]["id"],
                    "capabilities": ["DEMO: simulated workflow"],
                },
            )
            actors[name] = {**issued["actor"], "token": issued["token"]}

        # Use a separate upload call because Idempotency-Key is required for blobs.
        def upload(name, body):
            response = demo.api.post(
                "artifacts",
                params={
                    "name": name,
                    "requires_review": True,
                    "media_type": "text/plain",
                },
                content=body.encode(),
                headers={
                    "Authorization": f"Bearer {access['token']}",
                    "Idempotency-Key": str(uuid4()),
                },
            )
            response.raise_for_status()
            return response.json()

        cases = {}
        for title, stage, name in [
            ("01-чекає-дозволу.txt", "pending", "Nova"),
            ("02-дозволено-чекає-початку.txt", "approved", "Atlas"),
            ("03-готовий-README.txt", "useful", "Kira"),
            ("04-непридатний-архів.txt", "unusable", "Kira"),
            ("05-відхилено-людиною.txt", "declined", "Atlas"),
        ]:
            material = upload(
                title,
                "[ДЕМО] Синтетичні нотатки CTF. "
                "Це приклад для інтерфейсу; зовнішніх цілей і справжніх секретів немає.\n"
                "Спостереження: кілька повторних статусів можна згорнути в один.\n",
            )
            agent = actors[name]
            task = demo.command("artifact.review.request", {}, material["id"], agent)
            if stage != "pending":
                task = demo.command(
                    "artifact.review.decide",
                    {
                        "expected_version": task["version"],
                        "decision": "declined" if stage == "declined" else "approved",
                        "note": "[ДЕМО] Симуляція рішення людини для огляду інтерфейсу.",
                    },
                    task["id"],
                )
            if stage in {"useful", "unusable"}:
                task = demo.command(
                    "artifact.review.start",
                    {
                        "expected_version": task["version"],
                    },
                    task["id"],
                    agent,
                )
                task = demo.command(
                    "artifact.review.complete",
                    {
                        "expected_version": task["version"],
                        "verdict": stage,
                        "readme": "# [ДЕМО] README\n\nАвтор: " + agent["name"] + "\n\n"
                        "## Що перевірено\nСинтетичні нотатки; демонструємо походження звіту.\n\n"
                        "## Результат\n"
                        + (
                            "Корисний приклад для наступного кроку команди."
                            if stage == "useful"
                            else "Матеріал не стосується поточного завдання."
                        ),
                        "reason": "[ДЕМО] Не містить релевантних даних; можна відновити."
                        if stage == "unusable"
                        else "",
                    },
                    task["id"],
                    agent,
                )
            cases[stage] = {"artifact_id": material["id"], "task_id": task["id"]}
        delegated = demo.command(
            "task.create",
            {
                "title": "[ДЕМО] Людині: уточнити правила й часові межі CTF",
                "description": "Atlas просить людину уточнити правила. Демонстраційне доручення.",
                "requested_assignee_id": snapshot["actor"]["id"],
            },
            actor=actors["Atlas"],
        )
        for name, state, detail in [
            ("Nova", "waiting_human", "[ДЕМО] Взяв нотатки; чекає дозволу людини"),
            ("Atlas", "ready", "[ДЕМО] Дозвіл отримано; аналіз ще не починав"),
            ("Kira", "idle", "[ДЕМО] Опублікувала README; непридатний матеріал в архіві"),
        ]:
            demo.command(
                "activity.report",
                {"state": state, "detail": detail, "ttl_seconds": 600},
                actor=actors[name],
            )
        for _ in range(3):
            demo.command(
                "message.send",
                {
                    "body": "[ДЕМО] Повторний однаковий статус перевірки",
                    "kind": "status",
                    "group_key": "demo-scan",
                },
                actor=actors["Kira"],
            )
        save_private(output, {"cases": cases, "actors": actors, "human_task_id": delegated["id"]})
        print(
            json.dumps(
                {
                    "project_id": access["project_id"],
                    "examples": len(cases),
                    "url": access["url"] + "/#project=" + access["project_id"],
                }
            )
        )
    finally:
        demo.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--access-file", type=Path, required=True)
    seed(parser.parse_args().access_file)
