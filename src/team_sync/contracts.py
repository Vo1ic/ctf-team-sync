import json
from pathlib import Path

from team_sync import schemas as s
from team_sync.bridge_protocol import DeliveryBatch, request_schema


def operation_schemas():
    models = {
        "resource.update": s.ResourceEdit,
        "log.create": s.LogCreate,
        "challenge.create": s.ChallengeCreate,
        "task.create": s.TaskCreate,
        "task.claim": s.Lease,
        "task.renew": s.Lease,
        "task.complete": s.Complete,
        "task.release": s.Release,
        "task.assign": s.Assign,
        "task.join": s.TaskJoin,
        "task.leave": s.TaskLeave,
        "task.join_many": s.TaskJoinMany,
        "link.create": s.LinkCreate,
        "link.delete": s.LinkDelete,
        "hint.request": s.HintRequestCreate,
        "hint.decide": s.HintDecision,
        "hint.used": s.HintUsed,
        "question.ask": s.QuestionAsk,
        "question.answer": s.QuestionAnswer,
        "run.request": s.RunRequest,
        "run.decide": s.RunDecision,
        "run.cancel": s.RunCancel,
        "actor.rename": s.Rename,
        "activity.report": s.ActivityReport,
        "artifact.review.request": s.ReviewRequest,
        "artifact.review.decide": s.ReviewDecision,
        "artifact.review.start": s.Versioned,
        "artifact.review.complete": s.ReviewFinish,
        "artifact.archive": s.Archive,
        "finding.create": s.FindingCreate,
        "flag.create": s.FlagCreate,
        "flag.verify": s.FlagVerify,
        "submission.create": s.SubmissionCreate,
        "message.send": s.MessageCreate,
        "message.ack": s.MessageAck,
    }
    schemas = {name: model.model_json_schema() for name, model in models.items()}
    for name, example in EXAMPLES.items():
        schemas[name]["examples"] = [example]
    return schemas


_TASK = "00000000-0000-4000-8000-000000000001"
_FLAG = "00000000-0000-4000-8000-000000000002"
# Full HTTP/command envelopes: resource_id sits next to operation, not inside arguments.
EXAMPLES = {
    "task.create": {
        "operation": "task.create",
        "arguments": {"title": "Solve: Clipboard Heist Pt. 2", "challenge_id": _TASK},
        "idempotency_key": "create-clipboard-2",
    },
    "task.claim": {
        "operation": "task.claim",
        "resource_id": _TASK,
        "arguments": {"expected_version": 1, "lease_seconds": 900},
        "idempotency_key": "claim-1",
    },
    "task.join": {
        "operation": "task.join",
        "resource_id": _TASK,
        "arguments": {"note": "checking the pcap in parallel"},
        "idempotency_key": "join-1",
    },
    "task.join_many": {
        "operation": "task.join_many",
        "arguments": {"category": "forensics"},
        "idempotency_key": "take-all-forensics",
    },
    "finding.create": {
        "operation": "finding.create",
        "arguments": {
            "title": "Archive password reused from the PNG comment",
            "body": "7z accepts the password from exiftool Comment.",
            "evidence": "exiftool img.png | grep Comment; 7z t data.7z -p<redacted> -> OK",
            "kind": "result",
            "task_id": _TASK,
        },
        "idempotency_key": "finding-1",
    },
    "flag.create": {
        "operation": "flag.create",
        "arguments": {"value": "CTF{<redacted>}", "task_id": _TASK},
        "idempotency_key": "flag-1",
    },
    "flag.verify": {
        "operation": "flag.verify",
        "resource_id": _FLAG,
        "arguments": {"expected_version": 1, "status": "confirmed"},
        "idempotency_key": "verify-1",
    },
    "resource.update": {
        "operation": "resource.update",
        "resource_id": _TASK,
        "arguments": {
            "collection": "tasks",
            "expected_version": 3,
            "changes": {"priority": "urgent"},
            "reason": "blocks two other tasks",
        },
        "idempotency_key": "edit-1",
    },
}


def main():
    # The API schema factory constructs an engine but never opens a DB connection.
    from team_sync.api import create_app
    from team_sync.settings import Settings

    directory = Path("contracts")
    directory.mkdir(exist_ok=True)
    app = create_app(
        Settings(
            "postgresql+psycopg://unused:unused@localhost/unused",
            "contract-export-only",
            Path("data/artifacts"),
        )
    )
    documents = {
        "openapi.json": app.openapi(),
        "commands.schema.json": operation_schemas(),
        "event.schema.json": s.EventEnvelope.model_json_schema(),
        "agent-bridge.schema.json": request_schema(),
        "delivery-batch.schema.json": DeliveryBatch.model_json_schema(),
    }
    for name, document in documents.items():
        (directory / name).write_text(json.dumps(document, indent=2) + "\n")
    app.state.core.engine.dispose()
