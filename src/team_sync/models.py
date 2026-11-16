from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id():
    return str(uuid4())


def now():
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    profile: Mapped[str] = mapped_column(String(32))
    capabilities: Mapped[dict] = mapped_column(JSON)
    rules: Mapped[str] = mapped_column(Text, default="")
    event_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    briefing: Mapped[str] = mapped_column(Text, default="", server_default="")


class Scoped:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def scoped_constraints(table, *extra):
    return (
        UniqueConstraint("project_id", "id"),
        ForeignKeyConstraint(["project_id"], ["projects.id"]),
        *extra,
    )


def reference(column, table):
    return ForeignKeyConstraint(["project_id", column], [f"{table}.project_id", f"{table}.id"])


class Actor(Scoped, Base):
    __tablename__ = "actors"
    __table_args__ = scoped_constraints(
        "actors",
        reference("owner_id", "actors"),
        CheckConstraint("role IN ('owner','coordinator','worker','observer')"),
        CheckConstraint("kind IN ('human','agent')"),
    )
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    role: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    owner_id: Mapped[str | None] = mapped_column(String(36))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")


class Challenge(Scoped, Base):
    __tablename__ = "challenges"
    __table_args__ = scoped_constraints("challenges")
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str | None] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    points: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    writeup: Mapped[str] = mapped_column(Text, default="", server_default="")


class Task(Scoped, Base):
    __tablename__ = "tasks"
    __table_args__ = scoped_constraints(
        "tasks",
        reference("challenge_id", "challenges"),
        reference("assignee_id", "actors"),
        reference("created_by_id", "actors"),
        reference("requested_assignee_id", "actors"),
        reference("artifact_id", "artifacts"),
        reference("approved_by_id", "actors"),
        reference("parent_id", "tasks"),
        CheckConstraint("status IN ('open','running','review','done')", name="task_status"),
        CheckConstraint("kind IN ('work','artifact_review','question')", name="task_kind"),
        CheckConstraint("priority IN ('low','normal','urgent')", name="task_priority"),
        CheckConstraint(
            "approval_status IN ('not_required','pending','approved','declined')",
            name="task_approval",
        ),
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    challenge_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), default="open")
    assignee_id: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    result: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[str | None] = mapped_column(String(36))
    requested_assignee_id: Mapped[str | None] = mapped_column(String(36))
    artifact_id: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(24), default="work", server_default="work")
    approval_status: Mapped[str] = mapped_column(
        String(24), default="not_required", server_default="not_required"
    )
    approved_by_id: Mapped[str | None] = mapped_column(String(36))
    approval_note: Mapped[str | None] = mapped_column(Text)
    analysis_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[str] = mapped_column(String(16), default="normal", server_default="normal")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Questions to expert agents: the skills they need and the work they came from.
    tags: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    parent_id: Mapped[str | None] = mapped_column(String(36))


class Log(Scoped, Base):
    __tablename__ = "logs"
    __table_args__ = scoped_constraints(
        "logs",
        reference("author_id", "actors"),
        reference("task_id", "tasks"),
        reference("challenge_id", "challenges"),
        CheckConstraint("level IN ('debug','info','warning','error')", name="log_level"),
    )
    author_id: Mapped[str] = mapped_column(String(36))
    task_id: Mapped[str | None] = mapped_column(String(36))
    challenge_id: Mapped[str | None] = mapped_column(String(36))
    level: Mapped[str] = mapped_column(String(16), default="info")
    body: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class Finding(Scoped, Base):
    __tablename__ = "findings"
    __table_args__ = scoped_constraints(
        "findings",
        reference("challenge_id", "challenges"),
        reference("task_id", "tasks"),
        reference("author_id", "actors"),
        reference("artifact_id", "artifacts"),
        CheckConstraint(
            "status IS NULL OR status IN ('open','confirmed','refuted')", name="finding_status"
        ),
    )
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(24))
    evidence: Mapped[str] = mapped_column(Text)
    challenge_id: Mapped[str | None] = mapped_column(String(36))
    task_id: Mapped[str | None] = mapped_column(String(36))
    author_id: Mapped[str] = mapped_column(String(36))
    artifact_id: Mapped[str | None] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # Hypothesis lifecycle: open -> confirmed | refuted. Other kinds keep NULL.
    status: Mapped[str | None] = mapped_column(String(16))


class Flag(Scoped, Base):
    __tablename__ = "flags"
    __table_args__ = scoped_constraints(
        "flags",
        reference("challenge_id", "challenges"),
        reference("author_id", "actors"),
        reference("task_id", "tasks"),
        UniqueConstraint("project_id", "scope_hash"),
        CheckConstraint("verification_status IN ('unverified','confirmed','rejected')"),
    )
    value: Mapped[str] = mapped_column(Text)
    scope_hash: Mapped[str] = mapped_column(String(64))
    challenge_id: Mapped[str | None] = mapped_column(String(36))
    author_id: Mapped[str] = mapped_column(String(36))
    target: Mapped[str | None] = mapped_column(String(200))
    round: Mapped[str | None] = mapped_column(String(100))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_status: Mapped[str] = mapped_column(String(16), default="unverified")
    verification: Mapped[dict | None] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    task_id: Mapped[str | None] = mapped_column(String(36))


class Verification(Scoped, Base):
    __tablename__ = "verifications"
    __table_args__ = scoped_constraints(
        "verifications",
        reference("flag_id", "flags"),
        reference("author_id", "actors"),
        CheckConstraint("status IN ('confirmed','rejected')"),
        CheckConstraint("method IN ('manual','local_checker','platform')"),
    )
    flag_id: Mapped[str] = mapped_column(String(36))
    author_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16))
    method: Mapped[str] = mapped_column(String(24))
    evidence: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    subject: Mapped[dict | None] = mapped_column(JSON)


class Submission(Scoped, Base):
    __tablename__ = "submissions"
    __table_args__ = scoped_constraints(
        "submissions",
        reference("flag_id", "flags"),
        reference("author_id", "actors"),
        CheckConstraint("outcome IN ('accepted','rejected','unknown')"),
    )
    flag_id: Mapped[str] = mapped_column(String(36))
    author_id: Mapped[str] = mapped_column(String(36))
    outcome: Mapped[str] = mapped_column(String(16))
    evidence: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    subject: Mapped[dict | None] = mapped_column(JSON)


class Message(Scoped, Base):
    __tablename__ = "messages"
    __table_args__ = scoped_constraints(
        "messages",
        reference("sender_id", "actors"),
        reference("recipient_id", "actors"),
        reference("task_id", "tasks"),
        reference("reply_to_id", "messages"),
        CheckConstraint(
            "kind IN ('note','question','assignment','status','approval','result')",
            name="message_kind",
        ),
        CheckConstraint("priority IN ('normal','urgent')", name="message_priority"),
    )
    sender_id: Mapped[str] = mapped_column(String(36))
    recipient_id: Mapped[str | None] = mapped_column(String(36))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(24), default="note", server_default="note")
    priority: Mapped[str] = mapped_column(String(16), default="normal", server_default="normal")
    task_id: Mapped[str | None] = mapped_column(String(36))
    group_key: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_to_id: Mapped[str | None] = mapped_column(String(36))
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class Receipt(Base):
    __tablename__ = "message_receipts"
    __table_args__ = (
        reference("message_id", "messages"),
        reference("actor_id", "actors"),
        CheckConstraint("stage IN ('received','processed')"),
    )
    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    stage: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    message_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class Artifact(Scoped, Base):
    __tablename__ = "artifacts"
    __table_args__ = scoped_constraints(
        "artifacts",
        reference("author_id", "actors"),
        reference("archived_by_id", "actors"),
        reference("supersedes_id", "artifacts"),
        UniqueConstraint("project_id", "supersedes_id"),
    )
    name: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    author_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    media_type: Mapped[str] = mapped_column(
        String(120), default="application/octet-stream", server_default="application/octet-stream"
    )
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    archive_reason: Mapped[str | None] = mapped_column(Text)
    archived_by_id: Mapped[str | None] = mapped_column(String(36))
    supersedes_id: Mapped[str | None] = mapped_column(String(36))


class Activity(Scoped, Base):
    __tablename__ = "activities"
    __table_args__ = scoped_constraints(
        "activities",
        reference("actor_id", "actors"),
        reference("task_id", "tasks"),
        UniqueConstraint("project_id", "actor_id"),
        CheckConstraint(
            "state IN ('idle','working','waiting_human','blocked','ready')", name="activity_state"
        ),
    )
    actor_id: Mapped[str] = mapped_column(String(36))
    task_id: Mapped[str | None] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(24))
    detail: Mapped[str] = mapped_column(String(300), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class TaskParticipant(Base):
    """Cooperative work on a task without taking its single lease from the assignee."""

    __tablename__ = "task_participants"
    __table_args__ = (
        reference("task_id", "tasks"),
        reference("actor_id", "actors"),
        reference("added_by_id", "actors"),
    )
    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    added_by_id: Mapped[str] = mapped_column(String(36))
    note: Mapped[str] = mapped_column(String(300), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


LINK_NODES = {
    "challenge": "challenges",
    "task": "tasks",
    "finding": "findings",
    "flag": "flags",
    "artifact": "artifacts",
}
LINK_RELATIONS = ("attached_to", "leads_to", "supports", "refutes", "supersedes")


class Link(Scoped, Base):
    """A typed edge of the team's knowledge graph (hypothesis chains, attachments)."""

    __tablename__ = "links"
    __table_args__ = scoped_constraints(
        "links",
        reference("author_id", "actors"),
        *(
            reference(f"{side}_{node}_id", table)
            for side in ("source", "target")
            for node, table in LINK_NODES.items()
        ),
        *(
            CheckConstraint(
                f"{side}_type != '{node}' OR {side}_{node}_id = {side}_id",
                name=f"link_{side}_{node}",
            )
            for side in ("source", "target")
            for node in LINK_NODES
        ),
        CheckConstraint("relation IN ('" + "','".join(LINK_RELATIONS) + "')", name="link_relation"),
        CheckConstraint("source_id != target_id", name="link_not_self"),
        UniqueConstraint("project_id", "source_id", "target_id", "relation"),
    )
    author_id: Mapped[str] = mapped_column(String(36))
    relation: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(String(500), default="", server_default="")
    source_type: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[str] = mapped_column(String(36))
    target_type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[str] = mapped_column(String(36))
    source_challenge_id: Mapped[str | None] = mapped_column(String(36))
    source_task_id: Mapped[str | None] = mapped_column(String(36))
    source_finding_id: Mapped[str | None] = mapped_column(String(36))
    source_flag_id: Mapped[str | None] = mapped_column(String(36))
    source_artifact_id: Mapped[str | None] = mapped_column(String(36))
    target_challenge_id: Mapped[str | None] = mapped_column(String(36))
    target_task_id: Mapped[str | None] = mapped_column(String(36))
    target_finding_id: Mapped[str | None] = mapped_column(String(36))
    target_flag_id: Mapped[str | None] = mapped_column(String(36))
    target_artifact_id: Mapped[str | None] = mapped_column(String(36))


class HintRequest(Scoped, Base):
    """A paid platform hint needs a human decision before anyone unlocks it."""

    __tablename__ = "hint_requests"
    __table_args__ = scoped_constraints(
        "hint_requests",
        reference("challenge_id", "challenges"),
        reference("task_id", "tasks"),
        reference("requester_id", "actors"),
        reference("decided_by_id", "actors"),
        reference("finding_id", "findings"),
        CheckConstraint("status IN ('pending','approved','declined','used')", name="hint_status"),
    )
    challenge_id: Mapped[str] = mapped_column(String(36))
    task_id: Mapped[str | None] = mapped_column(String(36))
    requester_id: Mapped[str] = mapped_column(String(36))
    hint_ref: Mapped[str] = mapped_column(String(200), default="", server_default="")
    cost: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    decided_by_id: Mapped[str | None] = mapped_column(String(36))
    decision_note: Mapped[str] = mapped_column(Text, default="", server_default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finding_id: Mapped[str | None] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class Invite(Scoped, Base):
    """A narrow, expiring credential that can only create a teammate and their agent."""

    __tablename__ = "invites"
    __table_args__ = scoped_constraints(
        "invites",
        reference("created_by_id", "actors"),
        CheckConstraint("role IN ('coordinator','worker','observer')", name="invite_role"),
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by_id: Mapped[str] = mapped_column(String(36))
    label: Mapped[str] = mapped_column(String(200), default="", server_default="")
    role: Mapped[str] = mapped_column(String(16), default="worker", server_default="worker")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    max_uses: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    uses: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


REVISION_TARGETS = {
    "actors": "target_actor_id",
    "challenges": "challenge_id",
    "tasks": "task_id",
    "findings": "finding_id",
    "flags": "flag_id",
    "verifications": "verification_id",
    "submissions": "submission_id",
    "messages": "message_id",
    "artifacts": "artifact_id",
    "activities": "activity_id",
}


class Revision(Scoped, Base):
    __tablename__ = "resource_revisions"
    __table_args__ = scoped_constraints(
        "resource_revisions",
        reference("editor_id", "actors"),
        *(reference(column, table) for table, column in REVISION_TARGETS.items()),
        *(
            CheckConstraint(
                f"collection != '{table}' OR ({column} IS NOT NULL AND {column}=resource_id)",
                name=f"revision_{table}_target",
            )
            for table, column in REVISION_TARGETS.items()
        ),
        CheckConstraint(
            "collection != 'projects' OR resource_id=project_id", name="revision_project_target"
        ),
        CheckConstraint(
            "collection IN ('projects','actors','challenges','tasks','findings',"
            "'flags','verifications','submissions','messages','artifacts','activities')",
            name="revision_collection",
        ),
        Index("ix_revisions_resource", "project_id", "collection", "resource_id", "created_at"),
    )
    collection: Mapped[str] = mapped_column(String(24))
    resource_id: Mapped[str] = mapped_column(String(36))
    editor_id: Mapped[str] = mapped_column(String(36))
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text, default="")
    target_actor_id: Mapped[str | None] = mapped_column(String(36))
    challenge_id: Mapped[str | None] = mapped_column(String(36))
    task_id: Mapped[str | None] = mapped_column(String(36))
    finding_id: Mapped[str | None] = mapped_column(String(36))
    flag_id: Mapped[str | None] = mapped_column(String(36))
    verification_id: Mapped[str | None] = mapped_column(String(36))
    submission_id: Mapped[str | None] = mapped_column(String(36))
    message_id: Mapped[str | None] = mapped_column(String(36))
    artifact_id: Mapped[str | None] = mapped_column(String(36))
    activity_id: Mapped[str | None] = mapped_column(String(36))


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        reference("actor_id", "actors"),
        Index("ix_events_actor_time", "project_id", "actor_id", "created_at"),
    )
    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    id: Mapped[str] = mapped_column(String(36), unique=True, default=new_id)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    type: Mapped[str] = mapped_column(String(100))
    actor_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Command(Base):
    __tablename__ = "commands"
    __table_args__ = (reference("actor_id", "actors"),)
    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)


RUN_STATES = ("requested", "approved", "declined", "running", "finished", "failed", "cancelled")
ACTIVE_RUN_STATES = ("requested", "approved", "running")


class Runner(Scoped, Base):
    """A machine of a human that can start agents from profiles defined locally on it.

    The server never stores commands: a runner advertises profile names and limits, and
    can only be asked to start one of them.
    """

    __tablename__ = "runners"
    __table_args__ = scoped_constraints(
        "runners",
        reference("owner_id", "actors"),
        UniqueConstraint("project_id", "owner_id", "name", name="runner_name"),
    )
    owner_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(100))
    profiles: Mapped[list] = mapped_column(JSON, default=list)
    info: Mapped[dict] = mapped_column(JSON, default=dict)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class Run(Scoped, Base):
    """One start of a runner profile; another participant's request needs the runner owner."""

    __tablename__ = "runs"
    __table_args__ = scoped_constraints(
        "runs",
        reference("runner_id", "runners"),
        reference("task_id", "tasks"),
        reference("requested_by_id", "actors"),
        reference("decided_by_id", "actors"),
        CheckConstraint("status IN ('" + "','".join(RUN_STATES) + "')", name="run_status"),
        CheckConstraint(
            "purpose IN ('task','question','coordinator','manual')", name="run_purpose"
        ),
        Index("ix_runs_active", "project_id", "status"),
    )
    runner_id: Mapped[str] = mapped_column(String(36))
    profile: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(16), default="manual")
    task_id: Mapped[str | None] = mapped_column(String(36))
    requested_by_id: Mapped[str] = mapped_column(String(36))
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(16), default="requested")
    decided_by_id: Mapped[str | None] = mapped_column(String(36))
    decision_note: Mapped[str] = mapped_column(Text, default="", server_default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    output_tail: Mapped[str] = mapped_column(Text, default="", server_default="")
    agent_name: Mapped[str] = mapped_column(String(200), default="", server_default="")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
