"""GraphStore: the Postgres-backed canon graph + vector search primitives.

This is the swap seam the plan calls out — a future Neo4j impl would match these
method signatures. Everything is campaign-scoped. Vector columns are compared
with pgvector cosine distance (smaller = closer); rows without an embedding are
excluded from ranked search.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Text

from dm_agent.db import CommunitySummary, DynamicChunk, Edge, Node


@dataclass
class NodeHit:
    id: str
    type: str
    name: str
    prose: str
    props: dict
    distance: float


@dataclass
class ChunkHit:
    text: str
    entity_ids: list[str]
    distance: float
    kind: str  # "community" | "dynamic"


class GraphStore:
    """Postgres implementation of the canon graph + hybrid retrieval primitives."""

    # ---- build-time writes ------------------------------------------------

    async def upsert_node(
        self,
        session: AsyncSession,
        campaign_id: uuid.UUID,
        *,
        id: str,
        type: str,
        name: str,
        props: dict,
        prose: str,
        embedding: list[float] | None = None,
    ) -> None:
        node = await session.get(Node, (campaign_id, id))
        if node is None:
            session.add(
                Node(
                    campaign_id=campaign_id,
                    id=id,
                    type=type,
                    name=name,
                    props=props,
                    prose=prose,
                    embedding=embedding,
                )
            )
        else:
            node.type, node.name, node.props, node.prose = type, name, props, prose
            if embedding is not None:
                node.embedding = embedding

    async def add_edge(
        self,
        session: AsyncSession,
        campaign_id: uuid.UUID,
        *,
        src: str,
        dst: str,
        type: str,
        props: dict | None = None,
    ) -> None:
        session.add(
            Edge(campaign_id=campaign_id, src=src, dst=dst, type=type, props=props or {})
        )

    # ---- read: exact + fuzzy entity resolution ----------------------------

    async def match_entities(
        self,
        session: AsyncSession,
        campaign_id: uuid.UUID,
        names: list[str],
        query_vectors: list[list[float]] | None = None,
        per_name_k: int = 2,
    ) -> list[str]:
        """Resolve free-text entity names from a question to canonical node slugs.

        Exact/ILIKE name match first; then, if per-name embeddings are supplied,
        nearest nodes by cosine distance. Returns a de-duplicated slug list,
        preserving first-seen order.
        """
        found: list[str] = []

        def _remember(slug: str) -> None:
            if slug not in found:
                found.append(slug)

        for i, name in enumerate(names):
            like = f"%{name.strip()}%"
            rows = await session.execute(
                select(Node.id)
                .where(Node.campaign_id == campaign_id)
                .where(Node.name.ilike(like))
                .limit(per_name_k)
            )
            for slug in rows.scalars():
                _remember(slug)

            if query_vectors is not None and i < len(query_vectors):
                vrows = await session.execute(
                    select(Node.id, Node.embedding.cosine_distance(query_vectors[i]).label("d"))
                    .where(Node.campaign_id == campaign_id)
                    .where(Node.embedding.isnot(None))
                    .order_by(text("d"))
                    .limit(per_name_k)
                )
                for slug, dist in vrows:
                    if dist is not None and dist < 0.5:  # cosine-distance gate
                        _remember(slug)

        return found

    async def neighborhood(
        self,
        session: AsyncSession,
        campaign_id: uuid.UUID,
        seed_ids: list[str],
        hops: int = 1,
    ) -> list[str]:
        """Slugs within `hops` of any seed, treating edges as undirected."""
        if not seed_ids:
            return []
        stmt = text(
            """
            WITH RECURSIVE nbh(id, depth) AS (
                SELECT unnest(:seeds)::text AS id, 0 AS depth
                UNION
                SELECT e.other, nbh.depth + 1
                FROM nbh
                JOIN LATERAL (
                    SELECT dst AS other FROM edges
                        WHERE campaign_id = :cid AND src = nbh.id
                    UNION
                    SELECT src AS other FROM edges
                        WHERE campaign_id = :cid AND dst = nbh.id
                ) e ON true
                WHERE nbh.depth < :hops
            )
            SELECT DISTINCT id FROM nbh
            """
        ).bindparams(bindparam("seeds", type_=ARRAY(Text)))
        rows = await session.execute(stmt, {"seeds": seed_ids, "cid": campaign_id, "hops": hops})
        return [r[0] for r in rows]

    async def get_nodes(
        self, session: AsyncSession, campaign_id: uuid.UUID, ids: list[str]
    ) -> list[Node]:
        if not ids:
            return []
        rows = await session.execute(
            select(Node).where(Node.campaign_id == campaign_id).where(Node.id.in_(ids))
        )
        return list(rows.scalars())

    # ---- read: vector search ----------------------------------------------

    async def search_nodes(
        self,
        session: AsyncSession,
        campaign_id: uuid.UUID,
        query_vector: list[float],
        restrict_ids: list[str] | None = None,
        top_k: int = 6,
    ) -> list[NodeHit]:
        q = (
            select(Node, Node.embedding.cosine_distance(query_vector).label("d"))
            .where(Node.campaign_id == campaign_id)
            .where(Node.embedding.isnot(None))
        )
        if restrict_ids:
            q = q.where(Node.id.in_(restrict_ids))
        q = q.order_by(text("d")).limit(top_k)
        rows = await session.execute(q)
        return [
            NodeHit(n.id, n.type, n.name, n.prose, n.props, float(d)) for n, d in rows
        ]

    async def search_community_summaries(
        self,
        session: AsyncSession,
        campaign_id: uuid.UUID,
        query_vector: list[float],
        top_k: int = 2,
    ) -> list[ChunkHit]:
        rows = await session.execute(
            select(
                CommunitySummary,
                CommunitySummary.embedding.cosine_distance(query_vector).label("d"),
            )
            .where(CommunitySummary.campaign_id == campaign_id)
            .where(CommunitySummary.embedding.isnot(None))
            .order_by(text("d"))
            .limit(top_k)
        )
        return [ChunkHit(c.text, c.member_ids, float(d), "community") for c, d in rows]

    async def search_dynamic_chunks(
        self,
        session: AsyncSession,
        campaign_id: uuid.UUID,
        query_vector: list[float],
        restrict_ids: list[str] | None = None,
        top_k: int = 6,
    ) -> list[ChunkHit]:
        q = (
            select(DynamicChunk, DynamicChunk.embedding.cosine_distance(query_vector).label("d"))
            .where(DynamicChunk.campaign_id == campaign_id)
            .where(DynamicChunk.embedding.isnot(None))
        )
        if restrict_ids:
            # entity_ids array overlaps the restrict set (Postgres && operator)
            q = q.where(DynamicChunk.entity_ids.overlap(restrict_ids))
        q = q.order_by(text("d")).limit(top_k)
        rows = await session.execute(q)
        return [ChunkHit(c.text, c.entity_ids, float(d), "dynamic") for c, d in rows]
