"""lookup_lore: the hybrid retrieval pipeline (§2), a single black box to the
orchestrator.

Pipeline: extract entities from the question (Haiku) -> resolve to canonical node
slugs -> expand the graph neighborhood -> vector-search static prose, community
summaries, and dynamic session chunks -> synthesize an answer (Haiku) under the
recency-override rule (session events supersede canon on conflict).

The pure retrieval assembly (`gather_context`) does no LLM calls, so ranking is
testable on its own; the two Haiku calls sit in thin wrappers around it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import anthropic
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from dm_agent.config import get_settings
from dm_agent.db import db_session
from dm_agent.knowledge.embeddings import EmbeddingProvider, get_embedder
from dm_agent.knowledge.store import ChunkHit, GraphStore, NodeHit


@dataclass
class RetrievedContext:
    node_hits: list[NodeHit] = field(default_factory=list)
    community_hits: list[ChunkHit] = field(default_factory=list)
    dynamic_hits: list[ChunkHit] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.node_hits or self.community_hits or self.dynamic_hits)


@dataclass
class LoreAnswer:
    text: str
    source_ids: list[str]


class _QuestionEntities(BaseModel):
    names: list[str]


async def gather_context(
    session: AsyncSession,
    world_id: uuid.UUID,
    campaign_id: uuid.UUID,
    question: str,
    entity_names: list[str],
    embedder: EmbeddingProvider,
    store: GraphStore | None = None,
) -> RetrievedContext:
    """LLM-free: resolve entities, expand the neighborhood, run the vector searches.

    Takes both scopes because the hybrid spans both (M2i): canon belongs to the
    world and is shared by every campaign played in it, while the dynamic chunks
    are this campaign's own play and must not leak into a sibling campaign.
    """
    store = store or GraphStore()

    qvec = embedder.embed_one(question)
    name_vecs = embedder.embed(entity_names) if entity_names else []

    seeds = await store.match_entities(session, world_id, entity_names, name_vecs or None)
    neighborhood = await store.neighborhood(session, world_id, seeds, hops=2)

    restrict = neighborhood or None
    node_hits = await store.search_nodes(session, world_id, qvec, restrict_ids=restrict, top_k=6)
    community_hits = await store.search_community_summaries(session, world_id, qvec, top_k=2)

    # Recency safety: session events matter even when the entity link is weak, so
    # union neighborhood-restricted chunks with a small unrestricted vector search.
    dyn_linked = await store.search_dynamic_chunks(
        session, campaign_id, qvec, restrict_ids=neighborhood or None, top_k=6
    )
    dyn_global = await store.search_dynamic_chunks(session, campaign_id, qvec, top_k=3)
    seen: set[str] = set()
    dynamic_hits: list[ChunkHit] = []
    for hit in sorted([*dyn_linked, *dyn_global], key=lambda h: h.distance):
        if hit.text not in seen:
            seen.add(hit.text)
            dynamic_hits.append(hit)

    source_ids: list[str] = []
    for slug in [*seeds, *(h.id for h in node_hits)]:
        if slug not in source_ids:
            source_ids.append(slug)

    return RetrievedContext(node_hits, community_hits, dynamic_hits, source_ids)


async def extract_question_entities(
    question: str,
    client: anthropic.AsyncAnthropic,
    model: str,
) -> list[str]:
    parsed = await client.messages.parse(
        model=model,
        max_tokens=256,
        system=(
            "Extract the named entities (people, places, factions, items, deities, "
            "events, laws) a reader would need to look up to answer this question. "
            "Return just their names. If none, return an empty list."
        ),
        messages=[{"role": "user", "content": question}],
        output_format=_QuestionEntities,
    )
    out = parsed.parsed_output
    return out.names if out else []


_SYNTHESIS_SYSTEM = """\
You answer a question about a tabletop RPG world using the provided context. The context has \
two parts: CANON (established worldbuilding) and RECENT SESSION EVENTS (what has happened in \
play so far).

Rules:
- Recency override: session events supersede canon on conflict. If a recent event contradicts \
canon (e.g. a character canon says is alive was killed in a session), the recent state is the \
truth. Explicitly flag it as a recent development.
- Answer only from the context. If the context does not cover it, say so plainly — do not invent.
- Be concise and factual: this answer goes to the Dungeon Master, not the players.
"""


def _format_context(ctx: RetrievedContext) -> str:
    canon_parts = [f"[{h.type}] {h.name}: {h.prose}" for h in ctx.node_hits]
    canon_parts += [f"[Overview] {h.text}" for h in ctx.community_hits]
    recent_parts = [h.text for h in ctx.dynamic_hits]
    canon = "\n".join(f"- {p}" for p in canon_parts) or "- (none)"
    recent = "\n".join(f"- {p}" for p in recent_parts) or "- (none)"
    return f"CANON:\n{canon}\n\nRECENT SESSION EVENTS:\n{recent}"


async def synthesize(
    question: str,
    ctx: RetrievedContext,
    client: anthropic.AsyncAnthropic,
    model: str,
) -> str:
    msg = await client.messages.create(
        model=model,
        max_tokens=768,
        system=_SYNTHESIS_SYSTEM,
        messages=[
            {"role": "user", "content": f"{_format_context(ctx)}\n\nQUESTION: {question}"}
        ],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


async def lookup_lore(
    world_id: uuid.UUID,
    campaign_id: uuid.UUID,
    question: str,
    *,
    embedder: EmbeddingProvider | None = None,
    client: anthropic.AsyncAnthropic | None = None,
) -> LoreAnswer:
    """End-to-end lore lookup: the single tool the orchestrator calls."""
    settings = get_settings()
    embedder = embedder or get_embedder()
    client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)

    entity_names = await extract_question_entities(question, client, settings.utility_model)
    async with db_session() as session:
        ctx = await gather_context(
            session, world_id, campaign_id, question, entity_names, embedder
        )

    if ctx.is_empty():
        return LoreAnswer(
            text="No lore is on record for this world yet — narrate freely.", source_ids=[]
        )
    answer = await synthesize(question, ctx, client, settings.utility_model)
    return LoreAnswer(text=answer, source_ids=ctx.source_ids)
