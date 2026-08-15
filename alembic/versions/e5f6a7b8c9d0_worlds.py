"""worlds as the top-level container (M2i)

Adds `worlds` and re-scopes the canon graph (nodes/edges/community_summaries)
from campaign to world, because lore describes a *setting* and every campaign
played in that setting shares it. `campaigns.world_id` is what makes picking a
world change which campaigns, characters and stories exist.

The backfill wraps every pre-M2i campaign in **its own** world, named after it.
That is the only choice that preserves today's isolation exactly: lore was
campaign-scoped, so folding several campaigns into one shared world would let
each suddenly read the others' canon. Merging worlds afterwards is a decision
for the user, not for a migration.

`dynamic_chunks` and `story_beats` deliberately stay campaign-scoped — they are
one party's play, not the setting.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-14

"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables whose rows move wholesale from a campaign to that campaign's world.
_RESCOPED = ("nodes", "edges", "community_summaries")


def upgrade() -> None:
    op.create_table(
        "worlds",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        # Reserved for M8's per-world rules binding; NULL = the 5e SRD default.
        sa.Column("ruleset_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Add the new scoping columns nullable, so the backfill has somewhere to write.
    op.add_column("campaigns", sa.Column("world_id", sa.UUID(), nullable=True))
    for table in _RESCOPED:
        op.add_column(table, sa.Column("world_id", sa.UUID(), nullable=True))

    # --- backfill: one world per existing campaign -------------------------
    conn = op.get_bind()
    campaigns = conn.execute(
        sa.text("SELECT id, name, created_at FROM campaigns ORDER BY created_at")
    ).all()
    for campaign_id, name, created_at in campaigns:
        world_id = uuid.uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO worlds (id, name, description, created_at) "
                "VALUES (:wid, :name, :description, :created_at)"
            ),
            {
                "wid": world_id,
                # Named after the campaign because that is the only name we have.
                # The description says so, since a world and a campaign sharing a
                # name is confusing on the menu and renaming it is the fix.
                "name": name,
                "description": (
                    f"Created automatically from the campaign “{name}” when worlds were "
                    "introduced. Rename it to whatever this setting is actually called."
                ),
                "created_at": created_at,
            },
        )
        conn.execute(
            sa.text("UPDATE campaigns SET world_id = :wid WHERE id = :cid"),
            {"wid": world_id, "cid": campaign_id},
        )
        for table in _RESCOPED:
            conn.execute(
                sa.text(f"UPDATE {table} SET world_id = :wid WHERE campaign_id = :cid"),  # noqa: S608
                {"wid": world_id, "cid": campaign_id},
            )

    # --- swap the scoping column over -------------------------------------
    op.alter_column("campaigns", "world_id", nullable=False)
    op.create_foreign_key("campaigns_world_id_fkey", "campaigns", "worlds", ["world_id"], ["id"])
    op.create_index("ix_campaigns_world_id", "campaigns", ["world_id"])

    # nodes: the PK is composite, so it has to be rebuilt around the new column.
    op.drop_constraint("nodes_pkey", "nodes", type_="primary")
    op.drop_column("nodes", "campaign_id")  # takes its FK with it
    op.alter_column("nodes", "world_id", nullable=False)
    op.create_primary_key("nodes_pkey", "nodes", ["world_id", "id"])
    op.create_foreign_key("nodes_world_id_fkey", "nodes", "worlds", ["world_id"], ["id"])

    op.drop_index("ix_edges_campaign_src", table_name="edges")
    op.drop_index("ix_edges_campaign_dst", table_name="edges")
    op.drop_column("edges", "campaign_id")
    op.alter_column("edges", "world_id", nullable=False)
    op.create_foreign_key("edges_world_id_fkey", "edges", "worlds", ["world_id"], ["id"])
    op.create_index("ix_edges_world_src", "edges", ["world_id", "src"])
    op.create_index("ix_edges_world_dst", "edges", ["world_id", "dst"])

    op.drop_index("ix_community_summaries_campaign", table_name="community_summaries")
    op.drop_column("community_summaries", "campaign_id")
    op.alter_column("community_summaries", "world_id", nullable=False)
    op.create_foreign_key(
        "community_summaries_world_id_fkey", "community_summaries", "worlds", ["world_id"], ["id"]
    )
    op.create_index("ix_community_summaries_world", "community_summaries", ["world_id"])


def downgrade() -> None:
    # Reverses cleanly only while worlds map 1:1 to campaigns, which is what the
    # upgrade produced. Lore in a world with several campaigns is copied to the
    # oldest of them (campaign-scoped canon cannot express sharing) and lore in a
    # world with no campaign at all has nowhere to go, so it is dropped.
    for table in _RESCOPED:
        op.add_column(table, sa.Column("campaign_id", sa.UUID(), nullable=True))

    conn = op.get_bind()
    owners = conn.execute(
        sa.text(
            "SELECT DISTINCT ON (world_id) world_id, id FROM campaigns "
            "ORDER BY world_id, created_at"
        )
    ).all()
    for world_id, campaign_id in owners:
        for table in _RESCOPED:
            conn.execute(
                sa.text(f"UPDATE {table} SET campaign_id = :cid WHERE world_id = :wid"),  # noqa: S608
                {"cid": campaign_id, "wid": world_id},
            )
    for table in _RESCOPED:
        conn.execute(sa.text(f"DELETE FROM {table} WHERE campaign_id IS NULL"))  # noqa: S608

    op.drop_index("ix_community_summaries_world", table_name="community_summaries")
    op.drop_column("community_summaries", "world_id")
    op.alter_column("community_summaries", "campaign_id", nullable=False)
    op.create_foreign_key(
        "community_summaries_campaign_id_fkey",
        "community_summaries",
        "campaigns",
        ["campaign_id"],
        ["id"],
    )
    op.create_index("ix_community_summaries_campaign", "community_summaries", ["campaign_id"])

    op.drop_index("ix_edges_world_src", table_name="edges")
    op.drop_index("ix_edges_world_dst", table_name="edges")
    op.drop_column("edges", "world_id")
    op.alter_column("edges", "campaign_id", nullable=False)
    op.create_foreign_key("edges_campaign_id_fkey", "edges", "campaigns", ["campaign_id"], ["id"])
    op.create_index("ix_edges_campaign_src", "edges", ["campaign_id", "src"])
    op.create_index("ix_edges_campaign_dst", "edges", ["campaign_id", "dst"])

    op.drop_constraint("nodes_pkey", "nodes", type_="primary")
    op.drop_column("nodes", "world_id")
    op.alter_column("nodes", "campaign_id", nullable=False)
    op.create_primary_key("nodes_pkey", "nodes", ["campaign_id", "id"])
    op.create_foreign_key("nodes_campaign_id_fkey", "nodes", "campaigns", ["campaign_id"], ["id"])

    op.drop_index("ix_campaigns_world_id", table_name="campaigns")
    op.drop_column("campaigns", "world_id")
    op.drop_table("worlds")
