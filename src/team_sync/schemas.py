from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

ID = Annotated[
    str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
]
Title = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
Body = Annotated[str, Field(min_length=1, max_length=100_000, pattern=r"\S")]
CommandKey = Annotated[str, Field(min_length=1, max_length=128)]
Version = Annotated[int, Field(ge=1)]
LeaseSeconds = Annotated[int, Field(ge=10, le=3600)]
ContextLimit = Annotated[int, Field(ge=1, le=20)]
ContextBudget = Annotated[int, Field(ge=4000, le=32000)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Role(StrEnum):
    owner = "owner"
    coordinator = "coordinator"
    worker = "worker"
    observer = "observer"


class ProjectCreate(Input):
    name: Title
    owner_name: Title
    profile: Literal["jeopardy", "attack_defense", "custom"] = "jeopardy"
    capabilities: dict[str, bool] = Field(default_factory=lambda: {"flags": True})
    rules: str = Field(default="", max_length=100_000)


class ActorCreate(Input):
    name: Title | None = None
    kind: Literal["human", "agent"] = "agent"
    role: Role = Role.worker
    owner_id: ID | None = None
    capabilities: list[Title] = Field(default_factory=list, max_length=100)


class ChallengeCreate(Input):
    title: Title
    description: str = Field(default="", max_length=100_000)
    category: Annotated[str, Field(max_length=100)] | None = None


class TaskCreate(ChallengeCreate):
    category: None = None
    challenge_id: ID | None = None
    requested_assignee_id: ID | None = None


class Assign(Input):
    expected_version: Version
    recipient_id: ID | None = None


class Rename(Input):
    expected_version: Version
    name: Title


class Versioned(Input):
    expected_version: Version


class ActivityReport(Input):
    state: Literal["idle", "working", "waiting_human", "blocked", "ready"]
    detail: Annotated[str, Field(max_length=300)] = ""
    task_id: ID | None = None
    ttl_seconds: Annotated[int, Field(ge=30, le=600)] = 120


class ReviewRequest(Input):
    recipient_id: ID | None = None


class ReviewDecision(Input):
    expected_version: Version
    decision: Literal["approved", "declined"]
    note: Annotated[str, Field(max_length=10000)] = ""


class ReviewFinish(Input):
    expected_version: Version
    readme: Body
    verdict: Literal["useful", "unusable"] = "useful"
    reason: Annotated[str, Field(max_length=10000)] = ""


class Archive(Input):
    expected_version: Version
    archived: bool
    reason: Body


class Lease(Input):
    expected_version: int = Field(ge=1)
    lease_seconds: int = Field(default=300, ge=10, le=3600)


class Complete(Input):
    expected_version: int = Field(ge=1)
    result: Body


class Release(Input):
    expected_version: int = Field(ge=1)
    reason: Body


class FindingCreate(Input):
    title: Title
    body: Body
    kind: Literal["observation", "hypothesis", "result", "failed_attempt"] = "observation"
    evidence: Body
    challenge_id: ID | None = None
    task_id: ID | None = None
    artifact_id: ID | None = None


class FlagCreate(Input):
    value: Annotated[str, Field(min_length=1, max_length=4096, pattern=r"\S")]
    challenge_id: ID | None = None
    target: Title | None = None
    round: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    expires_at: AwareDatetime | None = None


class FlagVerify(Input):
    expected_version: int = Field(ge=1)
    status: Literal["confirmed", "rejected"]
    method: Literal["manual", "local_checker", "platform"]
    evidence: Body


class SubmissionCreate(Input):
    outcome: Literal["accepted", "rejected", "unknown"]
    evidence: Body


class MessageCreate(Input):
    recipient_id: ID | None = None
    body: Body
    kind: Literal["note", "question", "assignment", "status", "approval", "result"] = "note"
    priority: Literal["normal", "urgent"] = "normal"
    task_id: ID | None = None
    group_key: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    expires_at: AwareDatetime | None = None
    reply_to_id: ID | None = None
    deduplicate: bool = True


class MessageAck(Input):
    stage: Literal["received", "processed"]


class EventEnvelope(BaseModel):
    id: UUID
    project_id: UUID
    seq: int
    schema_version: Literal[1]
    type: str
    actor_id: UUID
    payload: dict
    created_at: datetime


class AgentAction(Input):
    """Transport-independent MCP command envelope."""

    operation: Literal[
        "challenge.create",
        "task.create",
        "task.claim",
        "task.renew",
        "task.complete",
        "task.release",
        "task.assign",
        "actor.rename",
        "activity.report",
        "artifact.review.request",
        "artifact.review.decide",
        "artifact.review.start",
        "artifact.review.complete",
        "artifact.archive",
        "finding.create",
        "flag.create",
        "flag.verify",
        "submission.create",
        "message.send",
        "message.ack",
    ]
    resource_id: ID | None = None
    arguments: dict = Field(default_factory=dict)
    idempotency_key: CommandKey

    @model_validator(mode="after")
    def require_resource(self):
        if (
            self.operation
            in {
                "task.claim",
                "task.renew",
                "task.complete",
                "task.release",
                "task.assign",
                "actor.rename",
                "artifact.review.request",
                "artifact.review.decide",
                "artifact.review.start",
                "artifact.review.complete",
                "artifact.archive",
                "flag.verify",
                "submission.create",
                "message.ack",
            }
            and self.resource_id is None
        ):
            raise ValueError("resource_id is required for this operation")
        return self


ResourceCollection = Literal[
    "projects",
    "actors",
    "challenges",
    "tasks",
    "findings",
    "flags",
    "verifications",
    "submissions",
    "artifacts",
    "messages",
    "activities",
]


class WorkspaceQuery(Input):
    challenge_id: ID | None = None
    task_id: ID | None = None
    limit: int = Field(default=5, ge=1, le=20)
    max_chars: int = Field(default=12000, ge=4000, le=32000)


class SearchQuery(Input):
    query: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    collection: ResourceCollection | None = None
    offset: int = Field(default=0, ge=0, le=1000)
    limit: int = Field(default=20, ge=1, le=50)


class AttentionQuery(Input):
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=20, ge=1, le=50)
    max_chars: ContextBudget = 12000
