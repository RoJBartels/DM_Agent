"""M2e refinement: webbing a new hero in, and the party-convergence note.

Two halves. The newcomer scan and the roster marker are pure logic (no DB, no
torch). The play-mode gate reads a campaign's settings, so those tests are
DB-backed and skip when Postgres is down.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, text

from dm_agent.db import Campaign, World, db_session
from dm_agent.orchestrator.loop import (
    format_party_mode_note,
    format_party_roster,
    newcomer_ids,
)


def _char(**kw):
    base = dict(
        id=uuid.uuid4(), name="X", is_pc=True, stats={}, hp=10, max_hp=10, ac=10, notes=""
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _turn(player: str, dm: str) -> list[dict]:
    return [
        {"role": "user", "content": player},
        {"role": "assistant", "content": [{"type": "text", "text": dm}]},
    ]


# --- who has actually appeared in play (newcomer_ids) ----------------------


def test_everyone_is_a_newcomer_before_the_first_turn():
    kara, borin = _char(name="Kara"), _char(name="Borin Stonefist")
    assert newcomer_ids([kara, borin], []) == {kara.id, borin.id}


def test_a_hero_the_dm_has_narrated_is_not_a_newcomer():
    kara, borin = _char(name="Kara"), _char(name="Borin Stonefist")
    history = _turn("look around", "Kara steps into the taproom. The fire is low.")
    assert newcomer_ids([kara, borin], history) == {borin.id}


def test_acting_as_a_hero_counts_as_appearing():
    """The "As <Name>:" prefix is the player putting that hero on stage."""
    kara = _char(name="Kara")
    assert newcomer_ids([kara], _turn("As Kara: I draw my blade", "Steel rasps free.")) == set()


def test_a_surname_alone_is_enough():
    borin = _char(name="Borin Stonefist")
    history = _turn("who is that?", "Stonefist hefts his axe and grins at you.")
    assert newcomer_ids([borin], history) == set()


def test_npcs_are_never_marked():
    """The marker means "a player is waiting to play them". Where the DM's own
    NPCs stand in the fiction is the DM's business, not a convergence problem."""
    ben = _char(name="Old Ben", is_pc=False)
    assert newcomer_ids([ben], []) == set()


def test_a_name_with_no_usable_token_is_left_alone():
    """Better to skip the nudge than to guess: a two-letter name would match
    prose constantly, so it is treated as already introduced."""
    assert newcomer_ids([_char(name="Al")], []) == set()


def test_partial_words_do_not_count_as_an_appearance():
    kara = _char(name="Kara")
    history = _turn("look", "A karaoke machine would be anachronistic here.")
    assert newcomer_ids([kara], history) == {kara.id}


def test_tool_traffic_is_not_prose():
    """A name inside a tool call is machinery, not a scene the player saw."""
    kara = _char(name="Kara")
    history = [
        {"role": "user", "content": "look"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "get_character_sheet",
                 "input": {"name": "Kara"}},
            ],
        },
    ]
    assert newcomer_ids([kara], history) == {kara.id}


# --- the roster marker ------------------------------------------------------


def test_roster_marks_a_newcomer_and_explains_the_marker():
    kara, borin = _char(name="Kara"), _char(name="Borin")
    out = format_party_roster([kara, borin], {borin.id})
    assert "Kara (PC)" in out
    assert "Borin (PC, not yet in the story)" in out
    assert "never appeared in play" in out


def test_roster_without_newcomers_is_unchanged():
    """A campaign whose party is all on stage renders exactly as it did before."""
    chars = [_char(name="Kara"), _char(name="Old Ben", is_pc=False)]
    assert format_party_roster(chars, set()) == format_party_roster(chars)
    assert "not yet in the story" not in format_party_roster(chars)


# --- the convergence note ---------------------------------------------------


def test_note_allows_splitting_and_forbids_stranding():
    note = format_party_mode_note(opening=False)
    assert note.startswith("[") and note.endswith("]")
    assert "Splitting up remains the players' choice" in note
    assert "ONE member of the party" in note
    assert "opening scene" not in note


def test_transition_rule_covers_a_hero_still_waiting_to_appear():
    """The seam a live run actually broke on: told only to "account for every
    member", the narrator rode the party out of town without the hero it had not
    introduced yet, because an unintroduced hero didn't read as a member."""
    note = format_party_mode_note(opening=False)
    assert "a waiting newcomer included" in note
    assert "as though a hero on the roster did not exist" in note


def test_opening_note_gathers_the_party():
    assert "opening scene" in format_party_mode_note(opening=True)


# --- the play-mode gate (DB-backed) ----------------------------------------


async def _db_up() -> bool:
    try:
        async with db_session() as s:
            await s.execute(text("SELECT 1"))
        return True
    except Exception:  # pragma: no cover - environment guard
        return False


@pytest_asyncio.fixture
async def campaign_ids():
    if not await _db_up():
        pytest.skip("Postgres not reachable")
    ids: list = []
    yield ids
    async with db_session() as s:
        for cid, wid in ids:
            await s.execute(delete(Campaign).where(Campaign.id == cid))
            await s.execute(delete(World).where(World.id == wid))
        await s.commit()


async def _make_campaign(settings: dict | None):
    async with db_session() as s:
        w = World(name="Play Mode Test World")
        s.add(w)
        await s.flush()
        c = Campaign(name="Play Mode Test", world_id=w.id, settings=settings or {})
        s.add(c)
        await s.flush()
        ids = (c.id, w.id)
        await s.commit()
    return ids


def _orch():
    # _party_mode_note only reads the DB — skip __init__ so no API key is needed.
    from dm_agent.orchestrator.loop import Orchestrator

    return object.__new__(Orchestrator)


async def test_convergence_off_by_default(campaign_ids):
    ids = await _make_campaign(None)
    campaign_ids.append(ids)
    assert await _orch()._party_mode_note(ids[0], opening=True) == ""


async def test_convergence_off_for_a_single_player_campaign(campaign_ids):
    ids = await _make_campaign({"play_mode": "single"})
    campaign_ids.append(ids)
    assert await _orch()._party_mode_note(ids[0], opening=False) == ""


async def test_convergence_on_for_a_party_campaign(campaign_ids):
    ids = await _make_campaign({"play_mode": "multiplayer"})
    campaign_ids.append(ids)
    assert await _orch()._party_mode_note(ids[0], opening=False) == format_party_mode_note(
        opening=False
    )
    assert await _orch()._party_mode_note(ids[0], opening=True) == format_party_mode_note(
        opening=True
    )
