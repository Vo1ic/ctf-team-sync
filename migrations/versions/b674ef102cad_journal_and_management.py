"""Add structured project logs and editable management metadata."""

import sqlalchemy as sa
from alembic import op

revision = "b674ef102cad"
down_revision = "a8b6231d5e90"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("actors", sa.Column("notes", sa.Text(), nullable=False, server_default=""))
    op.add_column("challenges", sa.Column("points", sa.Integer(), nullable=True))
    op.add_column("challenges", sa.Column("source_url", sa.String(2048), nullable=True))
    op.add_column(
        "tasks", sa.Column("priority", sa.String(16), nullable=False, server_default="normal")
    )
    op.add_column("tasks", sa.Column("due_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("task_priority", "tasks", "priority IN ('low','normal','urgent')")
    op.create_table(
        "logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("author_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("challenge_id", sa.String(36), nullable=True),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("project_id", "id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["project_id", "author_id"], ["actors.project_id", "actors.id"]),
        sa.ForeignKeyConstraint(["project_id", "task_id"], ["tasks.project_id", "tasks.id"]),
        sa.ForeignKeyConstraint(
            ["project_id", "challenge_id"], ["challenges.project_id", "challenges.id"]
        ),
        sa.CheckConstraint("level IN ('debug','info','warning','error')", name="log_level"),
    )
    op.create_index("ix_logs_project_id", "logs", ["project_id"])


def downgrade():
    op.drop_table("logs")
    op.drop_constraint("task_priority", "tasks", type_="check")
    op.drop_column("tasks", "due_at")
    op.drop_column("tasks", "priority")
    op.drop_column("challenges", "source_url")
    op.drop_column("challenges", "points")
    op.drop_column("actors", "notes")
