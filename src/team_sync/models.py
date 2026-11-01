from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
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


class Challenge(Scoped, Base):
    __tablename__ = "challenges"
    __table_args__ = scoped_constraints("challenges")
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str | None] = mapped_column(String(100))


class Task(Scoped, Base):
    __tablename__ = "tasks"
    __table_args__ = scoped_constraints(
        "tasks",
        reference("challenge_id", "challenges"),
        reference("assignee_id", "actors"),
        CheckConstraint("status IN ('open','running','done')"),
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    challenge_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), default="open")
    assignee_id: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    result: Mapped[str | None] = mapped_column(Text)


class Finding(Scoped, Base):
    __tablename__ = "findings"
    __table_args__ = scoped_constraints(
        "findings",
        reference("challenge_id", "challenges"),
        reference("task_id", "tasks"),
        reference("author_id", "actors"),
    )
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(24))
    evidence: Mapped[str] = mapped_column(Text)
    challenge_id: Mapped[str | None] = mapped_column(String(36))
    task_id: Mapped[str | None] = mapped_column(String(36))
    author_id: Mapped[str] = mapped_column(String(36))


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


class Message(Scoped, Base):
    __tablename__ = "messages"
    __table_args__ = scoped_constraints(
        "messages",
        reference("sender_id", "actors"),
        reference("recipient_id", "actors"),
    )
    sender_id: Mapped[str] = mapped_column(String(36))
    recipient_id: Mapped[str | None] = mapped_column(String(36))
    body: Mapped[str] = mapped_column(Text)


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


class Artifact(Scoped, Base):
    __tablename__ = "artifacts"
    __table_args__ = scoped_constraints("artifacts", reference("author_id", "actors"))
    name: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    author_id: Mapped[str] = mapped_column(String(36))


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (reference("actor_id", "actors"),)
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
