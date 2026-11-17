"""Add the knowledge graph (links), hypothesis status and challenge writeups."""

import sqlalchemy as sa
from alembic import op

revision = "d7a3c95e1b42"
down_revision = "c41d7e2f9a10"
branch_labels = None
depends_on = None

NODES = {
    "challenge": "challenges",
    "task": "tasks",
    "finding": "findings",
    "flag": "flags",
    "artifact": "artifacts",
}
RELATIONS = ("attached_to", "leads_to", "supports", "refutes", "supersedes")


def upgrade():
    op.add_column("challenges", sa.Column("writeup", sa.Text(), nullable=False, server_default=""))
    op.add_column("findings", sa.Column("status", sa.String(16), nullable=True))
    op.create_check_constraint(
        "finding_status", "findings", "status IS NULL OR status IN ('open','confirmed','refuted')"
    )
    columns = [
        sa.Column(f"{side}_{node}_id", sa.String(36), nullable=True)
        for side in ("source", "target")
        for node in NODES
    ]
    op.create_table(
        "links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("author_id", sa.String(36), nullable=False),
        sa.Column("relation", sa.String(16), nullable=False),
        sa.Column("note", sa.String(500), nullable=False, server_default=""),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        *columns,
        sa.UniqueConstraint("project_id", "id"),
        sa.UniqueConstraint("project_id", "source_id", "target_id", "relation"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["project_id", "author_id"], ["actors.project_id", "actors.id"]),
        *(
            sa.ForeignKeyConstraint(
                ["project_id", f"{side}_{node}_id"], [f"{table}.project_id", f"{table}.id"]
            )
            for side in ("source", "target")
            for node, table in NODES.items()
        ),
        *(
            sa.CheckConstraint(
                f"{side}_type != '{node}' OR {side}_{node}_id = {side}_id",
                name=f"link_{side}_{node}",
            )
            for side in ("source", "target")
            for node in NODES
        ),
        sa.CheckConstraint("relation IN ('" + "','".join(RELATIONS) + "')", name="link_relation"),
        sa.CheckConstraint("source_id != target_id", name="link_not_self"),
    )
    op.create_index("ix_links_project_id", "links", ["project_id"])
    op.create_index("ix_links_target", "links", ["project_id", "target_id"])
    op.create_index("ix_links_source", "links", ["project_id", "source_id"])


def downgrade():
    op.drop_table("links")
    op.drop_constraint("finding_status", "findings", type_="check")
    op.drop_column("findings", "status")
    op.drop_column("challenges", "writeup")
