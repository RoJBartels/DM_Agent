"""knowledge layer (M2): nodes, edges, community_summaries, dynamic_chunks, rules_chunks

Revision ID: f1a2b3c4d5e6
Revises: 4b800353ed38
Create Date: 2026-07-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "4b800353ed38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBED_DIM = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "nodes",
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("props", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prose", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=True),
        sa.Column("community_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
        sa.PrimaryKeyConstraint("campaign_id", "id"),
    )

    op.create_table(
        "edges",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("src", sa.Text(), nullable=False),
        sa.Column("dst", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("props", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_edges_campaign_src", "edges", ["campaign_id", "src"])
    op.create_index("ix_edges_campaign_dst", "edges", ["campaign_id", "dst"])

    op.create_table(
        "community_summaries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("community_id", sa.Integer(), nullable=False),
        sa.Column("member_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=True),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_community_summaries_campaign", "community_summaries", ["campaign_id"])

    op.create_table(
        "dynamic_chunks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("entity_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["game_sessions.id"]),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dynamic_chunks_campaign", "dynamic_chunks", ["campaign_id"])

    op.create_table(
        "rules_chunks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ruleset_id", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rules_chunks_ruleset", "rules_chunks", ["ruleset_id"])


def downgrade() -> None:
    op.drop_table("rules_chunks")
    op.drop_table("dynamic_chunks")
    op.drop_table("community_summaries")
    op.drop_table("edges")
    op.drop_table("nodes")
    # leave the `vector` extension installed — other objects may depend on it
