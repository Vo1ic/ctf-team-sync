"""Hypothesis chains, attachments and the challenge dossier."""

from .conftest import enroll, execute, headers


def root(project):
    return f"/api/v1/projects/{project['project']['id']}"


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def test_hypothesis_chain_attachments_and_dossier(client, project):
    agent = enroll(client, project, "Vovk")
    token = agent["token"]
    challenge = ok(
        execute(
            client,
            project,
            "challenge.create",
            {
                "title": "Opening Speech Pt. 3",
                "category": "forensics",
                "points": 40,
                "description": "ODT. https://ctf.example/files/a.odt?token=SIGNED",
                "source_url": "https://ctf.example/challenges#os3?token=SIGNED",
            },
        )
    )
    task = ok(
        execute(client, project, "task.create", {"title": "Solve", "challenge_id": challenge["id"]})
    )
    hexed = ok(
        execute(
            client,
            project,
            "finding.create",
            {
                "title": "Flag is the hex in meta.xml generator",
                "body": "4354464c... decodes to a flag",
                "evidence": "unzip -p a.odt meta.xml",
                "kind": "hypothesis",
                "task_id": task["id"],
            },
            token=token,
        )
    )
    assert hexed["status"] == "open" and hexed["challenge_id"] == challenge["id"]
    planted = ok(
        execute(
            client,
            project,
            "finding.create",
            {
                "title": "meta.xml mtime differs: hex planted later",
                "body": "Decoy",
                "evidence": "zipinfo -T: 08:08:40 vs 06:04:18\n```nested fence```",
                "kind": "observation",
                "task_id": task["id"],
                "based_on": [hexed["id"]],
                "refutes": [hexed["id"]],
            },
            token=token,
        )
    )
    detail = client.get(
        root(project) + f"/resources/findings/{hexed['id']}", headers=headers(token)
    ).json()
    assert detail["item"]["status"] == "refuted"
    assert {item["relation"] for item in detail["related"]["links"]["items"]} == {
        "leads_to",
        "refutes",
    }
    result = ok(
        execute(
            client,
            project,
            "finding.create",
            {
                "title": "CRC of COMMAND.COM",
                "body": "Easter egg prints the flag",
                "evidence": "unicorn emu -> D3C7",
                "kind": "result",
                "task_id": task["id"],
                "based_on": [planted["id"]],
            },
            token=token,
        )
    )
    flag = ok(
        execute(
            client,
            project,
            "flag.create",
            {"value": "CTF{D3C7}", "task_id": task["id"]},
            token=token,
        )
    )
    ok(
        execute(
            client,
            project,
            "link.create",
            {
                "source_type": "finding",
                "source_id": result["id"],
                "target_type": "flag",
                "target_id": flag["id"],
                "relation": "leads_to",
            },
            token=token,
        )
    )
    # Invalid edge shapes are rejected; repeating an edge is idempotent.
    bad = execute(
        client,
        project,
        "link.create",
        {
            "source_type": "flag",
            "source_id": flag["id"],
            "target_type": "finding",
            "target_id": result["id"],
            "relation": "supports",
        },
        token=token,
    )
    assert bad.status_code == 422
    # Attach a real file to the task.
    upload = client.post(
        root(project) + "/artifacts",
        headers={**headers(token), "Idempotency-Key": "odt"},
        params={"name": "OpeningSpeech.odt"},
        content=b"PK\x03\x04 fake odt",
    )
    assert upload.status_code == 201
    artifact = upload.json()
    attach = ok(
        execute(
            client,
            project,
            "link.create",
            {
                "source_type": "artifact",
                "source_id": artifact["id"],
                "target_type": "task",
                "target_id": task["id"],
                "relation": "attached_to",
            },
            token=token,
        )
    )
    again = ok(
        execute(
            client,
            project,
            "link.create",
            {
                "source_type": "artifact",
                "source_id": artifact["id"],
                "target_type": "task",
                "target_id": task["id"],
                "relation": "attached_to",
            },
            token=token,
        )
    )
    assert again["id"] == attach["id"]
    ok(
        execute(
            client,
            project,
            "resource.update",
            {
                "collection": "challenges",
                "expected_version": 1,
                "changes": {"writeup": "ODT → COMMAND.COM → CRC16 → flag."},
            },
            challenge["id"],
        )
    )
    dossier = client.get(
        root(project) + f"/challenges/{challenge['id']}/dossier", headers=headers(token)
    ).json()
    text = dossier["markdown"]
    assert text.startswith("# Opening Speech Pt. 3")
    assert "ODT → COMMAND.COM → CRC16 → flag." in text
    assert "```mermaid" in text and "спростовує --x" in text
    assert "~~Flag is the hex in meta.xml generator~~" in text
    assert "OpeningSpeech.odt" in text and f"artifact:{artifact['id']}" in text
    assert "SIGNED" not in text.split("## Умова")[0]
    assert "````" in text  # evidence containing ``` gets a longer fence
    kinds = {node["type"] for node in dossier["graph"]["nodes"]}
    assert kinds == {"finding", "flag"}
    assert {edge["relation"] for edge in dossier["graph"]["edges"]} == {"leads_to", "refutes"}
    markdown = client.get(
        root(project) + f"/challenges/{challenge['id']}/dossier.md", headers=headers(token)
    )
    assert markdown.headers["content-type"].startswith("text/markdown")
    links = client.get(root(project) + "/collections/links", headers=headers(token)).json()
    assert len(links["items"]) == 5
    removed = ok(execute(client, project, "link.delete", {}, attach["id"], token))
    assert removed["deleted"] is True
    stranger = enroll(client, project, "Other")
    first = links["items"][0]["id"]
    assert execute(client, project, "link.delete", {}, first, stranger["token"]).status_code == 403
