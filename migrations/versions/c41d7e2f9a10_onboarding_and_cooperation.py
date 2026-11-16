"""Add project briefing (CTF.md), invites, task participants and flag task links."""

import sqlalchemy as sa
from alembic import op

revision = "c41d7e2f9a10"
down_revision = "b674ef102cad"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("projects", sa.Column("briefing", sa.Text(), nullable=False, server_default=""))
    op.add_column("flags", sa.Column("task_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "flags_task_fkey",
        "flags",
        "tasks",
        ["project_id", "task_id"],
        ["project_id", "id"],
    )
    op.create_table(
        "task_participants",
        sa.Column("project_id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(36), primary_key=True),
        sa.Column("added_by_id", sa.String(36), nullable=False),
        sa.Column("note", sa.String(300), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id", "task_id"], ["tasks.project_id", "tasks.id"]),
        sa.ForeignKeyConstraint(["project_id", "actor_id"], ["actors.project_id", "actors.id"]),
        sa.ForeignKeyConstraint(["project_id", "added_by_id"], ["actors.project_id", "actors.id"]),
    )
    op.create_table(
        "invites",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by_id", sa.String(36), nullable=False),
        sa.Column("label", sa.String(200), nullable=False, server_default=""),
        sa.Column("role", sa.String(16), nullable=False, server_default="worker"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("uses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("project_id", "id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(
            ["project_id", "created_by_id"], ["actors.project_id", "actors.id"]
        ),
        sa.CheckConstraint("role IN ('coordinator','worker','observer')", name="invite_role"),
    )
    op.create_index("ix_invites_project_id", "invites", ["project_id"])


def downgrade():
    op.drop_index("ix_invites_project_id", "invites")
    op.drop_table("invites")
    op.drop_table("task_participants")
    op.drop_constraint("flags_task_fkey", "flags", type_="foreignkey")
    op.drop_column("flags", "task_id")
    op.drop_column("projects", "briefing")
