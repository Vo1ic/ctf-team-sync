"""Versioned resource edits, scoped history, and linked immutable artifact versions."""

import sqlalchemy as sa
from alembic import op

revision = "a8b6231d5e90"
down_revision = "9c1e7a302b44"
branch_labels = None
depends_on = None

VERSIONED = [
    "projects",
    "challenges",
    "findings",
    "verifications",
    "submissions",
    "messages",
    "activities",
]
TARGETS = {
    "actors": "target_actor_id",
    "challenges": "challenge_id",
    "tasks": "task_id",
    "findings": "finding_id",
    "flags": "flag_id",
    "verifications": "verification_id",
    "submissions": "submission_id",
    "messages": "message_id",
    "artifacts": "artifact_id",
    "activities": "activity_id",
}


def upgrade():
    for table in VERSIONED:
        op.add_column(table, sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column(
        "message_receipts",
        sa.Column("message_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("artifacts", sa.Column("supersedes_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_artifacts_supersedes_project",
        "artifacts",
        "artifacts",
        ["project_id", "supersedes_id"],
        ["project_id", "id"],
    )
    op.create_unique_constraint(
        "uq_artifacts_supersedes", "artifacts", ["project_id", "supersedes_id"]
    )
    for table in ["verifications", "submissions"]:
        op.add_column(table, sa.Column("subject", sa.JSON(), nullable=True))
        op.execute(
            sa.text(
                f"UPDATE {table} s SET subject=json_build_object('value',f.value, "
                "'challenge_id',f.challenge_id,'target',f.target,'round',f.round) "
                "FROM flags f WHERE s.flag_id=f.id AND s.project_id=f.project_id"
            )
        )
    op.create_table(
        "resource_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collection", sa.String(24), nullable=False),
        sa.Column("resource_id", sa.String(36), nullable=False),
        sa.Column("editor_id", sa.String(36), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        *(sa.Column(column, sa.String(36), nullable=True) for column in TARGETS.values()),
        sa.UniqueConstraint("project_id", "id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["project_id", "editor_id"], ["actors.project_id", "actors.id"]),
        *(
            sa.ForeignKeyConstraint(["project_id", column], [f"{table}.project_id", f"{table}.id"])
            for table, column in TARGETS.items()
        ),
        *(
            sa.CheckConstraint(
                f"collection != '{table}' OR ({column} IS NOT NULL AND {column}=resource_id)",
                name=f"revision_{table}_target",
            )
            for table, column in TARGETS.items()
        ),
        sa.CheckConstraint(
            "collection != 'projects' OR resource_id=project_id", name="revision_project_target"
        ),
        sa.CheckConstraint(
            "collection IN ('projects','actors','challenges','tasks','findings',"
            "'flags','verifications','submissions','messages','artifacts','activities')",
            name="revision_collection",
        ),
    )
    op.create_index("ix_resource_revisions_project_id", "resource_revisions", ["project_id"])
    op.create_index(
        "ix_revisions_resource",
        "resource_revisions",
        ["project_id", "collection", "resource_id", "created_at"],
    )


def downgrade():
    op.drop_table("resource_revisions")
    for table in ["verifications", "submissions"]:
        op.drop_column(table, "subject")
    op.drop_constraint("uq_artifacts_supersedes", "artifacts", type_="unique")
    op.drop_constraint("fk_artifacts_supersedes_project", "artifacts", type_="foreignkey")
    op.drop_column("artifacts", "supersedes_id")
    op.drop_column("message_receipts", "message_version")
    for table in VERSIONED:
        op.drop_column(table, "version")
