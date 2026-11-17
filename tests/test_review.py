"""A challenge counts as solved only with a human-confirmed flag; hints need approval."""

from datetime import timedelta

from sqlalchemy import select, update

from team_sync import models as m

from .conftest import enroll, execute, headers


def root(project):
    return f"/api/v1/projects/{project['project']['id']}"


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def claimed_task(client, project, token, title="Solve"):
    challenge = ok(execute(client, project, "challenge.create", {"title": f"C {title}"}))
    task = ok(
        execute(client, project, "task.create", {"title": title, "challenge_id": challenge["id"]})
    )
    task = ok(execute(client, project, "task.claim", {"expected_version": 1}, task["id"], token))
    return challenge, task


def inbox(client, project, token):
    return client.get(root(project) + "/inbox", headers=headers(token)).json()["items"]


def test_task_waits_for_confirmed_flag_and_reopens_on_rejection(client, project):
    agent = enroll(client, project, "Vovk")
    token = agent["token"]
    challenge, task = claimed_task(client, project, token)
    done = ok(
        execute(
            client,
            project,
            "task.complete",
            {"expected_version": task["version"], "result": "flag found"},
            task["id"],
            token,
        )
    )
    assert done["status"] == "review"
    assert any(
        "чекає підтвердження флага" in item["body"]
        for item in inbox(client, project, project["token"])
    )
    # Nobody can claim it, and the worker cannot force it to done.
    assert (
        execute(
            client,
            project,
            "task.claim",
            {"expected_version": done["version"]},
            task["id"],
            enroll(client, project)["token"],
        ).status_code
        == 409
    )
    forced = execute(
        client,
        project,
        "resource.update",
        {"collection": "tasks", "expected_version": done["version"], "changes": {"status": "done"}},
        task["id"],
        token,
    )
    assert forced.status_code == 403 and forced.json()["code"] == "flag_confirmation_required"
    flag = ok(
        execute(
            client,
            project,
            "flag.create",
            {"value": "CTF{nope}", "task_id": task["id"]},
            token=token,
        )
    )
    ok(
        execute(
            client,
            project,
            "flag.verify",
            {"expected_version": 1, "status": "rejected"},
            flag["id"],
        )
    )
    reopened = client.get(
        root(project) + f"/resources/tasks/{task['id']}", headers=headers(token)
    ).json()["item"]
    assert (
        reopened["status"] == "open" and reopened["requested_assignee_id"] == agent["actor"]["id"]
    )
    assert any("відхилено" in item["body"] for item in inbox(client, project, token))
    # Second attempt: confirmed flag finishes the waiting task.
    again = ok(
        execute(
            client,
            project,
            "task.claim",
            {"expected_version": reopened["version"]},
            task["id"],
            token,
        )
    )
    review = ok(
        execute(
            client,
            project,
            "task.complete",
            {"expected_version": again["version"], "result": "real flag"},
            task["id"],
            token,
        )
    )
    assert review["status"] == "review"
    good = ok(
        execute(
            client,
            project,
            "flag.create",
            {"value": "CTF{yes}", "task_id": task["id"]},
            token=token,
        )
    )
    ok(
        execute(
            client,
            project,
            "flag.verify",
            {"expected_version": 1, "status": "confirmed"},
            good["id"],
        )
    )
    final = client.get(
        root(project) + f"/resources/tasks/{task['id']}", headers=headers(token)
    ).json()["item"]
    assert final["status"] == "done"
    dossier = client.get(
        root(project) + f"/challenges/{challenge['id']}/dossier", headers=headers(token)
    ).json()
    assert "розв'язано" in dossier["challenge"]["state"]
    # With a confirmed flag already in place, or for helper work, completion is immediate.
    _, helper = claimed_task(client, project, token, "Helper")
    assert (
        ok(
            execute(
                client,
                project,
                "task.complete",
                {
                    "expected_version": helper["version"],
                    "result": "spectrogram",
                    "without_flag": True,
                },
                helper["id"],
                token,
            )
        )["status"]
        == "done"
    )


def test_hint_requires_evidence_and_human_decision(client, project):
    agent = enroll(client, project, "Taras")
    token = agent["token"]
    challenge, task = claimed_task(client, project, token)
    asked = execute(
        client,
        project,
        "hint.request",
        {"task_id": task["id"], "reason": "stuck", "cost": 50},
        token=token,
    )
    assert asked.status_code == 409 and asked.json()["code"] == "evidence_required"
    ok(
        execute(
            client,
            project,
            "finding.create",
            {
                "title": "EXIF empty",
                "body": "nothing",
                "evidence": "exiftool",
                "kind": "failed_attempt",
                "task_id": task["id"],
            },
            token=token,
        )
    )
    request = ok(
        execute(
            client,
            project,
            "hint.request",
            {"task_id": task["id"], "reason": "need city", "cost": 50, "hint_ref": "Hint 1"},
            token=token,
        )
    )
    assert request["status"] == "pending" and request["challenge_id"] == challenge["id"]
    assert any(
        "просить підказку" in item["body"] and item["priority"] == "urgent"
        for item in inbox(client, project, project["token"])
    )
    assert (
        execute(
            client,
            project,
            "hint.decide",
            {"expected_version": 1, "decision": "approved"},
            request["id"],
            token,
        ).status_code
        == 403
    )
    assert (
        execute(
            client,
            project,
            "hint.used",
            {"expected_version": 1, "content": "x"},
            request["id"],
            token,
        ).status_code
        == 409
    )
    decided = ok(
        execute(
            client,
            project,
            "hint.decide",
            {"expected_version": 1, "decision": "approved", "note": "ok"},
            request["id"],
        )
    )
    used = ok(
        execute(
            client,
            project,
            "hint.used",
            {"expected_version": decided["version"], "content": "Look at the tram number"},
            request["id"],
            token,
        )
    )
    assert used["status"] == "used" and used["finding_id"]
    text = client.get(
        root(project) + f"/challenges/{challenge['id']}/dossier.md", headers=headers(token)
    ).text
    assert "## Підказки платформи" in text and "Витрачено: 50 балів" in text
    assert "Look at the tram number" in text


def test_decoys_and_stalled_work_are_visible(client, project, app):
    agent = enroll(client, project, "Vega")
    token = agent["token"]
    challenge, task = claimed_task(client, project, token)
    ok(
        execute(
            client,
            project,
            "finding.create",
            {
                "title": "Fake flag in styles.xml",
                "body": "prompt injection",
                "evidence": "styles.xml",
                "kind": "decoy",
                "task_id": task["id"],
            },
            token=token,
        )
    )
    context = client.get(
        root(project) + "/agent-context",
        headers=headers(token),
        params={"challenge_id": challenge["id"]},
    ).json()
    assert context["sections"]["known_decoys"]["items"][0]["title"] == "Fake flag in styles.xml"
    assert (
        "## ⚠️ Відомі приманки"
        in client.get(
            root(project) + f"/challenges/{challenge['id']}/dossier.md", headers=headers(token)
        ).text
    )
    assert (
        client.get(
            root(project) + "/stalled", headers=headers(token), params={"minutes": 20}
        ).json()["items"]
        == []
    )
    with app.state.core.sessions.begin() as db:
        db.execute(
            update(m.Event)
            .where(m.Event.project_id == project["project"]["id"])
            .values(created_at=m.Event.created_at - timedelta(minutes=45))
        )
        db.execute(
            update(m.Finding)
            .where(m.Finding.project_id == project["project"]["id"])
            .values(created_at=m.Finding.created_at - timedelta(minutes=45))
        )
        assert db.scalar(select(m.Task.id).where(m.Task.id == task["id"]))
    stalled = client.get(
        root(project) + "/stalled", headers=headers(token), params={"minutes": 20}
    ).json()["items"]
    assert stalled[0]["task_id"] == task["id"] and stalled[0]["idle_minutes"] >= 44
    assert stalled[0]["assignee"] == agent["actor"]["name"]
