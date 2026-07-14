"""M2c tool: update_story_progress.

The story guide (§2b) is advisory. This tool mirrors update_world_state's split:
the LLM makes the semantic judgment that a beat has been reached, completed, or
skipped by the players' choices; the tool deterministically persists it. No dice,
no gating — nothing here constrains what the players can do, only what the DM
privately expects next.

The tool is always registered (so the cached tool definitions stay byte-stable),
but the narrator is only told about beats via the per-turn director's notes, which
appear only when a guide is loaded. Called with no matching beat, it errors
harmlessly — the same way lookup_lore reports "no lore on record".
"""

from __future__ import annotations

import uuid
from typing import Any

from dm_agent.db import StoryBeat, db_session
from dm_agent.events import StateUpdate
from dm_agent.knowledge.story import BEAT_STATUSES
from dm_agent.tools.base import Tool, ToolContext


async def _update_story_progress(ctx: ToolContext, args: dict[str, Any]) -> str:
    beat_id = args["beat_id"]
    status = args["status"]
    if status not in BEAT_STATUSES:
        return f"Error: status must be one of {sorted(BEAT_STATUSES)}"
    try:
        bid = uuid.UUID(str(beat_id))
    except (ValueError, AttributeError):
        return f"Error: beat_id {beat_id!r} is not a valid beat id"

    async with db_session() as session:
        beat = await session.get(StoryBeat, bid)
        if beat is None or beat.campaign_id != ctx.campaign_id:
            return f"Error: no story beat {beat_id} in this campaign"
        beat.status = status
        title = beat.title
        await session.commit()

    await ctx.emit(StateUpdate(entity="story", changes={title: status}))
    return f"Story beat '{title}' marked {status}."


TOOLS: list[Tool] = [
    Tool(
        name="update_story_progress",
        description=(
            "Record progress through the uploaded story guide. Only relevant when you "
            "see DIRECTOR'S NOTES this turn; otherwise ignore it. Pass the beat_id shown "
            "in the notes and a status: 'active' when the party reaches a beat, "
            "'completed' when it resolves, 'skipped' if they bypass it. Advisory only — "
            "this never constrains the players, it just tracks where the story is."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "beat_id": {"type": "string", "description": "the beat's id from the notes"},
                "status": {
                    "type": "string",
                    "enum": ["upcoming", "active", "completed", "skipped"],
                },
            },
            "required": ["beat_id", "status"],
        },
        handler=_update_story_progress,
    ),
]
