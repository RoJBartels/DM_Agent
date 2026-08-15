"""Story / adventure guide (M2c, §2b): ingestion + per-turn delivery.

A user may optionally upload a pre-written adventure (a module or their own
outline). Opus extracts it into an ordered sequence of advisory *beats* — a
different kind of bucket than the §2 lore graph: lore is declarative fact, a beat
is the author's intended arc. Beats are delivered proactively to the narrator
each turn as private "director's notes" (never enforced) and progressed via the
update_story_progress tool. A campaign with no uploaded guide has zero beats and
plays exactly as an entirely improvised campaign.

Beats are fetched by (campaign, status, order), never vector-searched, so there
is no embedding step here — this module is torch-free. It reuses M2's Opus
structured-output extraction; `entity_ids` are resolved against existing lore
nodes (same join-key pattern as dynamic_chunks) so a beat can reference a canon
entity by its slug.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

import anthropic
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from dm_agent.config import get_settings
from dm_agent.db import Campaign, Node, StoryBeat, db_session

log = logging.getLogger(__name__)

# The statuses a beat moves through. Trigger conditions, not a fixed sequence,
# decide relevance — going off-script never breaks anything, it just leaves
# beats in `upcoming` until the fiction reaches them (or the DM skips them).
BEAT_STATUSES = frozenset({"upcoming", "active", "completed", "skipped"})


# --- extraction ------------------------------------------------------------


class ExtractedBeat(BaseModel):
    title: str = Field(description="short beat name, e.g. 'The Banquet Interrupted'")
    summary: str = Field(
        description="1-3 sentences on what this beat is about and what the author "
        "intends to happen — private DM guidance, not text read to players."
    )
    read_aloud: str = Field(
        default="",
        description="optional boxed/read-aloud text to speak to the players when the "
        "beat opens; empty if the adventure gives none.",
    )
    trigger_condition: str = Field(
        description="the in-fiction condition under which this beat becomes relevant "
        "(e.g. 'the party enters Vane Hall during the banquet'). Conditions, not a "
        "fixed order, so the party can reach beats out of sequence."
    )
    entity_slugs: list[str] = Field(
        default_factory=list,
        description="kebab-case slugs of known lore entities this beat involves, "
        "chosen only from the provided KNOWN ENTITIES list; omit any not in it.",
    )


class StoryExtraction(BaseModel):
    beats: list[ExtractedBeat]


_STORY_SYSTEM = """\
You convert a tabletop-RPG adventure or outline into an ordered list of advisory \
story BEATS for a Dungeon Master. A beat is a unit of intended plot — a scene, a \
turning point, a revelation — in the order the author expects them.

Rules:
- Extract beats in narrative order. Prefer a handful of meaningful beats over many \
trivial ones.
- `summary` is private guidance to the DM about what the beat is and what the author \
hoped would happen. `read_aloud` is only the text meant to be read to players, if any.
- `trigger_condition` describes WHEN the beat becomes relevant in the fiction — a \
condition, never "beat 3 must come after beat 2". The party may reach beats out of \
order or skip them entirely; that must never break the guide.
- These beats are advisory. Do NOT write anything that forces player choices; describe \
what the author intends, which the DM will offer, not impose.
- Tag each beat with `entity_slugs` drawn ONLY from the provided KNOWN ENTITIES list \
(the campaign's existing lore). Omit slugs that aren't in that list. If no list is \
given, leave it empty.
"""


async def extract_story(
    document: str,
    known_entities: dict[str, str] | None = None,
    *,
    client: anthropic.AsyncAnthropic | None = None,
    model: str | None = None,
) -> StoryExtraction:
    """Extract an ordered beat list from one adventure document.

    `known_entities` is {slug: name} of the campaign's existing lore nodes, passed
    to the model so it reuses canonical slugs; endpoints are filtered to it after.
    """
    settings = get_settings()
    client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)
    model = model or settings.narrator_model  # Opus for extraction quality

    roster = (
        "\n".join(f"- {slug} ({name})" for slug, name in (known_entities or {}).items())
        or "- (no lore graph loaded for this campaign)"
    )
    parsed = await client.messages.parse(
        model=model,
        max_tokens=8192,
        thinking={"type": "adaptive"},
        system=_STORY_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"KNOWN ENTITIES:\n{roster}\n\nADVENTURE:\n{document}",
            }
        ],
        output_format=StoryExtraction,
    )
    result = parsed.parsed_output
    if result is None:
        raise RuntimeError("story extraction returned no structured output")
    return result


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "beat"


def resolve_entities(slugs: list[str], known: set[str]) -> list[str]:
    """Keep only entity slugs that name a real lore node, de-duplicated and
    normalized. A guide may reference entities the world doesn't have; those are
    dropped so the join key stays reliable."""
    out: list[str] = []
    for raw in slugs:
        slug = _slugify(raw)
        if slug in known and slug not in out:
            out.append(slug)
    return out


async def build_story(
    campaign_id: uuid.UUID,
    documents: list[str],
    *,
    client: anthropic.AsyncAnthropic | None = None,
) -> dict[str, int]:
    """Extract the uploaded adventure into ordered beats for a campaign.

    Idempotent: wipes and rebuilds this campaign's beats. The first beat is marked
    `active` (so the narrator always has a current beat to pace toward); the rest
    start `upcoming`. A story is a campaign's arc, but the canon its beats can
    reference belongs to the campaign's *world* (M2i), so the roster comes from
    there."""
    settings = get_settings()
    client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)

    async with db_session() as session:
        rows = await session.execute(
            select(Node.id, Node.name)
            .join(Campaign, Campaign.world_id == Node.world_id)
            .where(Campaign.id == campaign_id)
        )
        known = {slug: name for slug, name in rows}

    beats: list[ExtractedBeat] = []
    for doc in documents:
        ex = await extract_story(doc, known, client=client)
        beats.extend(ex.beats)
    log.info("extracted %d story beats", len(beats))

    known_slugs = set(known)
    async with db_session() as session:
        await session.execute(
            delete(StoryBeat).where(StoryBeat.campaign_id == campaign_id)
        )
        await session.flush()
        for i, beat in enumerate(beats):
            session.add(
                StoryBeat(
                    campaign_id=campaign_id,
                    order_index=i,
                    title=beat.title.strip() or f"Beat {i + 1}",
                    summary=beat.summary.strip(),
                    read_aloud=beat.read_aloud.strip(),
                    trigger_condition=beat.trigger_condition.strip(),
                    entity_ids=resolve_entities(beat.entity_slugs, known_slugs),
                    status="active" if i == 0 else "upcoming",
                )
            )
        await session.commit()

    return {"beats": len(beats)}


# --- delivery: proactive per-turn director's notes -------------------------


@dataclass
class BeatView:
    """A detached, read-only view of a beat for formatting outside a DB session."""

    id: uuid.UUID
    order_index: int
    title: str
    summary: str
    read_aloud: str
    trigger_condition: str
    status: str


async def active_and_upcoming(
    campaign_id: uuid.UUID, upcoming: int = 2
) -> list[BeatView]:
    """The beats the narrator should see this turn: every `active` beat plus the
    next `upcoming` beats still to come, in story order. Completed/skipped beats
    are excluded. Empty when the campaign has no guide."""
    async with db_session() as session:
        rows = (
            await session.execute(
                select(StoryBeat)
                .where(StoryBeat.campaign_id == campaign_id)
                .where(StoryBeat.status.in_(("active", "upcoming")))
                .order_by(StoryBeat.order_index)
            )
        ).scalars().all()

    active = [b for b in rows if b.status == "active"]
    ahead = [b for b in rows if b.status == "upcoming"][:upcoming]
    chosen = sorted([*active, *ahead], key=lambda b: b.order_index)
    return [
        BeatView(
            id=b.id,
            order_index=b.order_index,
            title=b.title,
            summary=b.summary,
            read_aloud=b.read_aloud,
            trigger_condition=b.trigger_condition,
            status=b.status,
        )
        for b in chosen
    ]


def format_directors_notes(beats: list[BeatView]) -> str:
    """Render the current beats as a short private-notes block for injection into
    the turn's context. Empty string when there are no beats (so a campaign
    without a guide adds nothing to the prompt)."""
    if not beats:
        return ""
    lines = [
        "DIRECTOR'S NOTES — a private, advisory story guide the author uploaded. "
        "These are yours alone; the players never see them. Use them to pace the "
        "story, but never force them: the party may ignore, subvert, or outrun any "
        "beat, and you narrate around whatever they actually do.",
    ]
    for b in beats:
        marker = "ACTIVE NOW" if b.status == "active" else "upcoming"
        lines.append("")
        lines.append(f"[{marker}] beat {b.id} — {b.title}")
        if b.trigger_condition:
            lines.append(f"  triggers when: {b.trigger_condition}")
        if b.summary:
            lines.append(f"  {b.summary}")
        if b.read_aloud:
            lines.append(f'  read-aloud (optional): "{b.read_aloud}"')
    lines.append("")
    lines.append(
        "When a beat is reached, resolved, or bypassed by the players' choices, call "
        "update_story_progress(beat_id, status) to record it and advance the guide."
    )
    return "\n".join(lines)
