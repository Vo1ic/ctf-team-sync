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

EditableCollection = Literal[
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
ResourceCollection = Literal[EditableCollection, "logs"]


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
    notes: str = Field(default="", max_length=10000)


class ChallengeCreate(Input):
    title: Title
    description: str = Field(default="", max_length=100_000)
    category: Annotated[str, Field(max_length=100)] | None = None
    points: Annotated[int, Field(ge=0)] | None = None
    source_url: Annotated[str, Field(max_length=2048, pattern=r"^https?://\S+$")] | None = None
    allow_duplicate: bool = Field(
        default=False,
        description="Create even when an equally titled record already exists (409 otherwise).",
    )


class TaskCreate(ChallengeCreate):
    category: Annotated[str, Field(max_length=100)] | None = Field(
        default=None, description="Accepted for compatibility and ignored; set it on the challenge."
    )
    challenge_id: ID | None = None
    requested_assignee_id: ID | None = None
    points: Annotated[int, Field(ge=0)] | None = Field(
        default=None, description="Accepted for compatibility and ignored; set it on the challenge."
    )
    source_url: None = None
    priority: Literal["low", "normal", "urgent"] = "normal"
    due_at: AwareDatetime | None = None


class LinkDelete(Input):
    """Remove one edge; the linked records themselves are never deleted."""


class Assign(Input):
    expected_version: Version
    recipient_id: ID | None = None
    force: bool = Field(
        default=False,
        description="Coordinator override: take a LIVE assignment away (needs reason).",
    )
    reason: Annotated[str, Field(max_length=10_000)] = ""


class Rename(Input):
    expected_version: Version
    name: Title


class Versioned(Input):
    expected_version: Version


class TaskJoin(Input):
    actor_id: ID | None = Field(
        default=None,
        description="Default self. An agent may act for its human owner; leads for anyone.",
    )
    note: Annotated[str, Field(max_length=300)] = ""


class TaskLeave(Input):
    actor_id: ID | None = None


class TaskJoinMany(Input):
    """Join every unfinished task of a category or challenge, e.g. 'I take all forensics'."""

    category: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    challenge_id: ID | None = None
    actor_id: ID | None = None
    note: Annotated[str, Field(max_length=300)] = ""

    @model_validator(mode="after")
    def require_scope(self):
        if not self.category and not self.challenge_id:
            raise ValueError("category or challenge_id is required")
        return self


class InviteCreate(Input):
    label: Annotated[str, Field(max_length=200)] = ""
    role: Literal["coordinator", "worker", "observer"] = "worker"
    expires_in_hours: Annotated[int, Field(ge=1, le=720)] = 72
    max_uses: Annotated[int, Field(ge=1, le=100)] = 10


class JoinRequest(Input):
    human_name: Title
    agent_name: Title | None = None
    capabilities: list[Title] = Field(default_factory=list, max_length=100)
    create_agent: bool = True


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
    without_flag: bool = Field(
        default=False,
        description=(
            "Helper work that does not produce the challenge flag. Otherwise a challenge "
            "task stays in review until a human confirms a flag."
        ),
    )


class HintRequestCreate(Input):
    """Ask a human to approve a paid platform hint; requires published evidence first."""

    challenge_id: ID | None = None
    task_id: ID | None = None
    reason: Body
    hint_ref: Annotated[str, Field(max_length=200)] = ""
    cost: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def require_scope(self):
        if not self.challenge_id and not self.task_id:
            raise ValueError("challenge_id or task_id is required")
        return self


class HintDecision(Input):
    expected_version: Version
    decision: Literal["approved", "declined"]
    note: Annotated[str, Field(max_length=10000)] = ""


class HintUsed(Input):
    expected_version: Version
    content: Body


Skill = Literal[
    "general",
    "vision",
    "crypto",
    "reversing",
    "pwn",
    "web",
    "forensics",
    "stego",
    "osint",
    "math",
    "network",
]
ProfileName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]


class QuestionAsk(Input):
    """Ask a stronger expert agent (or a human) a focused question about your work."""

    question: Title = Field(description="One focused question, e.g. 'What is hidden in this PNG?'")
    details: Body = Field(description="What you see: data, output, where exactly you are stuck")
    tried: Annotated[str, Field(min_length=20, max_length=100_000, pattern=r"\S")] = Field(
        description="What you already tested and what happened (required, be concrete)"
    )
    task_id: ID | None = None
    challenge_id: ID | None = None
    skills: list[Skill] = Field(default_factory=lambda: ["general"], min_length=1, max_length=4)
    artifact_ids: list[ID] = Field(default_factory=list, max_length=10)
    priority: Literal["normal", "urgent"] = "normal"

    @model_validator(mode="after")
    def require_scope(self):
        if not self.challenge_id and not self.task_id:
            raise ValueError("challenge_id or task_id is required")
        return self


class QuestionAnswer(Input):
    expected_version: Version
    answer: Body
    confidence: Literal["low", "medium", "high"] = "medium"
    evidence: Annotated[str, Field(max_length=100_000)] = ""


class RunRequest(Input):
    """Ask a runner to start one of its locally defined profiles."""

    profile: ProfileName
    runner_id: ID | None = Field(
        default=None, description="Default: an online runner that offers the profile"
    )
    task_id: ID | None = None
    note: Annotated[str, Field(max_length=2000)] = ""


class RunDecision(Input):
    expected_version: Version
    decision: Literal["approved", "declined"]
    note: Annotated[str, Field(max_length=2000)] = ""


class RunCancel(Input):
    expected_version: Version
    reason: Annotated[str, Field(max_length=2000)] = ""


class RunnerProfile(Input):
    name: ProfileName
    description: Annotated[str, Field(max_length=500)] = ""
    skills: list[Skill] = Field(default_factory=list, max_length=11)
    answers_questions: bool = False
    works_tasks: bool = False
    categories: list[Annotated[str, Field(max_length=100)]] = Field(
        default_factory=list, max_length=20
    )
    coordinator: bool = False
    max_minutes: Annotated[int, Field(ge=1, le=600)] = 30
    model: Annotated[str, Field(max_length=100)] = ""


class RunnerRegister(Input):
    """Runner heartbeat: advertises profile names and limits, never commands."""

    name: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[\w.@-]+$")]
    profiles: list[RunnerProfile] = Field(default_factory=list, max_length=32)
    info: dict = Field(default_factory=dict)
    paused: bool = False


class RunState(Input):
    status: Literal["running", "finished", "failed", "cancelled"]
    exit_code: int | None = None
    output_tail: Annotated[str, Field(max_length=20_000)] = ""
    agent_name: Annotated[str, Field(max_length=200)] = ""
    agent_id: ID | None = None


class Release(Input):
    expected_version: int = Field(ge=1)
    reason: Body


HypothesisStatus = Literal["open", "confirmed", "refuted"]
NodeType = Literal["challenge", "task", "finding", "flag", "artifact"]
Relation = Literal["attached_to", "leads_to", "supports", "refutes", "supersedes"]


class FindingCreate(Input):
    title: Title
    body: Body
    kind: Literal["observation", "hypothesis", "result", "failed_attempt", "decoy"] = "observation"
    evidence: Body
    challenge_id: ID | None = None
    task_id: ID | None = None
    artifact_id: ID | None = None
    status: HypothesisStatus | None = Field(
        default=None, description="Hypothesis state; a new hypothesis defaults to open."
    )
    based_on: list[ID] = Field(
        default_factory=list,
        max_length=20,
        description="Findings this one follows from (creates leads_to edges: chain/graph).",
    )
    refutes: list[ID] = Field(
        default_factory=list,
        max_length=20,
        description="Findings this one disproves; refuted hypotheses change state.",
    )


class LinkCreate(Input):
    """One edge: artifact attached_to challenge/task/finding, or finding chains.

    leads_to: finding/task -> finding/flag/task; supports/supersedes: finding -> finding;
    refutes: finding -> finding/flag (a refuted hypothesis changes its status).
    """

    source_type: NodeType
    source_id: ID
    target_type: NodeType
    target_id: ID
    relation: Relation
    note: Annotated[str, Field(max_length=500)] = ""


class FlagCreate(Input):
    value: Annotated[str, Field(min_length=1, max_length=4096, pattern=r"\S")]
    challenge_id: ID | None = Field(default=None, description="Derived from task_id when omitted.")
    task_id: ID | None = None
    target: Title | None = None
    round: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    expires_at: AwareDatetime | None = None


class FlagVerify(Input):
    expected_version: int = Field(ge=1)
    status: Literal["confirmed", "rejected"]
    method: Literal["manual", "local_checker", "platform"] = "manual"
    evidence: Annotated[str, Field(max_length=100_000)] = Field(
        default="", description="Optional; a quick decision records who decided and when."
    )


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
    expected_version: Version | None = None
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
        "resource.update",
        "log.create",
        "challenge.create",
        "task.create",
        "task.claim",
        "task.renew",
        "task.complete",
        "task.release",
        "task.assign",
        "task.join",
        "task.leave",
        "task.join_many",
        "link.create",
        "link.delete",
        "hint.request",
        "hint.decide",
        "hint.used",
        "question.ask",
        "question.answer",
        "run.request",
        "run.decide",
        "run.cancel",
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
                "resource.update",
                "task.claim",
                "task.renew",
                "task.complete",
                "task.release",
                "task.assign",
                "task.join",
                "task.leave",
                "link.delete",
                "hint.decide",
                "hint.used",
                "question.answer",
                "run.decide",
                "run.cancel",
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
            raise ValueError(
                "resource_id is required for this operation; put the target id at the top "
                "level of the command envelope, next to operation and arguments"
            )
        return self


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


class ResourceEdit(Input):
    collection: EditableCollection
    expected_version: Version
    changes: dict = Field(min_length=1, max_length=20)
    reason: str = Field(default="", max_length=10000)
    force: bool = False


class ProjectFields(Input):
    name: Title = None
    profile: Literal["jeopardy", "attack_defense", "custom"] = None
    capabilities: dict[str, bool] = None
    rules: Annotated[str, Field(max_length=100000)] = None
    briefing: Annotated[str, Field(max_length=100000)] = None


class ActorFields(Input):
    name: Title = None
    role: Role = None
    capabilities: Annotated[list[Title], Field(max_length=100)] = None
    owner_id: ID | None = None
    active: bool = None
    notes: Annotated[str, Field(max_length=10000)] = None


class ChallengeFields(Input):
    title: Title = None
    description: Annotated[str, Field(max_length=100000)] = None
    category: Annotated[str, Field(max_length=100)] | None = None
    points: Annotated[int, Field(ge=0)] | None = None
    source_url: Annotated[str, Field(max_length=2048, pattern=r"^https?://\S+$")] | None = None
    writeup: Annotated[str, Field(max_length=200000)] = None


class TaskFields(Input):
    title: Title = None
    description: Annotated[str, Field(max_length=100000)] = None
    challenge_id: ID | None = None
    requested_assignee_id: ID | None = None
    status: Literal["open", "done"] = None
    result: Annotated[str, Field(max_length=100000)] | None = None
    priority: Literal["low", "normal", "urgent"] = None
    due_at: AwareDatetime | None = None


class LogCreate(Input):
    level: Literal["debug", "info", "warning", "error"] = "info"
    body: Annotated[str, Field(min_length=1, max_length=20000, pattern=r"\S")]
    task_id: ID | None = None
    challenge_id: ID | None = None


class JournalQuery(Input):
    before: int | None = Field(default=None, ge=1)
    actor_id: ID | None = None
    resource_id: ID | None = None
    collection: ResourceCollection | None = None
    event_type: str | None = Field(default=None, min_length=1, max_length=100)
    level: Literal["debug", "info", "warning", "error"] | None = None
    query: str | None = Field(default=None, min_length=1, max_length=200)
    include_routine: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    max_chars: ContextBudget = 12000


class FindingFields(Input):
    title: Title = None
    body: Body = None
    evidence: Body = None
    kind: Literal["observation", "hypothesis", "result", "failed_attempt", "decoy"] = None
    status: HypothesisStatus | None = None
    challenge_id: ID | None = None
    task_id: ID | None = None
    artifact_id: ID | None = None


class FlagFields(Input):
    value: Annotated[str, Field(min_length=1, max_length=4096, pattern=r"\S")] = None
    challenge_id: ID | None = None
    task_id: ID | None = None
    target: Title | None = None
    round: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    expires_at: AwareDatetime | None = None


class VerificationFields(Input):
    status: Literal["confirmed", "rejected"] = None
    method: Literal["manual", "local_checker", "platform"] = None
    evidence: Body = None


class SubmissionFields(Input):
    outcome: Literal["accepted", "rejected", "unknown"] = None
    evidence: Body = None


class MessageFields(Input):
    body: Body = None
    kind: Literal["note", "question", "assignment", "status", "approval", "result"] = None
    priority: Literal["normal", "urgent"] = None
    task_id: ID | None = None
    group_key: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    expires_at: AwareDatetime | None = None


class ArtifactFields(Input):
    name: Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[^/\\\x00-\x1f]+$")] = None
    media_type: Annotated[str, Field(min_length=1, max_length=120)] = None
    requires_review: bool = None


class ActivityFields(Input):
    state: Literal["idle", "working", "waiting_human", "blocked", "ready"] = None
    detail: Annotated[str, Field(max_length=300)] = None
    task_id: ID | None = None
    ttl_seconds: Annotated[int, Field(ge=30, le=600)] = None
