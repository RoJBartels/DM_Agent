"""The agent loop: streaming narration with deterministic tool round-trips.

One `run_turn` call = one player action resolved. Narration text streams out as
`narration_delta` events; tools emit their own typed events (dice, state). The
full Anthropic message history is persisted on the GameSession so play resumes
across restarts.
"""

import logging
from typing import Any

import anthropic

from dm_agent.config import get_settings
from dm_agent.db import EventLog, GameSession, db_session
from dm_agent.events import Event, NarrationDelta, TurnEnd, TurnStart
from dm_agent.tools import TOOL_DEFINITIONS, TOOLS_BY_NAME, ToolContext
from dm_agent.tools.base import EmitFn

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the Dungeon Master for a tabletop RPG campaign (D&D 5e SRD rules). You narrate \
scenes, voice NPCs, and adjudicate outcomes. The players act; the world reacts.

Core principle: you narrate, the tools adjudicate. Everything mechanical goes through tools:
- NEVER invent dice results, damage numbers, or check outcomes. Decide WHAT to roll \
(e.g. "DC 15 Dexterity save"), call roll_dice, then narrate the result you got back.
- Apply damage, healing, and inventory changes with update_character_sheet immediately \
after they happen in the fiction.
- Record durable consequences (quest flags, opened gates, dead NPCs) with update_world_state.
- Consult get_character_sheet instead of guessing stats or current HP.

Narration style: second person, present tense, vivid but tight — usually 2 to 6 sentences \
between player decisions. End each turn at a natural decision point, often with a question \
or a clear prompt for what the players can do. Never decide for the players.
"""

# Stable prefix for prompt caching: tools render first, then system. Keep both
# byte-identical across requests — volatile context goes into messages instead.
_CACHED_SYSTEM = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]

MAX_TOOL_ITERATIONS = 12


def _make_client() -> anthropic.AsyncAnthropic:
    settings = get_settings()
    if settings.anthropic_api_key:
        return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return anthropic.AsyncAnthropic()  # ANTHROPIC_API_KEY / ant profile resolution


class Orchestrator:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = _make_client()

    async def run_turn(self, game_session: GameSession, player_text: str, emit: EmitFn) -> None:
        """Resolve one player action. Streams events via `emit`; persists history."""
        turn_id = f"turn-{len(game_session.history)}"

        async def emit_and_log(event: Event) -> None:
            await emit(event)
            # narration_delta is high-volume and reconstructable from history — don't log it
            if event.type != "narration_delta":
                async with db_session() as s:
                    s.add(EventLog(session_id=game_session.id, event=event.model_dump(mode="json")))
                    await s.commit()

        ctx = ToolContext(
            campaign_id=game_session.campaign_id,
            session_id=game_session.id,
            emit=emit_and_log,
        )

        messages: list[dict[str, Any]] = [*game_session.history]
        messages.append({"role": "user", "content": player_text})

        await emit_and_log(TurnStart(turn_id=turn_id))
        try:
            for _ in range(MAX_TOOL_ITERATIONS):
                async with self.client.messages.stream(
                    model=self.settings.narrator_model,
                    max_tokens=self.settings.narrator_max_tokens,
                    thinking={"type": "adaptive"},
                    system=_CACHED_SYSTEM,
                    tools=TOOL_DEFINITIONS,
                    messages=messages,
                ) as stream:
                    async for text in stream.text_stream:
                        await emit(NarrationDelta(text=text))
                    final = await stream.get_final_message()

                # Append the assistant turn verbatim (incl. thinking blocks — the API
                # requires them unchanged when continuing on the same model).
                messages.append(
                    {
                        "role": "assistant",
                        "content": [b.model_dump(mode="json", exclude_none=True) for b in final.content],
                    }
                )

                if final.stop_reason == "pause_turn":
                    continue
                if final.stop_reason != "tool_use":
                    break

                tool_results: list[dict[str, Any]] = []
                for block in final.content:
                    if block.type != "tool_use":
                        continue
                    tool = TOOLS_BY_NAME.get(block.name)
                    if tool is None:
                        result, is_error = f"Error: unknown tool {block.name!r}", True
                    else:
                        try:
                            result = await tool.handler(ctx, dict(block.input))
                            is_error = result.startswith("Error:")
                        except Exception:
                            log.exception("tool %s failed", block.name)
                            result, is_error = "Error: tool execution failed unexpectedly.", True
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                            "is_error": is_error,
                        }
                    )
                # All results for one assistant turn go back in a single user message.
                messages.append({"role": "user", "content": tool_results})
        finally:
            async with db_session() as s:
                db_sess = await s.get(GameSession, game_session.id)
                if db_sess is not None:
                    db_sess.history = messages
                    await s.commit()
            game_session.history = messages
            await emit_and_log(TurnEnd(turn_id=turn_id))
