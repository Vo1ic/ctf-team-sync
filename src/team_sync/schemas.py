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
    name: Title
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


class Lease(Input):
    expected_version: int = Field(ge=1)
    lease_seconds: int = Field(default=300, ge=10, le=3600)


class Complete(Input):
    expected_version: int = Field(ge=1)
    result: Body


class FindingCreate(Input):
    title: Title
    body: Body
    kind: Literal["observation", "hypothesis", "result", "failed_attempt"] = "observation"
    evidence: Body
    challenge_id: ID | None = None
    task_id: ID | None = None


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
        "finding.create",
        "flag.create",
        "flag.verify",
        "submission.create",
        "message.send",
        "message.ack",
    ]
    resource_id: ID | None = None
    arguments: dict = Field(default_factory=dict)
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def require_resource(self):
        if (
            self.operation
            in {
                "task.claim",
                "task.renew",
                "task.complete",
                "flag.verify",
                "submission.create",
                "message.ack",
            }
            and self.resource_id is None
        ):
            raise ValueError("resource_id is required for this operation")
        return self
