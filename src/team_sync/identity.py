"""Client-side identity resolution shared by the MCP bridge and the CLI.

One running agent process = one Team Sync actor. A human (or invite) access file is a
*seed*: each MCP process takes a free agent identity of that human from a local pool, or
enrolls a new one named ``<Human>-<Callsign>_<4 chars>``. An agent access file that is
already in use by another live process gets a sibling identity instead of being shared.
Locks are advisory OS file locks that disappear when the process exits.
"""

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx

try:  # POSIX advisory locks; on other platforms identities are not shared-checked.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

SERVICE = "ctf-team-sync"


class IdentityError(Exception):
    """A user-actionable problem; code/detail/recovery are safe to show to a model."""

    def __init__(self, code, detail, recovery, status=None, extra=None):
        super().__init__(detail)
        self.code, self.detail, self.recovery, self.status = code, detail, recovery, status
        self.extra = extra or {}

    def as_dict(self):
        return {
            "status": self.status,
            "code": self.code,
            "detail": self.detail,
            "recovery": self.recovery,
            **self.extra,
        }


def state_root():
    configured = os.environ.get("TEAM_SYNC_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base).expanduser() / "team-sync"


def slugify(value):
    slug = re.sub(r"[^0-9A-Za-zЀ-ӿ]+", "-", value).strip("-").lower()
    return slug[:60] or "item"


def write_private(path, payload):
    """Atomically write a 0600 JSON file; never prints its content."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    temporary = path.parent / f".{path.name}.{secrets.token_hex(4)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def validate_url(url):
    url = (url or "").strip().rstrip("/")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or any((parsed.username, parsed.password, parsed.query, parsed.fragment))
    ):
        raise IdentityError(
            "invalid_access_file", "The access file has an invalid server URL", "Fix its url"
        )
    return url


def read_access(path):
    """Read a credential or invite file: {url, project_id, token} or {url, invite}."""
    source = Path(path).expanduser()
    try:
        if source.stat().st_size > 64 * 1024:
            raise ValueError("too large")
        values = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise IdentityError(
            "access_file_missing",
            f"Access file not found: {source}",
            "Set TEAM_SYNC_ACCESS_FILE to your access or invite file (no restart needed "
            "after fixing the file itself).",
        ) from None
    except (OSError, ValueError):
        raise IdentityError(
            "invalid_access_file",
            f"Access file is not valid JSON: {source}",
            "Download the access/invite file again from the Team Sync web UI.",
        ) from None
    if not isinstance(values, dict) or values.get("schema_version", 1) != 1:
        raise IdentityError(
            "invalid_access_file", f"Unsupported access file: {source}", "Download it again."
        )
    url = validate_url(values.get("url"))
    if values.get("invite"):
        return {"kind": "invite", "url": url, "invite": str(values["invite"]).strip()}
    project, token = values.get("project_id"), values.get("token")
    if not (isinstance(project, str) and isinstance(token, str) and token.strip()):
        raise IdentityError(
            "invalid_access_file",
            f"Access file needs url, project_id and token (or invite): {source}",
            "Download it again from the Team Sync web UI.",
        )
    try:
        project = str(UUID(project))
    except ValueError:
        raise IdentityError(
            "invalid_access_file", f"Invalid project_id in {source}", "Download it again."
        ) from None
    return {"kind": "credential", "url": url, "project_id": project, "token": token.strip()}


def env_access():
    url, project, token = (
        os.environ.get(key) for key in ("TEAM_SYNC_URL", "TEAM_SYNC_PROJECT_ID", "TEAM_SYNC_TOKEN")
    )
    if url and project and token:
        return {
            "kind": "credential",
            "url": validate_url(url),
            "project_id": str(UUID(project)),
            "token": token.strip(),
        }
    return None


def check_server(http, url):
    """Distinguish 'nothing listening' from 'another application on this port'."""
    try:
        response = http.get(f"{url}/health", timeout=5)
    except httpx.HTTPError as error:
        raise unreachable(url, error) from None
    try:
        service = response.json().get("service")
    except ValueError:
        service = None
    if response.status_code != 200 or service != SERVICE:
        raise IdentityError(
            "wrong_server",
            f"{url} answers, but it is not a Team Sync server (health: HTTP "
            f"{response.status_code}, service={service!r})",
            "Another application uses this port. Fix url in the access file or start Team "
            "Sync on a free port (TEAM_SYNC_HOST_PORT=8010 docker compose up -d).",
        )


def unreachable(url, error):
    return IdentityError(
        "server_unreachable",
        f"Cannot reach Team Sync at {url} ({type(error).__name__})",
        "Start the server (docker compose up -d in tools/ctf-team-sync) or correct url in "
        "the access file. The bridge re-reads the file on the next call; no restart needed.",
    )


def remote_problem(response, url):
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    return IdentityError(
        body.get("code", "remote_error"),
        str(body.get("detail") or f"HTTP {response.status_code} from {url}"),
        "Check the access file and server.",
        response.status_code,
        {k: v for k, v in body.items() if k not in {"code", "detail"}},
    )


class FileLock:
    """Hold an exclusive advisory lock for the lifetime of this process."""

    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def acquire(self):
        if fcntl is None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+")  # noqa: SIM115 - kept open while the lock is held
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self.handle = handle
        return True

    def release(self):
        if self.handle:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None


@dataclass
class Resolved:
    url: str
    project_id: str
    token: str
    actor: dict
    access_file: str | None
    source: str  # direct | session-agent | sibling-agent | joined
    owner_name: str | None = None
    project_name: str | None = None
    agent_file: str | None = None
    notes: list = field(default_factory=list)

    @property
    def terminal_title(self):
        return f"{self.actor['name']} · {self.project_name or 'Team Sync'}"

    def public(self):
        return {
            "name": self.actor["name"],
            "actor_id": self.actor["id"],
            "kind": self.actor["kind"],
            "role": self.actor["role"],
            "owner_name": self.owner_name,
            "project": {"id": self.project_id, "name": self.project_name},
            "server": self.url,
            "access_file": self.access_file,
            "identity_source": self.source,
            "agent_file": self.agent_file,
            # Same identity for shell commands: team-sync-agent --access-file <this> run ...
            "cli_access_file": self.agent_file or self.access_file,
            "terminal_title": self.terminal_title,
            "set_terminal_title": f"printf '\\033]0;%s\\007' '{self.terminal_title}'",
            "notes": self.notes,
        }


def api(http, url, project_id, token, method, path, **kwargs):
    try:
        response = http.request(
            method,
            f"{url}/api/v1/projects/{project_id}/{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
            **kwargs,
        )
    except httpx.HTTPError as error:
        raise unreachable(url, error) from None
    if response.status_code == 404 and not response.headers.get("content-type", "").startswith(
        "application/json"
    ):
        check_server(http, url)
    if response.is_error:
        problem = remote_problem(response, url)
        if problem.code == "remote_error" and response.status_code == 404:
            check_server(http, url)
        raise problem
    return response.json()


def whoami(http, access):
    return api(http, access["url"], access["project_id"], access["token"], "GET", "me")


def pool_dir(project_id, human_id):
    return state_root() / project_id / "humans" / human_id / "agents"


def actor_lock(actor_id):
    """One lock per actor, however its access file was reached."""
    return FileLock(state_root() / "locks" / f"{actor_id}.lock")


def take_pooled(project_id, human_id, locks):
    """Reuse a stored agent identity of this human that no live process holds."""
    directory = pool_dir(project_id, human_id)
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        try:
            actor_id = json.loads(path.read_text(encoding="utf-8")).get("actor_id")
            access = read_access(path)
        except (OSError, ValueError, IdentityError):
            continue
        if not actor_id:
            continue
        lock = actor_lock(actor_id)
        if lock.acquire():
            locks.append(lock)
            return path, access
    return None, None


def usable_pooled(http, project_id, human_id, locks):
    """Like take_pooled, but retire identities the server no longer accepts (revoked)."""
    while True:
        path, access = take_pooled(project_id, human_id, locks)
        if access is None:
            return None, None
        try:
            whoami(http, access)
            return path, access
        except IdentityError as error:
            if error.status not in {401, 404}:
                raise
            locks.pop().release()
            path.rename(path.with_suffix(".revoked"))


def enroll_agent(http, seed, human_id, capabilities=None, name=None, locks=None):
    body = {"kind": "agent", "role": "worker", "capabilities": capabilities or []}
    if name:
        body["name"] = name
    created = api(http, seed["url"], seed["project_id"], seed["token"], "POST", "actors", json=body)
    actor = created["actor"]
    path = pool_dir(seed["project_id"], human_id) / f"{slugify(actor['name'])}.json"
    lock = actor_lock(actor["id"])
    lock.acquire()
    if locks is not None:
        locks.append(lock)
    write_private(
        path,
        {
            "schema_version": 1,
            "url": seed["url"],
            "project_id": seed["project_id"],
            "token": created["token"],
            "actor_id": actor["id"],
            "name": actor["name"],
        },
    )
    return path, {
        "kind": "credential",
        "url": seed["url"],
        "project_id": seed["project_id"],
        "token": created["token"],
    }


def invite_record_path(invite):
    digest = hashlib.sha256(invite.encode()).hexdigest()[:16]
    return state_root() / "invites" / f"{digest}.json"


def redeem_invite(http, invite_access, human_name, agent_name=None, capabilities=None):
    """Create the teammate and their first agent; remember the human credential locally."""
    url = invite_access["url"]
    try:
        response = http.post(
            f"{url}/api/v1/join",
            headers={"Authorization": f"Bearer {invite_access['invite']}"},
            json={
                "human_name": human_name,
                "agent_name": agent_name,
                "capabilities": capabilities or [],
            },
            timeout=20,
        )
    except httpx.HTTPError as error:
        raise unreachable(url, error) from None
    if response.is_error:
        if response.status_code == 404:
            check_server(http, url)
        raise remote_problem(response, url)
    joined = response.json()
    project_id, human = joined["project"]["id"], joined["human"]
    human_file = write_private(
        state_root() / project_id / "humans" / f"{slugify(human['actor']['name'])}.json",
        {
            "schema_version": 1,
            "url": url,
            "project_id": project_id,
            "token": human["token"],
            "actor_id": human["actor"]["id"],
            "name": human["actor"]["name"],
        },
    )
    write_private(invite_record_path(invite_access["invite"]), {"human_file": str(human_file)})
    agent_file = None
    if joined.get("agent"):
        agent = joined["agent"]
        agent_file = write_private(
            pool_dir(project_id, human["actor"]["id"]) / f"{slugify(agent['actor']['name'])}.json",
            {
                "schema_version": 1,
                "url": url,
                "project_id": project_id,
                "token": agent["token"],
                "actor_id": agent["actor"]["id"],
                "name": agent["actor"]["name"],
            },
        )
    return joined, human_file, agent_file


def seed_from(access_file):
    """Return the seed credential for a file, following a redeemed invite if needed."""
    if access_file is None:
        access = env_access()
        if access is None:
            raise IdentityError(
                "access_file_missing",
                "No Team Sync access configured",
                "Set TEAM_SYNC_ACCESS_FILE to an access or invite file in your MCP config.",
            )
        return access
    access = read_access(access_file)
    if access["kind"] == "invite":
        record = invite_record_path(access["invite"])
        if record.exists():
            human_file = json.loads(record.read_text(encoding="utf-8")).get("human_file")
            if human_file and Path(human_file).exists():
                return read_access(human_file)
        raise IdentityError(
            "join_required",
            "This is a team invite; you have not joined yet",
            "Ask the user for their nickname, then call join_team(human_name=<nickname>). "
            "It creates the person and your own agent identity named after them.",
        )
    return access


def resolve(http, access_file, mode="auto", locks=None, capabilities=None):
    """Pick this process's identity. mode: auto (default) | direct."""
    locks = [] if locks is None else locks
    seed = seed_from(access_file)
    me = whoami(http, seed)
    actor = me["actor"]
    resolved_from = "direct"
    notes = []
    access, agent_file = seed, None
    if mode != "direct":
        if actor["kind"] == "human" and actor["role"] != "observer":
            agent_file, access = usable_pooled(http, seed["project_id"], actor["id"], locks)
            resolved_from = "session-agent"
            if access is None:
                agent_file, access = enroll_agent(
                    http, seed, actor["id"], capabilities, locks=locks
                )
                notes.append("Enrolled a new agent identity for this process.")
        elif actor["kind"] == "agent":
            lock = actor_lock(actor["id"])
            if lock.acquire():
                locks.append(lock)
            elif actor.get("owner_id"):
                agent_file, access = usable_pooled(
                    http, seed["project_id"], actor["owner_id"], locks
                )
                if access is None:
                    agent_file, access = enroll_agent(
                        http, seed, actor["owner_id"], actor.get("capabilities"), locks=locks
                    )
                resolved_from = "sibling-agent"
                notes.append(
                    f"{actor['name']} is already used by another running process; this "
                    "process got its own sibling identity to avoid shared leases."
                )
            else:
                notes.append("Warning: this agent access file is used by another running process.")
        if access is not seed:
            me = whoami(http, access)
    return Resolved(
        url=access["url"],
        project_id=access["project_id"],
        token=access["token"],
        actor=me["actor"],
        access_file=str(access_file) if access_file else None,
        source=resolved_from,
        owner_name=me.get("owner_name"),
        project_name=me["project"]["name"],
        agent_file=str(agent_file) if agent_file else None,
        notes=notes,
    )
