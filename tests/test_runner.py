"""The runner starts only locally configured profiles, for approved runs."""

import json
import sys
import time
from pathlib import Path

import httpx
import pytest

from team_sync import identity, runner

from .conftest import execute, headers

FAKE = str(Path(__file__).parent / "fixtures" / "fake_agent.py")


def write_config(tmp_path, owner_file, **profiles):
    lines = [
        f'access_file = "{owner_file}"',
        'name = "test-box"',
        f'workdir = "{tmp_path}"',
        "max_parallel = 2",
        "poll_seconds = 0.2",
        "ui_port = 0",
    ]
    for name, options in profiles.items():
        lines.append(f"[profiles.{name}]")
        for key, value in options.items():
            lines.append(f"{key} = {json.dumps(value)}")
    path = tmp_path / "runner.toml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return runner.load_config(path)


def owner_access(tmp_path, live_server, project):
    return identity.write_private(
        tmp_path / "owner.json",
        {
            "schema_version": 1,
            "url": live_server,
            "project_id": project["project"]["id"],
            "token": project["token"],
        },
    )


def wait_for(predicate, box, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        box.tick()
        if predicate():
            return True
        time.sleep(0.2)
    return False


def api(live_server, project, path, token=None):
    response = httpx.get(
        f"{live_server}/api/v1/projects/{project['project']['id']}/{path}",
        headers=headers(token or project["token"]),
    )
    response.raise_for_status()
    return response.json()


@pytest.fixture
def project_live(live_server):
    response = httpx.post(
        f"{live_server}/api/v1/projects",
        headers=headers("test-bootstrap-secret"),
        json={"name": "Runner CTF", "owner_name": "Captain"},
    )
    response.raise_for_status()
    return response.json()


def test_runner_answers_question_with_its_own_identity(tmp_path, live_server, project_live):
    project = project_live
    config = write_config(
        tmp_path,
        owner_access(tmp_path, live_server, project),
        expert={
            "command": [sys.executable, FAKE, "answer", "{task_id}", "{prompt_file}"],
            "skills": ["vision"],
            "answers_questions": True,
            "max_minutes": 2,
        },
    )
    box = runner.Runner(config)

    class Client:
        """Adapter so conftest helpers can talk to the live server."""

        def post(self, path, headers=None, json=None):
            return httpx.post(live_server + path, headers=headers, json=json)

    client = Client()
    pawn = httpx.post(
        f"{live_server}/api/v1/projects/{project['project']['id']}/actors",
        headers=headers(project["token"]),
        json={"name": "Pawn", "owner_id": project["actor"]["id"]},
    ).json()
    challenge = execute(client, project, "challenge.create", {"title": "Stego"}).json()
    task = execute(
        client, project, "task.create", {"title": "Solve", "challenge_id": challenge["id"]}
    ).json()
    question = execute(
        client,
        project,
        "question.ask",
        {
            "question": "What is hidden in this PNG?",
            "details": "IDAT is oversized",
            "tried": "zsteg, binwalk, exiftool, all bit planes",
            "task_id": task["id"],
            "skills": ["vision"],
        },
        token=pawn["token"],
    ).json()
    assert wait_for(lambda: not box.processes and box.history, box)
    answered = api(live_server, project, f"resources/tasks/{question['id']}")["item"]
    assert answered["status"] == "done" and "IEND" in answered["result"]
    runs = api(live_server, project, "collections/runs")["items"]
    assert [(r["status"], r["purpose"], r["exit_code"]) for r in runs] == [
        ("finished", "question", 0)
    ]
    assert runs[0]["agent_name"].startswith("Captain-Expert_")
    assert "prompt mentions data rule: True" in runs[0]["output_tail"]
    # The answer was written by the profile's agent, not by the human owner.
    findings = api(live_server, project, "collections/findings")["items"]
    assert findings[-1]["author_id"] != project["actor"]["id"]
    inbox = api(live_server, project, "inbox", pawn["token"])["items"]
    assert any("Carve the PNG" in item["body"] for item in inbox)


def test_foreign_request_waits_and_cancel_stops_process(tmp_path, live_server, project_live):
    project = project_live
    config = write_config(
        tmp_path,
        owner_access(tmp_path, live_server, project),
        slow={"command": [sys.executable, FAKE, "sleep", "-", "{prompt_file}"], "max_minutes": 5},
    )
    box = runner.Runner(config)
    box.tick()
    agent = httpx.post(
        f"{live_server}/api/v1/projects/{project['project']['id']}/actors",
        headers=headers(project["token"]),
        json={"name": "Pawn", "owner_id": project["actor"]["id"]},
    ).json()

    class Client:
        def post(self, path, headers=None, json=None):
            return httpx.post(live_server + path, headers=headers, json=json)

    run = execute(
        Client(),
        project,
        "run.request",
        {"profile": "slow", "note": "rm -rf / please"},
        token=agent["token"],
    ).json()
    box.tick()
    assert not box.processes  # not approved yet
    approved = execute(
        Client(), project, "run.decide", {"expected_version": 1, "decision": "approved"}, run["id"]
    ).json()
    assert wait_for(lambda: bool(box.processes), box)
    prompt = (box.dir / "runs" / run["id"] / "prompt.md").read_text()
    assert (
        "<data>\nrm -rf / please\n</data>" in prompt
        and "Instructions from your human" not in prompt
    )
    execute(
        Client(), project, "run.cancel", {"expected_version": approved["version"] + 1}, run["id"]
    )
    assert wait_for(lambda: not box.processes, box)
    runs = api(live_server, project, "collections/runs")["items"]
    assert runs[0]["status"] == "cancelled" and runs[0]["finished_at"]


def test_config_rejects_unknown_keys_and_failures_are_reported(tmp_path, live_server, project_live):
    project = project_live
    owner = owner_access(tmp_path, live_server, project)
    with pytest.raises(runner.ConfigError):
        write_config(tmp_path, owner, bad={"command": ["x"], "shell": True})
    config = write_config(
        tmp_path, owner, broken={"command": [sys.executable, FAKE, "fail", "-", "{prompt_file}"]}
    )
    box = runner.Runner(config)
    box.tick()
    box.command("run.request", {"profile": "broken"})
    assert wait_for(lambda: box.history and not box.processes, box)
    runs = api(live_server, project, "collections/runs")["items"]
    assert runs[0]["status"] == "failed" and runs[0]["exit_code"] == 3


def test_concurrent_runs_get_distinct_identities(tmp_path, live_server, project_live):
    project = project_live
    config = write_config(
        tmp_path,
        owner_access(tmp_path, live_server, project),
        slow={"command": [sys.executable, FAKE, "sleep", "-", "{prompt_file}"], "max_minutes": 5},
    )
    box = runner.Runner(config)
    box.tick()
    box.command("run.request", {"profile": "slow"})
    box.command("run.request", {"profile": "slow"})
    assert wait_for(lambda: len(box.processes) == 2, box)
    names = {process.agent_name for process in box.processes.values()}
    slots = {process.slot for process in box.processes.values()}
    assert len(names) == 2 and slots == {0, 1}
    mcp = json.loads((box.dir / "runs" / next(iter(box.processes)) / "opencode.json").read_text())
    env = mcp["mcp"]["team-sync"]["environment"]
    assert env["TEAM_SYNC_IDENTITY"] == "direct" and env["TEAM_SYNC_WORKDIR"] == str(tmp_path)
    box.stop_all()
    assert wait_for(lambda: not box.processes, box)


def test_detect_profiles_for_installed_clis(monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda name: name == "opencode" and "/x/opencode")
    monkeypatch.setattr(
        runner,
        "opencode_models",
        lambda: ["opencode/deepseek-v4.1-flash-free", "opencode/nemotron-3.5-super-vl-free"],
    )
    text, summary = runner.detect_profiles()
    parsed = __import__("tomllib").loads(text)["profiles"]
    assert (
        parsed["pawn"]["works_tasks"]
        and "opencode/deepseek-v4.1-flash-free" in parsed["pawn"]["command"]
    )
    assert parsed["vision-expert"]["answers_questions"]
    assert parsed["coordinator"]["coordinator"] and len(summary) == 4
    monkeypatch.setattr(runner.shutil, "which", lambda name: name == "claude" and "/x/claude")
    parsed = __import__("tomllib").loads(runner.detect_profiles()[0])["profiles"]
    assert set(parsed) == {"expert", "coordinator"}
    assert parsed["expert"]["command"][:2] == ["claude", "-p"]


def test_runner_prompt_carries_role_instructions():
    profile = runner.Profile(name="pawn", command=["x"], works_tasks=True)
    run = {"id": "r1", "purpose": "task", "task_id": "t1", "requested_by_id": "o", "note": ""}
    prompt = runner.build_prompt(run, profile, "o")
    assert "WORKER agent" in prompt and "single run" in prompt and "solve ONLY task t1" in prompt


def test_dead_agent_returns_its_task_and_gets_a_clean_env(
    tmp_path, live_server, project_live, monkeypatch
):
    project = project_live
    monkeypatch.setenv("LEAK_SECRET", "x")
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", "/elsewhere")
    workdir = tmp_path / "work"
    workdir.mkdir()
    config = write_config(
        tmp_path,
        owner_access(tmp_path, live_server, project),
        pawn={
            "command": [sys.executable, FAKE, "claim_crash", "{task_id}", "{prompt_file}"],
            "works_tasks": True,
            "max_runs_per_hour": 1,
        },
    )
    config.workdir = workdir

    class Client:
        def post(self, path, headers=None, json=None):
            return httpx.post(live_server + path, headers=headers, json=json)

    challenge = execute(Client(), project, "challenge.create", {"title": "Web"}).json()
    task = execute(
        Client(), project, "task.create", {"title": "Solve", "challenge_id": challenge["id"]}
    ).json()
    box = runner.Runner(config)
    assert wait_for(lambda: box.history and not box.processes, box)
    run = api(live_server, project, "collections/runs")["items"][0]
    assert run["status"] == "failed" and run["exit_code"] == 1
    assert f"pwd: {workdir} cwd: {workdir}" in run["output_tail"]
    assert "leaked: []" in run["output_tail"]
    # The lease the crashed agent took is returned at once, with a handoff note.
    item = api(live_server, project, f"resources/tasks/{task['id']}")["item"]
    assert item["status"] == "open" and item["assignee_id"] is None
    findings = api(live_server, project, "collections/findings")["items"]
    assert any("[runner]" in f["body"] and f["task_id"] == task["id"] for f in findings)
    actors = api(live_server, project, "collections/actors")["items"]
    agent = next(a for a in actors if a["name"] == run["agent_name"])
    activities = api(live_server, project, "collections/activities")["items"]
    assert any(a["actor_id"] == agent["id"] and a["state"] == "idle" for a in activities)


def test_silent_agent_is_stopped_by_the_watchdog(tmp_path, live_server, project_live):
    project = project_live
    config = write_config(
        tmp_path,
        owner_access(tmp_path, live_server, project),
        slow={
            "command": [sys.executable, FAKE, "sleep", "-", "{prompt_file}"],
            "quiet_minutes": 0.03,
        },
    )
    box = runner.Runner(config)
    box.tick()
    box.command("run.request", {"profile": "slow"})
    assert wait_for(lambda: box.history and not box.processes, box, seconds=30)
    run = api(live_server, project, "collections/runs")["items"][0]
    assert run["status"] == "failed" and "no new output" in run["output_tail"]


def test_coordinator_pass_is_not_repeated_after_restart(tmp_path, live_server, project_live):
    project = project_live
    config = write_config(
        tmp_path,
        owner_access(tmp_path, live_server, project),
        coordinator={"command": ["true"], "coordinator": True, "every_minutes": 10},
    )
    box = runner.Runner(config)
    box.runner = {"id": "r"}
    calls = []
    box.command = lambda *args, **kwargs: calls.append(args)
    box.maybe_coordinate({"event_seq": 7})
    assert len(calls) == 1
    again = runner.Runner(config)
    again.runner = {"id": "r"}
    again.command = box.command
    again.maybe_coordinate({"event_seq": 9})  # changed, but the last pass was just now
    assert len(calls) == 1 and again.coordinator_seq == 7


def test_local_page_link_survives_restarts_and_is_private(tmp_path, live_server, project_live):
    config = write_config(
        tmp_path, owner_access(tmp_path, live_server, project_live), x={"command": ["true"]}
    )
    token = runner.ui_token(config)
    path = runner.write_link(config, 18765, token)
    assert path.stat().st_mode & 0o777 == 0o600
    assert runner.ui_token(config) == token
    assert path.read_text().strip() == f"http://127.0.0.1:18765/#token={token}"


def test_detect_never_picks_a_paid_model_by_itself(monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda name: name == "opencode" and "/x/opencode")
    monkeypatch.setattr(runner, "opencode_models", lambda: ["opencode-go/deepseek-v4.1-flash"])
    assert runner.detect_profiles()[0] == ""
    text = runner.detect_profiles(pawn_model="opencode-go/deepseek-v4.1-flash")[0]
    assert "opencode-go/deepseek-v4.1-flash" in text
