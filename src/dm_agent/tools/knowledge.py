"""M2 knowledge tools: grounded rules + lore lookup for the narrator.

`lookup_rules` closes the gap where the model was adjudicating skills/abilities
purely from its own training (no grounding). `lookup_lore` is the §2 hybrid
retrieval black box. Both are read-only — no state mutation, no events.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, text

from dm_agent.config import get_settings
from dm_agent.db import RulesChunk, db_session
from dm_agent.knowledge.embeddings import get_embedder
from dm_agent.knowledge.retrieval import lookup_lore as _lookup_lore
from dm_agent.tools.base import Tool, ToolContext


async def _lookup_rules(ctx: ToolContext, args: dict[str, Any]) -> str:
    query = args["query"]
    ruleset = get_settings().default_ruleset
    qvec = get_embedder().embed_one(query)
    async with db_session() as session:
        rows = await session.execute(
            select(RulesChunk.text, RulesChunk.embedding.cosine_distance(qvec).label("d"))
            .where(RulesChunk.ruleset_id == ruleset)
            .where(RulesChunk.embedding.isnot(None))
            .order_by(text("d"))
            .limit(4)
        )
        chunks = [r[0] for r in rows]
    if not chunks:
        return (
            "No rules corpus is loaded for this ruleset. Adjudicate with standard "
            "D&D 5e SRD knowledge, deciding the ability/skill and DC yourself."
        )
    return "\n\n---\n\n".join(chunks)


async def _lookup_lore_tool(ctx: ToolContext, args: dict[str, Any]) -> str:
    answer = await _lookup_lore(ctx.campaign_id, args["question"])
    if answer.source_ids:
        return f"{answer.text}\n\n(sources: {', '.join(answer.source_ids)})"
    return answer.text


TOOLS: list[Tool] = [
    Tool(
        name="lookup_rules",
        description=(
            "Look up game rules (which ability/skill governs an action, how a mechanic works, "
            "conditions, DCs) from the loaded ruleset corpus. Call this when unsure what to roll "
            "or how a rule resolves — don't guess."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language rules question, e.g. 'sneaking past a guard'",
                }
            },
            "required": ["query"],
        },
        handler=_lookup_rules,
    ),
    Tool(
        name="lookup_lore",
        description=(
            "Ask about the world's canon and what has happened this campaign: people, places, "
            "factions, history, secrets. Returns a synthesized answer grounded in the lore graph "
            "AND recent session events (recent events override canon on conflict). Call this "
            "before inventing world facts."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "What you want to know about the world or recent events",
                }
            },
            "required": ["question"],
        },
        handler=_lookup_lore_tool,
    ),
]
