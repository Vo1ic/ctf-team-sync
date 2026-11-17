"""End-to-end setup: init-project, join, whoami, mcp-config, workspace and CTFd import."""

import json
import socket
import sys
import time
from threading import Thread

import pytest
import uvicorn
from fastapi import FastAPI, Header

from team_sync import agent

from .conftest import headers


def run_cli(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["team-sync-agent", *argv])
    code = agent.main()
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def fake_ctfd():
    app = FastAPI()
    challenges = [
        {"id": 7, "name": "Silent Analyst Pt. 1", "value": 100, "category": "Forensics"},
        {"id": 9, "name": "Radio Lviv", "value": 250, "category": "Misc"},
    ]

    def authorized(authorization):
        return authorization == "Token ctfd-test-token"

    @app.get("/api/v1/challenges")
    def listing(authorization: str = Header(default="")):
        if not authorized(authorization):
            return {"success": False}
        return {"success": True, "data": challenges}

    @app.get("/api/v1/challenges/{ident}")
    def detail(ident: int, authorization: str = Header(default="")):
        item = next(c for c in challenges if c["id"] == ident)
        return {
            "success": authorized(authorization),
            "data": {
                **item,
                "description": f"Description of {item['name']}",
                "files": ["/files/abc/dump.e01?token=signed"],
                "hints": [{"id": 1, "cost": 50}],
                "connection_info": "nc chal.example 31337" if ident == 9 else None,
            },
        }

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    server.should_exit = True
    thread.join(timeout=10)
    listener.close()


def test_setup_flow_from_empty_server_to_workspace(
    client, live_server, fake_ctfd, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("TEAM_SYNC_BOOTSTRAP_TOKEN", "test-bootstrap-secret")
    code, out, err = run_cli(
        monkeypatch,
        capsys,
        "init-project",
        "--url",
        live_server,
        "--name",
        "CTF Lviv 2026",
        "--owner",
        "Vo1ic",
        "--out",
        str(tmp_path / "ts"),
    )
    assert code == 0, err
    folder = tmp_path / "ts" / "ctf-lviv-2026"
    owner_file, invite_file = folder / "owner.json", folder / "invite.json"
    assert (owner_file.stat().st_mode & 0o777) == 0o600
    assert (
        "test-bootstrap-secret" not in out
        and json.loads(owner_file.read_text())["token"] not in out
    )

    code, out, err = run_cli(
        monkeypatch,
        capsys,
        "join",
        "--invite",
        str(invite_file),
        "--human",
        "Olena",
        "--host",
        "opencode",
        "--workspace",
        str(tmp_path / "work"),
    )
    assert code == 0, err
    assert "joined:" in out and "Olena-" in out
    config = json.loads(out[out.index("{") :])
    server = config["mcp"]["team-sync"]
    assert server["environment"]["TEAM_SYNC_ACCESS_FILE"] == str(invite_file.resolve())
    assert "підказки" in (tmp_path / "work" / "CTF.md").read_text()

    code, out, err = run_cli(
        monkeypatch, capsys, "--access-file", str(owner_file), "whoami", "--title"
    )
    assert code == 0, err
    assert out.startswith("\033]0;Vo1ic · CTF Lviv 2026\007")
    assert json.loads(out[out.index("{") :])["name"] == "Vo1ic"

    code, out, err = run_cli(
        monkeypatch, capsys, "--access-file", str(owner_file), "mcp-config", "--host", "codex"
    )
    assert code == 0 and "[mcp_servers.team_sync]" in out

    monkeypatch.setenv("CTFD_TOKEN", "ctfd-test-token")
    for _ in range(2):  # Re-running the import is safe.
        code, out, err = run_cli(
            monkeypatch,
            capsys,
            "--access-file",
            str(owner_file),
            "import-ctfd",
            "--ctfd-url",
            fake_ctfd,
        )
        assert code == 0, err
    assert "2 challenges · 0 new · 2 already present · 0 tasks created" in out
    owner = json.loads(owner_file.read_text())
    base = f"/api/v1/projects/{owner['project_id']}"
    imported = client.get(base + "/collections/challenges", headers=headers(owner["token"]))
    by_title = {item["title"]: item for item in imported.json()["items"]}
    radio = by_title["Radio Lviv"]
    assert radio["points"] == 250 and radio["category"] == "Misc"
    assert "nc chal.example 31337" in radio["description"]
    assert f"{fake_ctfd}/files/abc/dump.e01" in radio["description"]
    assert "DO NOT unlock" in radio["description"]
    tasks = client.get(base + "/collections/tasks", headers=headers(owner["token"])).json()
    assert sorted(t["title"] for t in tasks["items"]) == [
        "Solve: Radio Lviv",
        "Solve: Silent Analyst Pt. 1",
    ]

    code, out, err = run_cli(
        monkeypatch,
        capsys,
        "--access-file",
        str(owner_file),
        "workspace",
        "--root",
        str(tmp_path / "work"),
    )
    assert code == 0, err
    challenge_md = tmp_path / "work" / "forensics" / "silent-analyst-pt-1" / "CHALLENGE.md"
    text = challenge_md.read_text()
    assert "Solve: Silent Analyst Pt. 1" in text and "task_id" in text

    monkeypatch.delenv("CTFD_TOKEN")
    code, out, err = run_cli(
        monkeypatch,
        capsys,
        "--access-file",
        str(owner_file),
        "import-ctfd",
        "--ctfd-url",
        fake_ctfd,
    )
    assert code == 1 and "ctfd" in err


def test_run_publishes_experiment_with_output_and_files(
    client, project, live_server, tmp_path, monkeypatch, capsys
):
    from .conftest import enroll, execute

    agent = enroll(client, project, "Runner")
    access = tmp_path / "agent.json"
    access.write_text(
        json.dumps(
            {"url": live_server, "project_id": project["project"]["id"], "token": agent["token"]}
        )
    )
    task = execute(client, project, "task.create", {"title": "Probe"}).json()
    monkeypatch.chdir(tmp_path)
    script = "print('magic 89504e47'); open('out.bin','wb').write(b'PNG')"
    code, out, err = run_cli(
        monkeypatch,
        capsys,
        "--access-file",
        str(access),
        "run",
        "--task",
        task["id"],
        "--note",
        "check magic bytes",
        "--file",
        "out.bin",
        "--",
        sys.executable,
        "-c",
        script,
    )
    assert code == 0, err
    assert "magic 89504e47" in out and "team-sync: finding" in err
    base = f"/api/v1/projects/{project['project']['id']}"
    detail = client.get(base + f"/resources/tasks/{task['id']}", headers=headers(agent["token"]))
    finding = detail.json()["related"]["findings"]["items"][0]
    full = client.get(
        base + f"/resources/findings/{finding['id']}", headers=headers(agent["token"])
    )
    item = full.json()
    assert item["item"]["kind"] == "observation" and "exit=0" in item["item"]["evidence"]
    assert "magic 89504e47" in item["item"]["evidence"]
    assert item["related"]["links"]["items"][0]["relation"] == "attached_to"
    code, out, err = run_cli(
        monkeypatch,
        capsys,
        "--access-file",
        str(access),
        "run",
        "--task",
        task["id"],
        "--",
        sys.executable,
        "-c",
        "raise SystemExit(3)",
    )
    assert code == 3 and "failed_attempt" in err
