"""Setup commands for people and agents: create a project, join a team, configure hosts,
lay out the workspace and import challenges from CTFd. Credentials are never printed."""

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from team_sync import identity

HOSTS = ("opencode", "claude-code", "codex")


def add_parsers(commands):
    init = commands.add_parser(
        "init-project", help="Create a project on a running server (owner, once per CTF)"
    )
    init.add_argument("--url", default="http://127.0.0.1:8000")
    init.add_argument(
        "--public-url",
        help="URL written into access files when clients reach the server differently "
        "(https://domain, or http://127.0.0.1:<port> through an SSH tunnel)",
    )
    init.add_argument("--name", required=True, help="CTF/project name")
    init.add_argument("--owner", required=True, help="Your nickname, e.g. Vo1ic")
    init.add_argument(
        "--out", default=".team-sync", help="Directory for owner.json and invite.json"
    )
    init.add_argument(
        "--bootstrap-env",
        default="TEAM_SYNC_BOOTSTRAP_TOKEN",
        help="Environment variable (or .env key) holding the server bootstrap token",
    )
    init.add_argument("--env-file", default=".env")

    invite = commands.add_parser("invite", help="Create an invite file for a teammate")
    invite.add_argument("--out", default="team-sync-invite.json")
    invite.add_argument("--label", default="")
    invite.add_argument("--hours", type=int, default=72)
    invite.add_argument("--uses", type=int, default=10)
    invite.add_argument("--role", choices=["worker", "coordinator", "observer"], default="worker")

    join = commands.add_parser(
        "join", help="Redeem an invite file: create yourself and your first agent"
    )
    join.add_argument("--invite", required=True, help="Invite file from the team owner")
    join.add_argument("--human", required=True, help="Your nickname, e.g. Vo1ic")
    join.add_argument("--agent-name", help="Optional agent callsign (prefixed with your name)")
    join.add_argument("--capabilities", default="", help="Comma-separated, e.g. web,forensics")
    join.add_argument("--host", choices=HOSTS, help="Also print MCP config for this host")
    join.add_argument("--workspace", help="Write CTF.md and per-challenge folders here")

    link = commands.add_parser(
        "web-link", help="Print the browser login link for an access file (a secret: do not share)"
    )
    link.add_argument("--open", action="store_true", help="Also open it in the default browser")

    prompts = commands.add_parser(
        "prompts", help="Print the short prompts that start agents for this project"
    )
    prompts.add_argument("--id", help="Only this prompt (setup, runner, teammate, worker, ...)")

    who = commands.add_parser("whoami", help="Show which identity an access file resolves to")
    who.add_argument(
        "--title", action="store_true", help="Set this terminal's title to the identity"
    )

    config = commands.add_parser(
        "mcp-config", help="Print MCP configuration for a host (writes nothing)"
    )
    config.add_argument("--host", choices=HOSTS, required=True)
    config.add_argument("--name", default="team-sync", help="MCP server name in the host")

    workspace = commands.add_parser(
        "workspace", help="Create <root>/CTF.md and <root>/<category>/<challenge>/CHALLENGE.md"
    )
    workspace.add_argument("--root", required=True)

    run = commands.add_parser(
        "run",
        help="Run a command and publish it as a finding with exit code, output and hashes",
    )
    run.add_argument("--task", help="Task id the experiment belongs to")
    run.add_argument("--challenge", help="Challenge id (when there is no task)")
    run.add_argument("--title", help="Finding title (default: the command)")
    run.add_argument(
        "--kind",
        choices=["observation", "hypothesis", "result", "failed_attempt", "decoy"],
        help="Default: observation on exit 0, failed_attempt otherwise",
    )
    run.add_argument("--note", default="", help="What this experiment checks")
    run.add_argument("--based-on", action="append", default=[], help="Finding id (repeatable)")
    run.add_argument("--refutes", action="append", default=[], help="Finding id (repeatable)")
    run.add_argument(
        "--file", action="append", default=[], help="Output file to hash and attach (repeatable)"
    )
    run.add_argument("--tail", type=int, default=40, help="Output lines kept as evidence")
    run.add_argument("--dry-run", action="store_true", help="Run, but do not publish")
    run.add_argument("argv", nargs=argparse.REMAINDER, help="-- command and arguments")

    ctfd = commands.add_parser(
        "import-ctfd", help="Import CTFd challenges as Team Sync challenges and tasks"
    )
    ctfd.add_argument("--ctfd-url", required=True, help="e.g. https://ctflviv2026.ctfd.io")
    ctfd.add_argument(
        "--token-env", default="CTFD_TOKEN", help="Env var with a CTFd API access token"
    )
    ctfd.add_argument(
        "--session-env", default="CTFD_SESSION", help="Env var with a CTFd session cookie"
    )
    ctfd.add_argument("--dry-run", action="store_true")
    ctfd.add_argument("--no-tasks", action="store_true", help="Create challenges only")


COMMANDS = {
    "web-link",
    "prompts",
    "init-project",
    "invite",
    "join",
    "whoami",
    "mcp-config",
    "workspace",
    "import-ctfd",
    "run",
}


def run(args):
    handler = {
        "web-link": web_link,
        "prompts": print_prompts,
        "init-project": init_project,
        "invite": create_invite,
        "join": join,
        "whoami": whoami,
        "mcp-config": mcp_config,
        "workspace": workspace,
        "import-ctfd": import_ctfd,
        "run": run_experiment,
    }[args.command]
    try:
        return handler(args)
    except identity.IdentityError as error:
        print(json.dumps(error.as_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


# ----- helpers ---------------------------------------------------------------------------


def access_path(args):
    path = args.access_file or os.environ.get("TEAM_SYNC_ACCESS_FILE")
    if not path and not identity.env_access():
        raise identity.IdentityError(
            "access_file_missing",
            "No access file given",
            "Pass --access-file <file> or set TEAM_SYNC_ACCESS_FILE.",
        )
    return path


def direct(http, args):
    """Use the access file's own identity (no per-process agent enrollment)."""
    resolved = identity.resolve(http, access_path(args), mode="direct")
    return resolved


def call(http, resolved, method, path, **kwargs):
    return identity.api(
        http, resolved.url, resolved.project_id, resolved.token, method, path, **kwargs
    )


def command(http, resolved, operation, arguments, key, resource_id=None):
    body = {"operation": operation, "arguments": arguments, "idempotency_key": key}
    if resource_id:
        body["resource_id"] = resource_id
    try:
        return call(http, resolved, "POST", "commands", json=body), None
    except identity.IdentityError as error:
        return None, error


def read_env_file(path, key):
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == key:
                return value.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def mcp_server(access_file):
    """Run the bridge with this exact interpreter so no PATH/venv activation is needed."""
    env = {"TEAM_SYNC_ACCESS_FILE": str(Path(access_file).expanduser().resolve())}
    return [Path(sys.executable).as_posix(), "-m", "team_sync.mcp"], env


def render_mcp_config(host, access_file, name="team-sync"):
    argv, env = mcp_server(access_file)
    if host == "opencode":
        return json.dumps(
            {"mcp": {name: {"type": "local", "command": argv, "environment": env}}}, indent=2
        )
    if host == "claude-code":
        return json.dumps(
            {"mcpServers": {name: {"command": argv[0], "args": argv[1:], "env": env}}}, indent=2
        )
    args = ", ".join(json.dumps(item) for item in argv[1:])
    envs = ", ".join(f"{key} = {json.dumps(value)}" for key, value in env.items())
    return (
        f"[mcp_servers.{name.replace('-', '_')}]\n"
        f"command = {json.dumps(argv[0])}\nargs = [{args}]\nenv = {{ {envs} }}\n"
    )


def config_hint(host):
    return {
        "opencode": "merge into opencode.json (project) or ~/.config/opencode/opencode.json",
        "claude-code": "merge into .mcp.json in the project root",
        "codex": "append to .codex/config.toml or ~/.codex/config.toml",
    }[host]


# ----- commands --------------------------------------------------------------------------


def init_project(args):
    token = os.environ.get(args.bootstrap_env) or read_env_file(args.env_file, args.bootstrap_env)
    if not token:
        raise identity.IdentityError(
            "bootstrap_missing",
            f"{args.bootstrap_env} is not set and not found in {args.env_file}",
            "Run scripts/quickstart.sh or export the bootstrap token from the server .env.",
        )
    url = identity.validate_url(args.url)
    public = identity.validate_url(args.public_url) if args.public_url else url
    with httpx.Client(timeout=20) as http:
        identity.check_server(http, url)
        response = http.post(
            f"{url}/api/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": args.name, "owner_name": args.owner},
        )
        if response.is_error:
            raise identity.remote_problem(response, url)
        created = response.json()
        project_id = created["project"]["id"]
        out = Path(args.out).expanduser() / identity.slugify(args.name)
        owner_file = identity.write_private(
            out / "owner.json",
            {
                "schema_version": 1,
                "url": public,
                "project_id": project_id,
                "token": created["token"],
                "actor_id": created["actor"]["id"],
                "name": created["actor"]["name"],
            },
        )
        invited = identity.api(
            http, url, project_id, created["token"], "POST", "invites", json={"label": "team"}
        )
        invite_file = identity.write_private(
            out / "invite.json", {"schema_version": 1, "url": public, "invite": invited["token"]}
        )
    print(f"project:      {args.name} ({project_id})")
    print(f"owner access: {owner_file}  (keep private; this is you)")
    print(f"team invite:  {invite_file}  (give to teammates together with the MCP config)")
    print()
    print("Point every agent's MCP at owner.json (your agents) or invite.json (teammates):")
    print("  team-sync-agent --access-file <file> mcp-config --host opencode|claude-code|codex")
    return 0


def create_invite(args):
    with httpx.Client(timeout=20) as http:
        resolved = direct(http, args)
        invited = call(
            http,
            resolved,
            "POST",
            "invites",
            json={
                "label": args.label,
                "role": args.role,
                "expires_in_hours": args.hours,
                "max_uses": args.uses,
            },
        )
    path = identity.write_private(
        Path(args.out), {"schema_version": 1, "url": resolved.url, "invite": invited["token"]}
    )
    print(f"invite file: {path} (valid {args.hours}h, {args.uses} uses, role {args.role})")
    return 0


def join(args):
    access = identity.read_access(args.invite)
    if access["kind"] != "invite":
        raise identity.IdentityError(
            "not_an_invite", f"{args.invite} is not an invite file", "Ask the owner for one."
        )
    capabilities = [item.strip() for item in args.capabilities.split(",") if item.strip()]
    with httpx.Client(timeout=20) as http:
        joined, human_file, agent_file = identity.redeem_invite(
            http, access, args.human, args.agent_name, capabilities
        )
    print(f"joined:       {joined['project']['name']} as {joined['human']['actor']['name']}")
    if joined.get("agent"):
        print(f"first agent:  {joined['agent']['actor']['name']}")
    print(f"your access:  {human_file} (private)")
    if agent_file:
        print(f"agent access: {agent_file} (used automatically by the MCP bridge)")
    print(
        "MCP:          keep TEAM_SYNC_ACCESS_FILE pointing at the invite file; every agent "
        "process gets its own identity automatically."
    )
    if args.workspace:
        root = Path(args.workspace).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        (root / "CTF.md").write_text(joined["briefing"], encoding="utf-8")
        print(f"CTF.md:       {root / 'CTF.md'}")
    if args.host:
        print()
        print(f"# {config_hint(args.host)}")
        print(render_mcp_config(args.host, args.invite))
    return 0


def whoami(args):
    with httpx.Client(timeout=20) as http:
        resolved = direct(http, args)
    info = resolved.public()
    if args.title:
        # OSC 0 sets the terminal/tab title; printed to the terminal, not to logs.
        sys.stdout.write(f"\033]0;{resolved.terminal_title}\007")
        sys.stdout.flush()
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


def web_link(args):
    """Same #join= link the web builds: the token stays in the URL fragment."""
    import base64
    import webbrowser

    with httpx.Client(timeout=20) as http:
        resolved = direct(http, args)
    value = json.dumps(
        {"project_id": resolved.project_id, "token": resolved.token}, separators=(",", ":")
    )
    encoded = base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")
    link = f"{resolved.url}/#join={encoded}"
    print(link)
    if args.open:
        webbrowser.open(link)
    return 0


def print_prompts(args):
    from team_sync import roles

    with httpx.Client(timeout=20) as http:
        resolved = direct(http, args)
    human = resolved.owner_name or resolved.actor["name"]
    data = roles.prompts(resolved.project_name, human)
    for item in data["items"]:
        if args.id and item["id"] != args.id:
            continue
        print(f"## {item['title']} ({item['id']})\n{item['who']}\n\n{item['prompt']}\n")
    if not args.id:
        print(data["mcp_hint"])
    return 0


def mcp_config(args):
    path = access_path(args)
    print(f"# {config_hint(args.host)}")
    print(render_mcp_config(args.host, path, args.name))
    return 0


def workspace(args):
    root = Path(args.root).expanduser()
    with httpx.Client(timeout=20) as http:
        resolved = direct(http, args)
        briefing = call(http, resolved, "GET", "briefing")["text"]
        challenges = paged(http, resolved, "challenges")
        tasks = paged(http, resolved, "tasks")
    root.mkdir(parents=True, exist_ok=True)
    (root / "CTF.md").write_text(briefing, encoding="utf-8")
    created = 0
    for challenge in challenges:
        folder = (
            root
            / identity.slugify(challenge.get("category") or "uncategorized")
            / identity.slugify(challenge["title"])
        )
        if not folder.exists():
            created += 1
        folder.mkdir(parents=True, exist_ok=True)
        related = [task for task in tasks if task.get("challenge_id") == challenge["id"]]
        lines = [
            f"# {challenge['title']}",
            "",
            f"- category: {challenge.get('category') or '—'}",
            f"- points: {challenge.get('points') if challenge.get('points') is not None else '—'}",
            f"- challenge_id: {challenge['id']}",
            f"- source: {challenge.get('source_url') or '—'}",
            "",
            "## Tasks",
            "",
            *(
                [f"- {task['title']} — {task['status']} — task_id {task['id']}" for task in related]
                or ["- (none yet)"]
            ),
            "",
            "## Description",
            "",
            challenge.get("description") or "(no description)",
            "",
            "> Generated by team-sync-agent workspace; your own files in this folder are kept.",
            "",
        ]
        (folder / "CHALLENGE.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"workspace: {root} ({len(challenges)} challenges, {created} new folders, CTF.md)")
    return 0


def paged(http, resolved, collection):
    items, offset = [], 0
    while True:
        page = call(
            http,
            resolved,
            "GET",
            f"collections/{collection}",
            params={"offset": offset, "limit": 500},
        )["items"]
        items += page
        if len(page) < 500:
            return items
        offset += len(page)


def ctfd_client(args):
    base = args.ctfd_url.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise identity.IdentityError("invalid_ctfd_url", f"Invalid CTFd URL: {base}", "Fix it.")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    cookies = {}
    if os.environ.get(args.token_env):
        headers["Authorization"] = f"Token {os.environ[args.token_env]}"
    elif os.environ.get(args.session_env):
        cookies["session"] = os.environ[args.session_env]
    return base, httpx.Client(base_url=base, headers=headers, cookies=cookies, timeout=30)


def ctfd_get(ctfd, path):
    response = ctfd.get(path)
    if response.status_code in {401, 403} or "/login" in str(response.url):
        raise identity.IdentityError(
            "ctfd_auth_required",
            f"CTFd refused {path} (HTTP {response.status_code})",
            "Create an API token in CTFd (Settings → Access Tokens) and export CTFD_TOKEN, "
            "or export CTFD_SESSION with your session cookie value.",
        )
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.is_error or not body.get("success", False):
        raise identity.IdentityError(
            "ctfd_error", f"CTFd {path}: HTTP {response.status_code}", "Check the CTFd URL."
        )
    return body["data"]


def ctfd_description(base, detail):
    text = (detail.get("description") or "").strip()
    extra = []
    if detail.get("connection_info"):
        extra.append(f"Connection: {detail['connection_info']}")
    files = [urljoin(base + "/", item.lstrip("/")) for item in detail.get("files") or []]
    if files:
        extra.append("Files:\n" + "\n".join(f"- {item}" for item in files))
    hints = detail.get("hints") or []
    if hints:
        costs = ", ".join(str(hint.get("cost", "?")) for hint in hints)
        extra.append(
            f"Hints on platform: {len(hints)} (cost {costs}). DO NOT unlock without explicit "
            "human approval — see CTF.md."
        )
    tags = [tag.get("value") for tag in detail.get("tags") or [] if isinstance(tag, dict)]
    if tags:
        extra.append("Tags: " + ", ".join(filter(None, tags)))
    if detail.get("solves") is not None:
        extra.append(f"Solves at import: {detail['solves']}")
    return "\n\n".join([text, *extra]).strip()[:100_000]


def import_ctfd(args):
    base, ctfd = ctfd_client(args)
    host = urlsplit(base).hostname
    with ctfd, httpx.Client(timeout=20) as http:
        resolved = None if args.dry_run else direct(http, args)
        listing = ctfd_get(ctfd, "/api/v1/challenges")
        known, open_tasks = {}, set()
        if resolved:
            for challenge in paged(http, resolved, "challenges"):
                known[challenge.get("source_url") or ""] = challenge["id"]
                known[challenge["title"].strip().casefold()] = challenge["id"]
            open_tasks = {
                (task.get("challenge_id"), task["title"].strip().casefold())
                for task in paged(http, resolved, "tasks")
                if task["status"] != "done"
            }
        created = existing = tasks = failed = 0
        for item in listing:
            detail = ctfd_get(ctfd, f"/api/v1/challenges/{item['id']}")
            slug = re.sub(r"[^0-9A-Za-z]+", "-", item["name"]).strip("-")
            payload = {
                "title": item["name"][:200],
                "category": (item.get("category") or None) and item["category"][:100],
                "points": item.get("value"),
                "description": ctfd_description(base, detail),
                "source_url": f"{base}/challenges#{slug}-{item['id']}"[:2048],
            }
            if args.dry_run:
                print(f"would import: [{payload['category']}] {payload['title']}")
                continue
            key = f"ctfd:{host}:{item['id']}"
            challenge_id = known.get(payload["source_url"]) or known.get(
                payload["title"].strip().casefold()
            )
            result, error = (None, None)
            if not challenge_id:
                result, error = command(http, resolved, "challenge.create", payload, key)
            if challenge_id:
                existing += 1
            elif error and error.code == "duplicate_challenge":
                challenge_id = error.extra.get("existing_id")
                existing += 1
            elif error:
                print(f"failed: {item['name']}: {error.code} {error.detail}", file=sys.stderr)
                failed += 1
                continue
            else:
                challenge_id = result["id"]
                created += 1
            title = f"Solve: {item['name']}"[:200]
            if args.no_tasks or not challenge_id or (challenge_id, title.casefold()) in open_tasks:
                continue
            _, task_error = command(
                http,
                resolved,
                "task.create",
                {"title": title, "challenge_id": challenge_id},
                f"{key}:task",
            )
            if not task_error:
                tasks += 1
            elif task_error.code != "duplicate_task":
                print(f"task failed: {item['name']}: {task_error.code}", file=sys.stderr)
    print(
        f"CTFd {host}: {len(listing)} challenges · {created} new · {existing} already present · "
        f"{tasks} tasks created · {failed} failed"
    )
    return 1 if failed else 0


def run_experiment(args):
    argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    if not argv:
        raise identity.IdentityError(
            "command_required", "Nothing to run", "Usage: run --task <id> -- <command> [args]"
        )
    tail = deque(maxlen=max(1, min(args.tail, 400)))
    started = time.monotonic()
    try:
        # No shell: arguments are passed as given; output is streamed and kept for evidence.
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace"
        )
    except OSError as error:
        raise identity.IdentityError(
            "command_failed", f"Cannot start {argv[0]}: {error.strerror}", "Check the command."
        ) from None
    for line in process.stdout:
        sys.stdout.write(line)
        tail.append(line.rstrip("\n"))
    code = process.wait()
    seconds = time.monotonic() - started
    files = []
    for name in args.file:
        path = Path(name)
        if path.is_file():
            data = path.read_bytes()
            files.append((path, hashlib.sha256(data).hexdigest(), len(data)))
    output = "\n".join(tail)
    if len(output) > 6000:
        output = "…" + output[-6000:]
    evidence = [f"$ {shlex.join(argv)}", f"exit={code} · {seconds:.1f}s · cwd={Path.cwd().name}"]
    if output:
        evidence += [f"--- output (last {len(tail)} lines) ---", output]
    if files:
        evidence.append("--- files ---")
        evidence += [f"{digest}  {size:>9}  {path}" for path, digest, size in files]
    kind = args.kind or ("observation" if code == 0 else "failed_attempt")
    title = args.title or f"{argv[0]} {' '.join(argv[1:])}".strip()
    finding = {
        "title": f"{title[:180]} (exit {code})",
        "body": args.note or f"Команда завершилась з кодом {code} за {seconds:.1f} с.",
        "evidence": "\n".join(evidence)[:100_000],
        "kind": kind,
        "based_on": args.based_on,
        "refutes": args.refutes,
    }
    if args.task:
        finding["task_id"] = args.task
    elif args.challenge:
        finding["challenge_id"] = args.challenge
    if args.dry_run:
        print(json.dumps(finding, ensure_ascii=False, indent=2), file=sys.stderr)
        return code
    with httpx.Client(timeout=60) as http:
        resolved = direct(http, args)
        if resolved.token in finding["evidence"]:
            finding["evidence"] = finding["evidence"].replace(resolved.token, "<redacted>")
        created, error = command(
            http, resolved, "finding.create", finding, f"run:{os.getpid()}:{time.time_ns()}"
        )
        if error:
            raise error
        for path, digest, _ in files:
            response = http.post(
                f"{resolved.url}/api/v1/projects/{resolved.project_id}/artifacts",
                params={"name": path.name},
                content=path.read_bytes(),
                headers={
                    "Authorization": f"Bearer {resolved.token}",
                    "Idempotency-Key": f"run:{created['id']}:{digest}",
                    "Content-Type": "application/octet-stream",
                },
            )
            if response.is_error:
                raise identity.remote_problem(response, resolved.url)
            command(
                http,
                resolved,
                "link.create",
                {
                    "source_type": "artifact",
                    "source_id": response.json()["id"],
                    "target_type": "finding",
                    "target_id": created["id"],
                    "relation": "attached_to",
                    "note": "output of team-sync-agent run",
                },
                f"run-attach:{created['id']}:{digest}",
            )
    print(f"team-sync: finding {created['id']} ({kind}, exit {code})", file=sys.stderr)
    return code
