"""Scene summarizer: after a scene, Haiku writes a summary chunk tagged with the
canon entities it involves and appends it to `dynamic_chunks` (§2's dynamic
layer). This is the cheap, millisecond side of the hybrid — no graph re-index.
"""

from __future__ import annotations

import logging
import uuid

import anthropic
from pydantic import BaseModel
from sqlalchemy import select

from dm_agent.config import get_settings
from dm_agent.db import DynamicChunk, Node, db_session
from dm_agent.knowledge.embeddings import EmbeddingProvider, get_embedder

log = logging.getLogger(__name__)


class _SceneSummary(BaseModel):
    summary: str
    entity_ids: list[str]


_SUMMARY_SYSTEM = """\
You are the campaign chronicler. Summarize what just happened in this scene in 2-4 factual \
sentences a Dungeon Master could reread later to stay consistent — decisions made, outcomes, \
NPCs met, secrets revealed, world changes. Then tag it with the relevant canon entity slugs \
from the provided list (only slugs that appear in that list; omit any that don't apply). \
Write nothing about dice or game mechanics.
"""


async def summarize_scene(
    world_id: uuid.UUID,
    campaign_id: uuid.UUID,
    session_id: uuid.UUID,
    transcript: str,
    *,
    embedder: EmbeddingProvider | None = None,
    client: anthropic.AsyncAnthropic | None = None,
    model: str | None = None,
) -> DynamicChunk | None:
    """Summarize a scene transcript into one dynamic chunk. Returns it, or None if
    there was nothing to summarize.

    Reads the entity roster from the campaign's *world* (canon is world-scoped
    since M2i) but files the chunk under the campaign, which is whose play it is.
    """
    if not transcript.strip():
        return None

    settings = get_settings()
    embedder = embedder or get_embedder()
    client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)
    model = model or settings.utility_model

    async with db_session() as session:
        rows = await session.execute(
            select(Node.id, Node.name).where(Node.world_id == world_id)
        )
        known = {slug: name for slug, name in rows}

    roster = "\n".join(f"- {slug} ({name})" for slug, name in known.items()) or "- (no lore yet)"
    parsed = await client.messages.parse(
        model=model,
        max_tokens=512,
        system=_SUMMARY_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"KNOWN ENTITIES:\n{roster}\n\nSCENE TRANSCRIPT:\n{transcript}",
            }
        ],
        output_format=_SceneSummary,
    )
    out = parsed.parsed_output
    if out is None or not out.summary.strip():
        return None

    entity_ids = [slug for slug in out.entity_ids if slug in known]
    embedding = embedder.embed_one(out.summary)

    async with db_session() as session:
        chunk = DynamicChunk(
            session_id=session_id,
            campaign_id=campaign_id,
            text=out.summary.strip(),
            entity_ids=entity_ids,
            embedding=embedding,
        )
        session.add(chunk)
        await session.commit()
        await session.refresh(chunk)
    log.info("scene summarized: %s (entities: %s)", chunk.id, entity_ids)
    return chunk
