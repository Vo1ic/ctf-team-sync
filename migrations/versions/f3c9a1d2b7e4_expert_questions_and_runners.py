"""Questions to expert agents and runners that start agents from local profiles."""

import sqlalchemy as sa
from alembic import op

revision = "f3c9a1d2b7e4"
down_revision = "e5b81f0c7d23"
branch_labels = None
depends_on = None

RUN_STATES = "'requested','approved','declined','running','finished','failed','cancelled'"


def scoped(*references):
    return (
        sa.UniqueConstraint("project_id", "id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        *(
            sa.ForeignKeyConstraint(["project_id", column], [f"{table}.project_id", f"{table}.id"])
            for column, table in references
        ),
    )


def base_columns():
    return (
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade():
    op.drop_constraint("task_kind", "tasks", type_="check")
    op.create_check_constraint(
        "task_kind", "tasks", "kind IN ('work','artifact_review','question')"
    )
    op.add_column("tasks", sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("tasks", sa.Column("parent_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "tasks_parent_fkey", "tasks", "tasks", ["project_id", "parent_id"], ["project_id", "id"]
    )
    op.create_table(
        "runners",
        *base_columns(),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("profiles", sa.JSON(), nullable=False),
        sa.Column("info", sa.JSON(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *scoped(("owner_id", "actors")),
        sa.UniqueConstraint("project_id", "owner_id", "name", name="runner_name"),
    )
    op.create_index("ix_runners_project_id", "runners", ["project_id"])
    op.create_table(
        "runs",
        *base_columns(),
        sa.Column("runner_id", sa.String(36), nullable=False),
        sa.Column("profile", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("requested_by_id", sa.String(36), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("decided_by_id", sa.String(36), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("output_tail", sa.Text(), nullable=False, server_default=""),
        sa.Column("agent_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *scoped(
            ("runner_id", "runners"),
            ("task_id", "tasks"),
            ("requested_by_id", "actors"),
            ("decided_by_id", "actors"),
        ),
        sa.CheckConstraint(f"status IN ({RUN_STATES})", name="run_status"),
        sa.CheckConstraint(
            "purpose IN ('task','question','coordinator','manual')", name="run_purpose"
        ),
    )
    op.create_index("ix_runs_project_id", "runs", ["project_id"])
    op.create_index("ix_runs_active", "runs", ["project_id", "status"])


def downgrade():
    op.drop_table("runs")
    op.drop_table("runners")
    op.execute("UPDATE tasks SET kind = 'work' WHERE kind = 'question'")
    op.drop_constraint("tasks_parent_fkey", "tasks", type_="foreignkey")
    op.drop_column("tasks", "parent_id")
    op.drop_column("tasks", "tags")
    op.drop_constraint("task_kind", "tasks", type_="check")
    op.create_check_constraint("task_kind", "tasks", "kind IN ('work','artifact_review')")
