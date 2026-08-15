"""Story guide (M2c): entity resolution + director's-notes formatting (pure),
plus DB-backed beat selection and the update_story_progress tool.

The Opus extraction itself isn't exercised here (needs a live key); these cover
the plumbing around it. DB tests use the `campaign` fixture and skip if Postgres
is down.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from dm_agent.db import StoryBeat, db_session
from dm_agent.events import StateUpdate
from dm_agent.knowledge.story import (
    BeatView,
    active_and_upcoming,
    format_directors_notes,
    resolve_entities,
)
from dm_agent.tools.base import ToolContext
from dm_agent.tools.story import _update_story_progress


# --- pure --------------------------------------------------------------------


def test_resolve_entities_keeps_known_normalized_deduped():
    known = {"duke-aldric", "vane-hall"}
    got = resolve_entities(["Duke Aldric", "duke-aldric", "unknown-entity"], known)
    # normalized to a slug, kept only if known, de-duplicated, first-seen order
    assert got == ["duke-aldric"]


def test_format_directors_notes_empty_is_blank():
    assert format_directors_notes([]) == ""


def test_format_directors_notes_marks_active_and_names_tool():
    bid = uuid.uuid4()
    notes = format_directors_notes(
        [
            BeatView(bid, 1, "The Banquet", "Maren accuses the Duke.", "", "when they arrive", "active"),
            BeatView(uuid.uuid4(), 2, "The Barrow", "The climax below.", "", "if breached", "upcoming"),
        ]
    )
    assert "ACTIVE NOW" in notes and "upcoming" in notes
    assert str(bid) in notes  # the model needs the id to call the tool
    assert "The Banquet" in notes and "when they arrive" in notes
    assert "update_story_progress" in notes
    # framed as advisory, never binding
    assert "never force" in notes.lower() or "advisory" in notes.lower()


# --- DB-backed ---------------------------------------------------------------


async def _add_beat(campaign_id, order, title, status):
    async with db_session() as s:
        beat = StoryBeat(
            campaign_id=campaign_id,
            order_index=order,
            title=title,
            summary="",
            read_aloud="",
            trigger_condition="",
            entity_ids=[],
            status=status,
        )
        s.add(beat)
        await s.commit()
        await s.refresh(beat)
        return beat.id


async def test_active_and_upcoming_selects_active_plus_next(campaign):
    _, campaign_id, _ = campaign
    await _add_beat(campaign_id, 0, "Prologue", "completed")
    await _add_beat(campaign_id, 1, "Arrival", "active")
    await _add_beat(campaign_id, 2, "Banquet", "upcoming")
    await _add_beat(campaign_id, 3, "Barrow", "upcoming")
    await _add_beat(campaign_id, 4, "Aftermath", "upcoming")

    beats = await active_and_upcoming(campaign_id, upcoming=2)
    # active beat + next two upcoming, in story order; completed excluded, 4th dropped
    assert [b.title for b in beats] == ["Arrival", "Banquet", "Barrow"]
    assert beats[0].status == "active"


async def test_active_and_upcoming_empty_for_guideless_campaign(campaign):
    _, campaign_id, _ = campaign
    assert await active_and_upcoming(campaign_id) == []


async def test_update_story_progress_tool(campaign):
    _, campaign_id, session_id = campaign
    beat_id = await _add_beat(campaign_id, 0, "Arrival", "active")

    events: list = []

    async def emit(ev):
        events.append(ev)

    ctx = ToolContext(
        world_id=uuid.uuid4(), campaign_id=campaign_id, session_id=session_id, emit=emit
    )

    ok = await _update_story_progress(ctx, {"beat_id": str(beat_id), "status": "completed"})
    assert "completed" in ok and "Arrival" in ok
    assert isinstance(events[0], StateUpdate) and events[0].entity == "story"

    async with db_session() as s:
        status = (
            await s.execute(select(StoryBeat.status).where(StoryBeat.id == beat_id))
        ).scalar_one()
    assert status == "completed"

    # bad status is rejected without touching the DB
    bad = await _update_story_progress(ctx, {"beat_id": str(beat_id), "status": "nope"})
    assert bad.startswith("Error:")

    # a beat in another campaign is invisible
    other = ToolContext(
        world_id=uuid.uuid4(), campaign_id=uuid.uuid4(), session_id=session_id, emit=emit
    )
    miss = await _update_story_progress(other, {"beat_id": str(beat_id), "status": "active"})
    assert miss.startswith("Error:")
