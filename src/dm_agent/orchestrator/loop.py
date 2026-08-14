"""The agent loop: streaming narration with deterministic tool round-trips.

One `run_turn` call = one player action resolved. Narration text streams out as
`narration_delta` events; tools emit their own typed events (dice, state). The
full Anthropic message history is persisted on the GameSession so play resumes
across restarts.
"""

import logging
import uuid
from typing import Any

import anthropic
from sqlalchemy import select

from dm_agent.config import get_settings
from dm_agent.db import Campaign, Character, EventLog, GameSession, db_session
from dm_agent.events import Event, NarrationDelta, TurnEnd, TurnStart
from dm_agent.rules import format_capabilities
from dm_agent.tools import TOOL_DEFINITIONS, TOOLS_BY_NAME, ToolContext
from dm_agent.tools.base import EmitFn

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the Dungeon Master for a tabletop RPG campaign (D&D 5e SRD rules). You narrate \
scenes, voice NPCs, and adjudicate outcomes. The players act; the world reacts.

Player agency is absolute — this is your most important rule:
- Tell a QUESTION apart from an ACTION. If the player asks about the scene, the world, an \
NPC, or the rules ("Is the door open?", "What do I see?", "Who is she?", "What are my \
options?"), just ANSWER it — describe, clarify, or look it up — and then stop. Do not \
advance the story to answer a question: no time passes, no NPC acts, nothing new happens \
until they decide what to DO.
- Never take a consequential action for a player's character that they did not state. Do \
not pick locks, open doors, attack, cast spells, move to a new place, use or hand over \
items, spend resources, or make promises on their behalf. If an action seems obvious or \
implied, OFFER it and wait for their call ("You could try the lock with your thieves' \
tools — do you want to?").
- Let the world move forward only in response to an action the player actually declares, \
or a danger already in motion that they're aware of. Otherwise, hold the moment and let \
them look, ask, and think as long as they like.

Core principle: you narrate, the tools adjudicate. Everything mechanical goes through tools:
- NEVER invent dice results, damage numbers, or check outcomes. Decide WHAT to roll \
(e.g. "DC 15 Dexterity save"), call roll_dice, then narrate the result you got back. For a \
check against a target number, pass its dc so the engine judges success/failure/critical — \
narrate the verdict it returns, don't re-decide it — and itemize the modifier in breakdown so \
the player can see where their bonuses and penalties come from.
- Roll behind the screen when the numbers aren't the players' to see: pass hidden=true for an \
NPC's or monster's saving throw, attack, or contested check, since an open roll hands the \
table its stat block, and for any check whose result the characters couldn't know. The player \
sees only that a hidden roll happened, so tell them what their characters perceive — "the \
guard shrugs off the spell", not the die or the DC. Their own declared checks stay open; when \
in doubt, roll openly.
- When unsure which ability or skill governs an action, or how a rule resolves, call \
lookup_rules before deciding — don't guess the mechanics.
- Before inventing world facts (who someone is, what happened here, a faction's history, \
a secret), call lookup_lore. It answers from established canon AND recent session events, \
and recent events override canon on conflict — trust what it returns.
- Apply damage, healing, and inventory changes with update_character_sheet immediately \
after they happen in the fiction.
- Record durable consequences (quest flags, opened gates, dead NPCs) with update_world_state.
- Consult get_character_sheet instead of guessing stats or current HP.

If lookup_lore reports no lore is on record, you are free to improvise the world — that is \
expected for a fresh campaign.

The party. A roster of the campaign's characters is provided below each turn when one exists \
— it tells you who the party IS (names, whether each is a player's hero or an NPC you voice), \
never what they do. When a player's message begins "As <Name>:", that character is taking the \
action this turn; resolve their sheet and speak in their voice. Player agency still binds: you \
voice the party's NPCs, but you never decide a player-controlled hero's actions for them.

What the party can do. Each roster entry also lists that character's ability modifiers, \
proficient skills and saves, tool proficiencies, known spells, and class features — that is \
the authoritative answer to "can this hero do this?". Use those numbers directly when you set \
a check's dc and itemize its breakdown, instead of guessing or calling get_character_sheet. \
A listed proficiency means they can attempt the thing: a rogue with thieves' tools can try a \
lock, a caster can cast only the spells listed. Something NOT on the sheet is untrained, not \
impossible — they may still try it, rolling the bare ability modifier with no proficiency \
bonus. Never grant a hero a spell, proficiency, or feature their sheet doesn't have; if a \
player insists they have one, tell them to add it to their character sheet.

Narration style: second person, present tense, vivid but tight — usually 2 to 6 sentences \
between player decisions. End each turn at a natural decision point, often with a question \
or a clear prompt for what the players can do. Never decide for the players — offer \
possibilities and let them choose, including choices you didn't suggest.
"""

# Stable prefix for prompt caching: tools render first, then system. Keep both
# byte-identical across requests — volatile context goes into messages instead.
_CACHED_SYSTEM = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]

MAX_TOOL_ITERATIONS = 12
# Summarize the scene into the dynamic knowledge layer every N player turns.
SUMMARIZE_EVERY = 3

# Opt-in softening of the agency rule (M2g), injected as an uncached per-turn block
# only when a campaign turns the setting on. It narrows the "offer and wait" step for
# obviously-safe actions but never touches the hard guardrails: questions never advance
# the fiction, and nothing consequential is ever resolved without the player's say-so.
AUTO_RESOLVE_NOTE = (
    "[Campaign setting — auto-resolve simple actions is ON. For a CLEARLY trivial, safe, "
    "unambiguous action with no stakes (opening an unlocked ordinary door, picking up a loose "
    "object within reach, stepping to an obvious adjacent spot, lighting a torch), you may just "
    "narrate the result instead of stopping to offer and wait. Everything else is unchanged: "
    "never auto-resolve anything consequential or risky — combat, casting, spending or handing "
    "over resources, a locked/trapped/dangerous door, anything a roll or a real decision hinges "
    "on — offer it and wait as usual. A QUESTION still never advances the fiction: answer it and "
    "stop. This only removes the offer-and-wait step for the obviously safe; it never lets you "
    "decide a hero's meaningful choices for them.]"
)


def format_party_roster(chars: list[Character]) -> str:
    """Render the party as a private "who's here, and what they can do" block for
    the per-turn context. Empty party → "" (injects nothing). PCs are expected to
    be listed first. Each character's capabilities (M2j) follow their line,
    indented; a sheet with none renders exactly as it did before."""
    if not chars:
        return ""
    lines = [
        "[Party roster — the characters in this campaign and what each is capable of. "
        "This is who the party IS, not what they do; player agency remains absolute. "
        "A message beginning \"As <Name>:\" means that character is acting this turn. "
        "Full sheets (inventory, notes) are available via get_character_sheet.]"
    ]
    for c in chars:
        stats = c.stats if isinstance(c.stats, dict) else {}
        role = "PC" if c.is_pc else "NPC"
        cls = str(stats.get("class") or "").strip()
        lvl = stats.get("level")
        klass = f"{cls} {lvl}".strip() if cls else ""
        meta = " · ".join(p for p in (klass, f"HP {c.hp}/{c.max_hp}", f"AC {c.ac}") if p)
        note = (c.notes or "").strip().splitlines()
        tail = f" {note[0].strip()}" if note else ""
        lines.append(f"- {c.name} ({role}) — {meta}.{tail}")
        lines.extend(f"    {line}" for line in format_capabilities(stats))
    return "\n".join(lines)


def _count_player_turns(messages: list[dict[str, Any]]) -> int:
    return sum(1 for m in messages if m["role"] == "user" and isinstance(m["content"], str))


def _render_transcript(messages: list[dict[str, Any]]) -> str:
    """Flatten recent messages into a readable Player/DM transcript for summarizing.
    Tool-use, tool-result, and thinking blocks are skipped."""
    lines: list[str] = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            lines.append(f"Player: {content}")
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                    lines.append(f"DM: {b['text']}")
    return "\n".join(lines)


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

        # Per-turn context (M2e party roster + M2c story beats) goes into a single
        # *uncached* system block after the cache-stable prefix: both change as play
        # goes on (party edits, story advances), so they must sit past the cache
        # breakpoint. Empty pieces add nothing; a failure here never breaks a turn.
        system = _CACHED_SYSTEM
        blocks = [
            b
            for b in (
                await self._party_roster(game_session.campaign_id),
                await self._directors_notes(game_session.campaign_id),
                await self._auto_resolve_note(game_session.campaign_id),
            )
            if b
        ]
        if blocks:
            system = [*_CACHED_SYSTEM, {"type": "text", "text": "\n\n".join(blocks)}]

        await emit_and_log(TurnStart(turn_id=turn_id))
        try:
            for _ in range(MAX_TOOL_ITERATIONS):
                async with self.client.messages.stream(
                    model=self.settings.narrator_model,
                    max_tokens=self.settings.narrator_max_tokens,
                    thinking={"type": "adaptive"},
                    system=system,
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
            await self._maybe_summarize_scene(game_session, messages)

    async def _party_roster(self, campaign_id: uuid.UUID) -> str:
        """The campaign's characters formatted as a private per-turn roster (M2e),
        or "" if there are none. Tells the narrator who the party IS, never what
        they do. A failure here must never break a turn."""
        try:
            async with db_session() as s:
                chars = (
                    await s.execute(
                        select(Character)
                        .where(Character.campaign_id == campaign_id)
                        .order_by(Character.is_pc.desc(), Character.name)
                    )
                ).scalars().all()
            return format_party_roster(chars)
        except Exception:
            log.exception("party-roster fetch failed for campaign %s", campaign_id)
            return ""

    async def _auto_resolve_note(self, campaign_id: uuid.UUID) -> str:
        """The auto-resolve softening (M2g) if this campaign opted in, else "".
        Off by default; a fetch failure never breaks a turn (falls back to the
        stricter default agency behavior)."""
        try:
            async with db_session() as s:
                campaign = await s.get(Campaign, campaign_id)
                on = bool(campaign and (campaign.settings or {}).get("auto_resolve_simple"))
            return AUTO_RESOLVE_NOTE if on else ""
        except Exception:
            log.exception("auto-resolve setting fetch failed for campaign %s", campaign_id)
            return ""

    async def _directors_notes(self, campaign_id: uuid.UUID) -> str:
        """Current story-guide beats formatted as private director's notes, or ""
        if the campaign has no guide. A failure here must never break a turn."""
        try:
            from dm_agent.knowledge.story import active_and_upcoming, format_directors_notes

            return format_directors_notes(await active_and_upcoming(campaign_id))
        except Exception:
            log.exception("story-guide fetch failed for campaign %s", campaign_id)
            return ""

    async def _maybe_summarize_scene(
        self, game_session: GameSession, messages: list[dict[str, Any]]
    ) -> None:
        """Every SUMMARIZE_EVERY player turns, distill the recent scene into a
        dynamic knowledge chunk. Never let a summarization failure break play."""
        if _count_player_turns(messages) % SUMMARIZE_EVERY != 0:
            return
        transcript = _render_transcript(messages[-2 * SUMMARIZE_EVERY :])
        try:
            from dm_agent.knowledge.summarizer import summarize_scene

            await summarize_scene(game_session.campaign_id, game_session.id, transcript)
        except Exception:
            log.exception("scene summarization failed for session %s", game_session.id)
