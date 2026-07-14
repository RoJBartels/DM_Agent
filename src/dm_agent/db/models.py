import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Embedding dimension of the configured sentence-transformers model
# (BAAI/bge-small-en-v1.5 = 384). Changing the model changes this and requires a
# migration, so it lives here alongside the columns that use it.
EMBED_DIM = 384


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sessions: Mapped[list["GameSession"]] = relationship(back_populates="campaign")
    characters: Mapped[list["Character"]] = relationship(back_populates="campaign")


class GameSession(Base):
    __tablename__ = "game_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"))
    title: Mapped[str] = mapped_column(Text, default="")
    # Anthropic messages array for this session, persisted verbatim so play can resume.
    history: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    campaign: Mapped[Campaign] = relationship(back_populates="sessions")


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"))
    name: Mapped[str] = mapped_column(Text)
    is_pc: Mapped[bool] = mapped_column(Boolean, default=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # STR..CHA, proficiencies
    max_hp: Mapped[int] = mapped_column(Integer, default=10)
    hp: Mapped[int] = mapped_column(Integer, default=10)
    ac: Mapped[int] = mapped_column(Integer, default=10)
    inventory: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")

    campaign: Mapped[Campaign] = relationship(back_populates="characters")


class WorldFlag(Base):
    __tablename__ = "world_flags"

    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EventLog(Base):
    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("game_sessions.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event: Mapped[dict[str, Any]] = mapped_column(JSONB)


# --- Knowledge layer (M2): the hybrid static-canon graph + dynamic session RAG ---
#
# Lore is scoped per campaign (a campaign is a world). `id` is a human-readable
# slug used as the join key in `entity_ids` arrays elsewhere — the composite PK
# (campaign_id, id) keeps two campaigns' worlds from colliding on the same slug.
# Edges reference node slugs softly (no FK): a build inserts nodes and edges in
# any order, and an edge may point at a node the extractor named but didn't emit.


class Node(Base):
    __tablename__ = "nodes"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id"), primary_key=True
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # slug
    type: Mapped[str] = mapped_column(Text)  # Character/Location/Faction/Item/Deity/Event/Law
    name: Mapped[str] = mapped_column(Text)
    props: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    prose: Mapped[str] = mapped_column(Text, default="")  # the text that gets embedded
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), nullable=True)
    community_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Edge(Base):
    __tablename__ = "edges"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"))
    src: Mapped[str] = mapped_column(Text)  # node slug
    dst: Mapped[str] = mapped_column(Text)  # node slug
    type: Mapped[str] = mapped_column(Text)  # RULES/LOCATED_IN/MEMBER_OF/ENEMY_OF/...
    props: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class CommunitySummary(Base):
    __tablename__ = "community_summaries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"))
    community_id: Mapped[int] = mapped_column(Integer)
    member_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), nullable=True)


class DynamicChunk(Base):
    """Session-history layer: Haiku scene summaries appended as play happens.

    Joined to canon by `entity_ids` (node slugs). `campaign_id` is denormalized
    from the session so lookup_lore can filter a campaign's chunks without a join.
    """

    __tablename__ = "dynamic_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("game_sessions.id"))
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    text: Mapped[str] = mapped_column(Text)
    entity_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), nullable=True)


class RulesChunk(Base):
    """Rules corpus (SRD 5.1 today). Separate from lore per §3; global, not
    campaign-scoped. `ruleset_id` is forward-compat for M8 (constant 'dnd5e' now)."""

    __tablename__ = "rules_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ruleset_id: Mapped[str] = mapped_column(Text, default="dnd5e")
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), nullable=True)


# --- Story / adventure guide (M2c, §2b): optional, advisory, per campaign ---
#
# A different kind of bucket than the §2 lore graph: lore is declarative fact,
# a beat is the author's intended arc. Beats are surfaced proactively to the
# narrator each turn as private "director's notes" (never enforced) and
# progressed via update_story_progress. A campaign with no uploaded guide has
# zero rows here and plays exactly as an improvised campaign. Beats are fetched
# by (campaign, status, order) — never vector-searched — so there is no
# embedding column. `entity_ids` join beats to canon node slugs (same pattern as
# dynamic_chunks). SQL `order` is reserved, so the column is `order_index`.


class StoryBeat(Base):
    __tablename__ = "story_beats"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"))
    order_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    read_aloud: Mapped[str] = mapped_column(Text, default="")
    trigger_condition: Mapped[str] = mapped_column(Text, default="")
    entity_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    status: Mapped[str] = mapped_column(Text, default="upcoming")  # upcoming|active|completed|skipped
