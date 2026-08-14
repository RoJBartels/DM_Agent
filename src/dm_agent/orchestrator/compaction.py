"""Context compaction & cost control (M2k).

Two "histories" exist and only one of them costs tokens. The *display* transcript
(M2d) is fetched over HTTP and rendered client-side — free, and it stays whole
forever. The *model* context is `GameSession.history`, which was re-sent to the
narrator in full on every turn: ~O(n) per turn, O(n²) over a campaign, with
nothing bounding it.

This module bounds the model context without touching the history a player can
read back. Three moving parts:

* `plan_cut` picks a compaction point on a **player-turn boundary**, so a
  `tool_result` is never orphaned from the `tool_use` that produced it.
* `summarize_history` rolls the dropped turns into a running "story so far",
  folding the *previous* summary in — so each compaction costs a fixed amount
  rather than growing with the campaign.
* `build_messages` assembles what actually goes on the wire, and
  `with_cache_breakpoint` marks the prefix so re-reads of the surviving history
  bill at the cache-read rate instead of full price.

Compacting is safe because durable facts already live in the lore graph and in
`dynamic_chunks` (the scene summarizer): what gets dropped is the verbatim
wording of old turns, never the canon they established.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from dm_agent.config import get_settings

log = logging.getLogger(__name__)

Message = dict[str, Any]

# The live context is compacted once its estimated size passes this. Sized so an
# ordinary evening's play is never touched and only a long campaign pays the
# summarizer — compacting sooner would also thrash the message cache, which is
# the other half of this milestone.
COMPACT_ABOVE_TOKENS = 24_000
# Player turns always kept verbatim after a compaction. The narrator needs the
# immediate scene word-for-word; older play is what the summary is for.
KEEP_RECENT_TURNS = 8
# Turns of recent play fed to the player-facing recap alongside the summary.
RECAP_RECENT_TURNS = 10

# Rough chars-per-token for JSON-serialized messages. Only ever used to decide
# *when* to compact, never to bill anything, so an estimate beats an API round
# trip on every turn.
_CHARS_PER_TOKEN = 4

STORY_SO_FAR_HEADER = (
    "[Story so far — your own notes on this session up to this point. The turns they cover "
    "have been condensed out of the conversation below to keep it short; treat these notes as "
    "established fact you remember, not as something a player said.]"
)


# --- inspecting a history --------------------------------------------------


def turn_starts(messages: list[Message]) -> list[int]:
    """Indices where a player turn begins — a user message carrying plain text.

    `run_turn` appends exactly one of these per turn and everything else in the
    array (assistant content, tool_result batches) hangs off the turn before it,
    which makes these the only safe places to cut.
    """
    return [
        i
        for i, m in enumerate(messages)
        if m.get("role") == "user" and isinstance(m.get("content"), str)
    ]


def estimate_tokens(messages: list[Message]) -> int:
    return len(json.dumps(messages, default=str)) // _CHARS_PER_TOKEN


def render_transcript(messages: list[Message]) -> str:
    """Flatten messages into a readable Player/DM transcript for summarizing.
    Tool-use, tool-result, and thinking blocks are skipped."""
    lines: list[str] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            lines.append(f"Player: {content}")
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                    lines.append(f"DM: {b['text']}")
    return "\n".join(lines)


def plan_cut(
    messages: list[Message],
    *,
    covered: int = 0,
    threshold_tokens: int = COMPACT_ABOVE_TOKENS,
    keep_turns: int = KEEP_RECENT_TURNS,
) -> int | None:
    """Index to compact up to, or None to leave the context alone.

    Returns a player-turn boundary strictly after `covered` (what the current
    summary already replaces), leaving at least `keep_turns` turns verbatim.
    """
    if covered < 0 or covered > len(messages):
        return None
    if estimate_tokens(messages[covered:]) < threshold_tokens:
        return None
    starts = [i for i in turn_starts(messages) if i >= covered]
    if len(starts) <= keep_turns:
        return None  # not enough turns to drop one and still keep the window
    cut = starts[-keep_turns]
    return cut if cut > covered else None


# --- assembling what goes on the wire --------------------------------------


def build_messages(history: list[Message], context: dict[str, Any] | None) -> list[Message]:
    """The messages actually sent to the narrator: the running summary folded
    onto the first surviving turn, then the rest of the history verbatim.

    The summary rides *inside* the first kept message rather than as a message of
    its own — no fabricated turn, no two user messages in a row, and the whole
    prefix stays byte-stable between compactions so it can be cached.
    """
    context = context or {}
    covered = int(context.get("covered") or 0)
    summary = str(context.get("summary") or "").strip()
    if not summary or covered <= 0 or covered >= len(history):
        return [*history]
    tail = history[covered:]
    first = tail[0]
    if not isinstance(first.get("content"), str):
        # Not a turn boundary: something reshaped the history under us. Sending
        # everything costs more than planned; sending a broken array fails.
        log.warning("compaction pointer %d is not a turn boundary — sending full history", covered)
        return [*history]
    framed = {
        **first,
        "content": [
            {"type": "text", "text": f"{STORY_SO_FAR_HEADER}\n{summary}"},
            {"type": "text", "text": first["content"]},
        ],
    }
    return [framed, *tail[1:]]


def with_cache_breakpoint(message: Message) -> Message:
    """A copy of `message` with an ephemeral cache breakpoint on its last block.

    Everything before the breakpoint bills at the cache-read rate on the next
    request that shares it. Copies rather than mutates: these dicts are the
    persisted history, and `cache_control` must never be written back to it.
    """
    content = message.get("content")
    if isinstance(content, str):
        blocks: list[Any] = [{"type": "text", "text": content}]
    elif isinstance(content, list) and content:
        blocks = [*content]
    else:
        return message
    last = blocks[-1]
    # Thinking blocks must be replayed exactly as received — don't decorate them.
    if not isinstance(last, dict) or last.get("type") in ("thinking", "redacted_thinking"):
        return message
    blocks[-1] = {**last, "cache_control": {"type": "ephemeral"}}
    return {**message, "content": blocks}


# --- the summarizers -------------------------------------------------------


_COMPACT_SYSTEM = """\
You are the campaign chronicler keeping a Dungeon Master's running notes. You are given the \
notes so far (possibly empty) and a transcript of the play that follows them. Rewrite the notes \
so they cover both, as though the whole stretch had been one continuous session.

Write for a DM who has to pick the scene back up with nothing else in front of them. Keep, in \
compact prose:
- where the party is and how they got there;
- what each character did that still matters — choices made, promises given, enemies earned;
- who they met, and where each of those NPCs stands with them;
- unresolved threads: open questions, dangers in motion, plans the party stated aloud;
- durable changes to the world, and to the characters' condition or possessions.

Drop the wording, the dice, and the small talk; keep the facts and the momentum. Never invent \
anything that is not in the notes or the transcript. Write continuous prose — a few short \
paragraphs, no headings and no bullet lists. Stay under 350 words as the campaign grows by \
tightening the older material rather than appending to it — the recent stretch deserves the most \
detail, the distant past the least. Output the notes themselves, nothing else.
"""

_RECAP_SYSTEM = """\
You write the "previously, on..." recap a player reads before sitting back down at the table. \
From the DM's running notes and the most recent play, write 3 to 6 sentences reminding the \
players where their story stands: what they were doing, who they were with, what is unresolved, \
and what they were about to decide.

Address the party as "you", past tense for what happened and present for where things stand. \
Only what the players themselves witnessed — no secrets they haven't learned, no dice, no \
mechanics, no advice. End on the open question in front of them. Output the recap itself, \
nothing else.
"""


async def _utility_text(
    system: str,
    user: str,
    *,
    max_tokens: int,
    client: anthropic.AsyncAnthropic | None = None,
    model: str | None = None,
) -> str:
    """One cheap non-streaming Haiku call returning plain prose."""
    settings = get_settings()
    client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)
    message = await client.messages.create(
        model=model or settings.utility_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in message.content if getattr(b, "type", None) == "text").strip()


async def summarize_history(
    previous: str,
    transcript: str,
    *,
    client: anthropic.AsyncAnthropic | None = None,
    model: str | None = None,
) -> str:
    """The updated running "story so far" covering `previous` plus `transcript`.

    Returns "" if there was nothing to fold in — the caller then leaves the
    context alone rather than advancing its pointer past unsummarized turns.
    """
    if not transcript.strip():
        return ""
    notes = previous.strip() or "(none yet — this is the start of the campaign)"
    return await _utility_text(
        _COMPACT_SYSTEM,
        f"NOTES SO FAR:\n{notes}\n\nPLAY SINCE THOSE NOTES:\n{transcript}",
        max_tokens=1024,
        client=client,
        model=model,
    )


async def build_recap(
    history: list[Message],
    context: dict[str, Any] | None = None,
    *,
    recent_turns: int = RECAP_RECENT_TURNS,
    client: anthropic.AsyncAnthropic | None = None,
    model: str | None = None,
) -> str:
    """A short player-facing "previously, on..." for a session, or "" if nothing
    has been played yet. Reads the compaction summary for the distant past and
    the tail of the history for the recent scenes, so its cost is bounded no
    matter how long the campaign runs."""
    starts = turn_starts(history)
    if not starts:
        return ""
    tail_from = starts[-recent_turns] if len(starts) > recent_turns else starts[0]
    notes = str((context or {}).get("summary") or "").strip()
    transcript = render_transcript(history[tail_from:])
    if not transcript.strip():
        return ""
    parts = []
    if notes:
        parts.append(f"EARLIER IN THE CAMPAIGN (the DM's notes):\n{notes}")
    parts.append(f"MOST RECENT PLAY:\n{transcript}")
    return await _utility_text(
        _RECAP_SYSTEM, "\n\n".join(parts), max_tokens=512, client=client, model=model
    )
