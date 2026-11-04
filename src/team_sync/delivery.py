"""Durable, client-side delivery cache. Authoritative team state stays on the server."""

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

from team_sync.connection import load_connection
from team_sync.schemas import EventEnvelope


class DeliveryError(Exception):
    """A safe, credential-free operational error."""


class RemoteError(DeliveryError):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(f"Team Sync request failed (HTTP {status}, {code})")


@dataclass(frozen=True)
class AgentSettings:
    url: str
    project_id: str
    token: str = field(repr=False)
    state_path: Path

    @classmethod
    def from_env(cls, state_path=None, access_file=None):
        connection = load_connection(access_file)
        url = connection["url"]
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise DeliveryError("TEAM_SYNC_URL must be an HTTP(S) URL without credentials or query")
        project = str(UUID(connection["project_id"]))
        token = connection["token"]
        if not token:
            raise DeliveryError("TEAM_SYNC_TOKEN must not be empty")
        fingerprint = hashlib.sha256(json.dumps([url, project, token]).encode()).hexdigest()
        if state_path is None:
            base = os.getenv("LOCALAPPDATA") if os.name == "nt" else os.getenv("XDG_STATE_HOME")
            directory = Path(base) if base else Path.home() / ".local" / "state"
            state_path = directory / "ctf-team-sync" / f"{fingerprint[:32]}.sqlite3"
        return cls(url, project, token, Path(state_path).expanduser().resolve())

    def binding(self):
        return {
            "url": self.url,
            "project_id": self.project_id,
            "credential_hash": hashlib.sha256(self.token.encode()).hexdigest(),
        }


class Spool:
    def __init__(self, settings: AgentSettings, *, lock_timeout=5):
        self.path = settings.state_path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self.db = sqlite3.connect(self.path, timeout=lock_timeout, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        if self.db.execute("PRAGMA user_version").fetchone()[0] > 2:
            self.db.close()
            raise DeliveryError("Delivery cache schema requires a newer adapter")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,
                envelope TEXT NOT NULL, context TEXT, message_id TEXT
            );
            CREATE TABLE IF NOT EXISTS pending_receipts (
                message_id TEXT PRIMARY KEY, command_key TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS consumers (
                name TEXT PRIMARY KEY, emitted_cursor INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS delivery_batches (
                id TEXT PRIMARY KEY, consumer TEXT NOT NULL,
                after_cursor INTEGER NOT NULL, through_cursor INTEGER NOT NULL,
                payload TEXT NOT NULL, acked INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_pending_batch
                ON delivery_batches(consumer) WHERE acked=0;
            CREATE TABLE IF NOT EXISTS hook_messages (
                consumer TEXT NOT NULL, message_id TEXT NOT NULL,
                PRIMARY KEY(consumer, message_id)
            );
        """)
        with self.transaction():
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(pending_receipts)")}
            if "message_version" not in columns:
                self.db.execute(
                    "ALTER TABLE pending_receipts ADD COLUMN message_version INTEGER "
                    "NOT NULL DEFAULT 1"
                )
            self.db.execute("PRAGMA user_version=2")
        expected = json.dumps(settings.binding(), sort_keys=True)
        try:
            with self.transaction():
                previous = self.get_meta("binding")
                if previous is not None and previous != expected:
                    raise DeliveryError(
                        "Delivery cache belongs to another server, project, or identity"
                    )
                self.set_meta("binding", expected)
                if self.get_meta("cursor") is None:
                    self.set_meta("cursor", "0")
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def get_meta(self, key):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)", (key, value))

    @property
    def cursor(self):
        return int(self.get_meta("cursor"))

    def bind_actor(self, actor_id):
        with self.transaction():
            previous = self.get_meta("actor_id")
            if previous and previous != actor_id:
                raise DeliveryError("Delivery cache actor does not match the remote identity")
            self.set_meta("actor_id", actor_id)

    def ingest(self, events, contexts, receipts, actor_id, processed=(), attention=None):
        with self.transaction():
            if attention is not None:
                attention = {**attention, "cached_at": datetime.now(UTC).isoformat()}
                self.set_meta("attention", json.dumps(attention))
            cursor = self.cursor
            for event in events:
                if event["seq"] <= cursor:
                    continue
                if event["seq"] != cursor + 1:
                    raise DeliveryError("Event sequence has a gap; cursor was not advanced")
                if event["type"] == "message.update":
                    message_id = event["payload"]["resource_id"]
                    self.db.execute(
                        "UPDATE events SET context=NULL WHERE message_id=?", (message_id,)
                    )
                    for batch in self.db.execute(
                        "SELECT id,payload FROM delivery_batches WHERE acked=0"
                    ).fetchall():
                        if any(
                            item.get("message", {}).get("id") == message_id
                            for item in json.loads(batch["payload"])["items"]
                        ):
                            self.db.execute(
                                "DELETE FROM delivery_batches WHERE id=?", (batch["id"],)
                            )
                self.db.execute(
                    "INSERT INTO events(seq,id,envelope,context,message_id) VALUES (?,?,?,?,?)",
                    (
                        event["seq"],
                        event["id"],
                        json.dumps(event),
                        json.dumps(contexts[event["seq"]]) if event["seq"] in contexts else None,
                        contexts.get(event["seq"], {}).get("message", {}).get("id"),
                    ),
                )
                cursor = event["seq"]
            for message in receipts:
                message_id = message["id"] if isinstance(message, dict) else message
                version = message.get("version", 1) if isinstance(message, dict) else 1
                key = f"received:{actor_id}:{message_id}"
                if version > 1:
                    key += f":v{version}"
                self.db.execute(
                    "INSERT INTO pending_receipts(message_id,command_key,message_version) "
                    "VALUES (?,?,?) ON CONFLICT(message_id) DO UPDATE SET "
                    "command_key=excluded.command_key,message_version=excluded.message_version "
                    "WHERE excluded.message_version>pending_receipts.message_version",
                    (message_id, key, version),
                )
            processed_versions = {}
            for entry in processed:
                message_id = entry["id"] if isinstance(entry, dict) else entry
                version = entry.get("version", 1) if isinstance(entry, dict) else 1
                processed_versions[message_id] = max(version, processed_versions.get(message_id, 0))
                for row in self.db.execute(
                    "SELECT seq,context FROM events WHERE message_id=? AND context IS NOT NULL",
                    (message_id,),
                ).fetchall():
                    if json.loads(row["context"])["message"].get("version", 1) <= version:
                        self.db.execute("UPDATE events SET context=NULL WHERE seq=?", (row["seq"],))
                self.db.execute(
                    "DELETE FROM pending_receipts WHERE message_id=? AND message_version<=?",
                    (message_id, version),
                )
            if processed:
                for batch in self.db.execute(
                    "SELECT id,payload FROM delivery_batches WHERE acked=0"
                ).fetchall():
                    if any(
                        item.get("message", {}).get("id") in processed_versions
                        and item["message"].get("version", 1)
                        <= processed_versions[item["message"]["id"]]
                        for item in json.loads(batch["payload"])["items"]
                    ):
                        self.db.execute("DELETE FROM delivery_batches WHERE id=?", (batch["id"],))
            self.set_meta("cursor", str(cursor))

    def pending_receipts(self):
        return self.db.execute("SELECT * FROM pending_receipts ORDER BY message_id").fetchall()

    def receipt_done(self, message_id):
        self.db.execute("DELETE FROM pending_receipts WHERE message_id=?", (message_id,))

    def context_batch(self, consumer, limit=12, max_chars=6000):
        if self.get_meta("blocked"):
            return [], 0
        previous = self.db.execute(
            "SELECT emitted_cursor FROM consumers WHERE name=?", (consumer,)
        ).fetchone()
        after = previous[0] if previous else 0
        rows = self.db.execute(
            "SELECT seq,context FROM events WHERE seq>? AND context IS NOT NULL "
            "ORDER BY seq LIMIT ?",
            (after, limit),
        ).fetchall()
        contexts, emitted = [], after
        for row in rows:
            context = json.loads(row["context"])
            if context.get("kind") == "message":
                body = context["message"]["body"]
                context["message"]["body"] = body[:2000]
                context["message"]["body_truncated"] = len(body) > 2000
            candidate = contexts + [context]
            if len(json.dumps(candidate, ensure_ascii=False)) > max_chars:
                if contexts:
                    break
                if context.get("kind") != "message":
                    raise DeliveryError("A cached event exceeds the context output budget")
                while len(json.dumps([context], ensure_ascii=False)) > max_chars:
                    body = context["message"]["body"]
                    if not body:
                        raise DeliveryError("Message metadata exceeds the context output budget")
                    context["message"]["body"] = body[: len(body) // 2]
                    context["message"]["body_truncated"] = True
                candidate = [context]
            contexts, emitted = candidate, row["seq"]
        return contexts, emitted

    def emitted(self, consumer, cursor):
        self.db.execute(
            """
            INSERT INTO consumers(name,emitted_cursor) VALUES (?,?)
            ON CONFLICT(name) DO UPDATE SET
                emitted_cursor=MAX(emitted_cursor,excluded.emitted_cursor)
        """,
            (consumer, cursor),
        )

    def hook_batch(self, consumer, limit=12, max_chars=6000):
        """Prioritize the latest attention snapshot without reordering journal cursors."""
        snapshot = self.get_meta("attention")
        if not snapshot:
            items, cursor = self.context_batch(consumer, limit, max_chars)
            return items, cursor, []
        if self.get_meta("blocked"):
            return [], 0, []
        seen = {
            row[0]
            for row in self.db.execute(
                "SELECT message_id FROM hook_messages WHERE consumer=?", (consumer,)
            )
        }
        contexts, ids = [], []
        snapshot = json.loads(snapshot)
        timestamp = datetime.fromisoformat(snapshot["server_time"]) + (
            datetime.now(UTC) - datetime.fromisoformat(snapshot["cached_at"])
        )
        for item in snapshot["items"]:
            message_key = (
                item["id"] if item.get("version", 1) == 1 else f"{item['id']}:v{item['version']}"
            )
            if message_key in seen or (
                item.get("expires_at") and datetime.fromisoformat(item["expires_at"]) <= timestamp
            ):
                continue
            message = {
                **item,
                "body": item["body"][:2000],
                "body_truncated": item.get("body_truncated", False) or len(item["body"]) > 2000,
            }
            context = {"kind": "message", "source": "attention", "message": message}
            while len(json.dumps(contexts + [context], ensure_ascii=False)) > max_chars:
                if len(message["body"]) < 40:
                    break
                message["body"] = message["body"][: len(message["body"]) // 2]
                message["body_truncated"] = True
            if len(json.dumps(contexts + [context], ensure_ascii=False)) > max_chars:
                break
            contexts.append(context)
            ids.append(message_key)
            if len(contexts) >= limit:
                break
        row = self.db.execute(
            "SELECT emitted_cursor FROM consumers WHERE name=?", (consumer,)
        ).fetchone()
        cursor = row[0] if row else 0
        for row in self.db.execute(
            "SELECT seq,context FROM events WHERE seq>? AND context IS NOT NULL "
            "AND message_id IS NULL ORDER BY seq LIMIT ?",
            (cursor, limit),
        ).fetchall():
            context = json.loads(row["context"])
            if (
                len(contexts) >= limit
                or len(json.dumps(contexts + [context], ensure_ascii=False)) > max_chars
            ):
                break
            contexts.append(context)
            cursor = row["seq"]
        return contexts, cursor, ids

    def hook_emitted(self, consumer, cursor, message_ids):
        with self.transaction():
            self.emitted(consumer, cursor)
            self.db.executemany(
                "INSERT OR IGNORE INTO hook_messages VALUES (?,?)",
                [(consumer, ident) for ident in message_ids],
            )

    def replay(self, consumer):
        with self.transaction():
            self.db.execute("DELETE FROM consumers WHERE name=?", (consumer,))
            self.db.execute("DELETE FROM hook_messages WHERE consumer=?", (consumer,))
            self.db.execute(
                "DELETE FROM delivery_batches WHERE consumer=? AND acked=0", (consumer,)
            )

    def read_context(self, consumer, *, limit=12, max_chars=6000):
        with self.transaction():
            binding = json.loads(self.get_meta("binding"))
            previous = self.db.execute(
                "SELECT emitted_cursor FROM consumers WHERE name=?", (consumer,)
            ).fetchone()
            after = previous[0] if previous else 0
            empty = {
                "schema_version": 1,
                "project_id": binding["project_id"],
                "actor_id": self.get_meta("actor_id"),
                "consumer": consumer,
                "delivery_id": None,
                "after_cursor": after,
                "through_cursor": after,
                "items": [],
            }
            if self.get_meta("blocked"):
                return empty
            pending = self.db.execute(
                "SELECT payload FROM delivery_batches WHERE consumer=? AND acked=0", (consumer,)
            ).fetchone()
            if pending:
                return json.loads(pending[0])
            items, through = self.context_batch(consumer, limit=limit, max_chars=max_chars)
            if not items:
                return empty
            payload = {
                **empty,
                "delivery_id": str(uuid4()),
                "through_cursor": through,
                "items": items,
            }
            self.db.execute(
                "INSERT INTO delivery_batches(id,consumer,after_cursor,through_cursor,payload) "
                "VALUES (?,?,?,?,?)",
                (payload["delivery_id"], consumer, after, through, json.dumps(payload)),
            )
            return payload

    def ack_context(self, consumer, delivery_id):
        with self.transaction():
            if self.get_meta("blocked"):
                raise DeliveryError(
                    "Context acknowledgement is unavailable after access revocation"
                )
            row = self.db.execute(
                "SELECT * FROM delivery_batches WHERE id=? AND consumer=?", (delivery_id, consumer)
            ).fetchone()
            if row is None:
                raise DeliveryError("Unknown, invalidated, or mismatched context delivery")
            if not row["acked"]:
                self.emitted(consumer, row["through_cursor"])
                self.db.execute(
                    "UPDATE delivery_batches SET acked=1,payload='{}' WHERE id=?", (delivery_id,)
                )
            return {
                "delivery_id": delivery_id,
                "consumer": consumer,
                "through_cursor": row["through_cursor"],
                "already_acked": bool(row["acked"]),
            }

    def status(self):
        return {
            "cursor": self.cursor,
            "actor_id": self.get_meta("actor_id"),
            "events_cached": self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "contexts_cached": self.db.execute(
                "SELECT COUNT(*) FROM events WHERE context IS NOT NULL"
            ).fetchone()[0],
            "receipts_pending": len(self.pending_receipts()),
            "deliveries_pending": self.db.execute(
                "SELECT COUNT(*) FROM delivery_batches WHERE acked=0"
            ).fetchone()[0],
            "blocked": self.get_meta("blocked") or None,
        }


CONTEXT_EVENTS = {
    "task.create",
    "task.claim",
    "task.complete",
    "task.release",
    "task.assign",
    "artifact.review.request",
    "artifact.review.decide",
    "artifact.review.start",
    "artifact.review.complete",
    "artifact.archive",
    "finding.create",
    "flag.create",
    "flag.verify",
    "submission.create",
    "artifact.published",
    "challenge.create",
    "project.update",
    "actor.update",
    "challenge.update",
    "task.update",
    "finding.update",
    "flag.update",
    "verification.update",
    "submission.update",
    "artifact.update",
}


class Receiver:
    def __init__(self, settings: AgentSettings, spool: Spool, *, transport=None):
        self.settings, self.spool, self.actor_id = settings, spool, None
        self.client = httpx.Client(
            base_url=f"{settings.url}/api/v1/projects/{settings.project_id}/",
            headers={"Authorization": f"Bearer {settings.token}"},
            timeout=30,
            transport=transport,
        )

    def close(self):
        self.client.close()

    def request(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        if response.is_error:
            try:
                code = response.json().get("code", "request_failed")
            except (ValueError, AttributeError):
                code = "request_failed"
            if not isinstance(code, str) or not re.fullmatch(r"[a-z_]{1,64}", code):
                code = "request_failed"
            if response.status_code == 401:
                self.spool.set_meta("blocked", "credential_revoked")
            # Do not echo response bodies, credentials, or request URLs into hook logs.
            raise RemoteError(response.status_code, code)
        return response.json()

    def initialize(self):
        snapshot = self.request("GET", "snapshot")
        if snapshot["project"]["id"] != self.settings.project_id:
            raise DeliveryError("Remote project identity does not match configuration")
        self.actor_id = snapshot["actor"]["id"]
        self.spool.bind_actor(self.actor_id)
        self.spool.set_meta("blocked", "")

    def flush_receipts(self):
        for receipt in self.spool.pending_receipts():
            arguments = {"stage": "received"}
            if receipt["message_version"] > 1:
                arguments["expected_version"] = receipt["message_version"]
            try:
                self.request(
                    "POST",
                    "commands",
                    json={
                        "operation": "message.ack",
                        "resource_id": receipt["message_id"],
                        "arguments": arguments,
                        "idempotency_key": receipt["command_key"],
                    },
                )
            except RemoteError as error:
                if error.code not in {
                    "receipt_regression",
                    "version_conflict",
                    "message_version_required",
                }:
                    raise
            self.spool.receipt_done(receipt["message_id"])

    def poll(self, timeout=25):
        if self.actor_id is None:
            self.initialize()
        self.flush_receipts()
        after = self.spool.cursor
        batch = self.request("GET", "events/wait", params={"after": after, "timeout": timeout})
        try:
            attention = self.request("GET", "attention", params={"limit": 50})
        except RemoteError as error:
            if error.status != 404:
                raise
            attention = None  # Older servers retain their FIFO delivery behavior.
        contexts, receipts, events, processed = {}, [], [], []
        for raw in batch["items"]:
            try:
                event = EventEnvelope.model_validate(raw).model_dump(mode="json")
            except ValueError as error:
                raise DeliveryError(
                    "Unsupported or invalid event schema; update the adapter"
                ) from error
            if event["project_id"] != self.settings.project_id:
                raise DeliveryError("Event project does not match this receiver")
            events.append(event)
            if (
                event["type"] == "message.ack"
                and event["actor_id"] == self.actor_id
                and event["payload"].get("stage") == "processed"
            ):
                processed.append(
                    {
                        "id": event["payload"]["resource_id"],
                        "version": event["payload"].get("message_version", 1),
                    }
                )
            if event["type"] in {"message.send", "message.update"}:
                message_id = event["payload"]["resource_id"]
                try:
                    message = self.request("GET", f"messages/{message_id}")
                except RemoteError as error:
                    if error.status == 404:
                        continue
                    raise
                if message["ack_stage"] == "processed":
                    continue
                if message["sender_id"] == self.actor_id and message["recipient_id"] is None:
                    continue
                contexts[event["seq"]] = {
                    "kind": "message",
                    "seq": event["seq"],
                    "message": message,
                }
                if message["ack_stage"] is None:
                    receipts.append({"id": message_id, "version": message.get("version", 1)})
            elif event["type"] in CONTEXT_EVENTS and event["actor_id"] != self.actor_id:
                contexts[event["seq"]] = {"kind": "change", "event": event}
        if events:
            if batch["next_cursor"] != events[-1]["seq"]:
                raise DeliveryError("Event batch cursor does not match its final event")
        elif batch["next_cursor"] != after:
            raise DeliveryError("An empty event batch cannot advance the cursor")
        if attention:
            attention["items"] = [
                item
                for item in attention["items"]
                if not (item["sender_id"] == self.actor_id and item["recipient_id"] is None)
            ]
            receipts.extend(
                {"id": item["id"], "version": item.get("version", 1)}
                for item in attention["items"]
                if not item["ack_stage"]
            )
        self.spool.ingest(events, contexts, receipts, self.actor_id, processed, attention)
        self.flush_receipts()
        return len(events)
