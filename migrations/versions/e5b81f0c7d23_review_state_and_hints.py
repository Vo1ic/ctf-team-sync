"""Task review state (flag must be confirmed) and human-approved hint requests."""

import sqlalchemy as sa
from alembic import op

revision = "e5b81f0c7d23"
down_revision = "d7a3c95e1b42"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("tasks_status_check", "tasks", type_="check")
    op.create_check_constraint(
        "task_status", "tasks", "status IN ('open','running','review','done')"
    )
    op.create_table(
        "hint_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("challenge_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("requester_id", sa.String(36), nullable=False),
        sa.Column("hint_ref", sa.String(200), nullable=False, server_default=""),
        sa.Column("cost", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("decided_by_id", sa.String(36), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finding_id", sa.String(36), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("project_id", "id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        *(
            sa.ForeignKeyConstraint(["project_id", column], [f"{table}.project_id", f"{table}.id"])
            for column, table in (
                ("challenge_id", "challenges"),
                ("task_id", "tasks"),
                ("requester_id", "actors"),
                ("decided_by_id", "actors"),
                ("finding_id", "findings"),
            )
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','declined','used')", name="hint_status"
        ),
    )
    op.create_index("ix_hint_requests_project_id", "hint_requests", ["project_id"])


def downgrade():
    op.drop_table("hint_requests")
    op.execute("UPDATE tasks SET status = 'done' WHERE status = 'review'")
    op.drop_constraint("task_status", "tasks", type_="check")
    op.create_check_constraint("tasks_status_check", "tasks", "status IN ('open','running','done')")
