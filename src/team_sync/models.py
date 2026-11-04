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
        CheckConstraint("status IN ('open','running','done')"),
        CheckConstraint("kind IN ('work','artifact_review')", name="task_kind"),
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


class Flag(Scoped, Base):
    __tablename__ = "flags"
    __table_args__ = scoped_constraints(
        "flags",
        reference("challenge_id", "challenges"),
        reference("author_id", "actors"),
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
