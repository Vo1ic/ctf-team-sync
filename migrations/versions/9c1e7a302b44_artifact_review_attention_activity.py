"""Add artifact review, named assignments, attention metadata and activity."""

import sqlalchemy as sa
from alembic import op

revision = "9c1e7a302b44"
down_revision = "e9ddd51d494e"
branch_labels = None
depends_on = None


def scoped_fk(table, column, target):
    op.create_foreign_key(
        f"fk_{table}_{column}_project", table, target, ["project_id", column], ["project_id", "id"]
    )


def upgrade():
    op.add_column("actors", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    for column in [
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "media_type", sa.String(120), nullable=False, server_default="application/octet-stream"
        ),
        sa.Column("requires_review", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("archive_reason", sa.Text(), nullable=True),
        sa.Column("archived_by_id", sa.String(36), nullable=True),
    ]:
        op.add_column("artifacts", column)
    scoped_fk("artifacts", "archived_by_id", "actors")
    for column in [
        sa.Column("created_by_id", sa.String(36), nullable=True),
        sa.Column("requested_assignee_id", sa.String(36), nullable=True),
        sa.Column("artifact_id", sa.String(36), nullable=True),
        sa.Column("kind", sa.String(24), nullable=False, server_default="work"),
        sa.Column("approval_status", sa.String(24), nullable=False, server_default="not_required"),
        sa.Column("approved_by_id", sa.String(36), nullable=True),
        sa.Column("approval_note", sa.Text(), nullable=True),
        sa.Column("analysis_started_at", sa.DateTime(timezone=True), nullable=True),
    ]:
        op.add_column("tasks", column)
    for column, target in [
        ("created_by_id", "actors"),
        ("requested_assignee_id", "actors"),
        ("artifact_id", "artifacts"),
        ("approved_by_id", "actors"),
    ]:
        scoped_fk("tasks", column, target)
    op.create_check_constraint("task_kind", "tasks", "kind IN ('work','artifact_review')")
    op.create_check_constraint(
        "task_approval",
        "tasks",
        "approval_status IN ('not_required','pending','approved','declined')",
    )
    op.add_column("findings", sa.Column("artifact_id", sa.String(36), nullable=True))
    scoped_fk("findings", "artifact_id", "artifacts")
    for column in [
        sa.Column("kind", sa.String(24), nullable=False, server_default="note"),
        sa.Column("priority", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("group_key", sa.String(128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_to_id", sa.String(36), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="1"),
    ]:
        op.add_column("messages", column)
    scoped_fk("messages", "task_id", "tasks")
    scoped_fk("messages", "reply_to_id", "messages")
    op.create_check_constraint(
        "message_kind",
        "messages",
        "kind IN ('note','question','assignment','status','approval','result')",
    )
    op.create_check_constraint("message_priority", "messages", "priority IN ('normal','urgent')")
    op.create_index("ix_messages_fingerprint", "messages", ["fingerprint"])
    op.create_index("ix_events_actor_time", "events", ["project_id", "actor_id", "created_at"])
    op.create_table(
        "activities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("detail", sa.String(300), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "id"),
        sa.UniqueConstraint("project_id", "actor_id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["project_id", "actor_id"], ["actors.project_id", "actors.id"]),
        sa.ForeignKeyConstraint(["project_id", "task_id"], ["tasks.project_id", "tasks.id"]),
        sa.CheckConstraint(
            "state IN ('idle','working','waiting_human','blocked','ready')", name="activity_state"
        ),
    )
    op.create_index("ix_activities_project_id", "activities", ["project_id"])


def downgrade():
    op.drop_table("activities")
    op.drop_index("ix_events_actor_time", table_name="events")
    op.drop_index("ix_messages_fingerprint", table_name="messages")
    for constraint in ["message_kind", "message_priority"]:
        op.drop_constraint(constraint, "messages", type_="check")
    for column in ["reply_to_id", "task_id"]:
        op.drop_constraint(f"fk_messages_{column}_project", "messages", type_="foreignkey")
    for column in [
        "duplicate_count",
        "fingerprint",
        "reply_to_id",
        "expires_at",
        "group_key",
        "task_id",
        "priority",
        "kind",
    ]:
        op.drop_column("messages", column)
    op.drop_constraint("fk_findings_artifact_id_project", "findings", type_="foreignkey")
    op.drop_column("findings", "artifact_id")
    for constraint in ["task_kind", "task_approval"]:
        op.drop_constraint(constraint, "tasks", type_="check")
    for column in ["created_by_id", "requested_assignee_id", "artifact_id", "approved_by_id"]:
        op.drop_constraint(f"fk_tasks_{column}_project", "tasks", type_="foreignkey")
    for column in [
        "analysis_started_at",
        "approval_note",
        "approved_by_id",
        "approval_status",
        "kind",
        "artifact_id",
        "requested_assignee_id",
        "created_by_id",
    ]:
        op.drop_column("tasks", column)
    op.drop_constraint("fk_artifacts_archived_by_id_project", "artifacts", type_="foreignkey")
    for column in [
        "archived_by_id",
        "archive_reason",
        "archived",
        "requires_review",
        "media_type",
        "version",
    ]:
        op.drop_column("artifacts", column)
    op.drop_column("actors", "version")
