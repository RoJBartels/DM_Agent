"""M2g: the auto-resolve softening is injected into the turn context only when a
campaign has opted in — off by default. DB-backed (skips if Postgres is down)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import delete, text

from dm_agent.db import Campaign, World, db_session
from dm_agent.orchestrator.loop import AUTO_RESOLVE_NOTE, Orchestrator


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
    """A throwaway campaign in a throwaway world — a campaign always has one."""
    async with db_session() as s:
        w = World(name="Prefs Test World")
        s.add(w)
        await s.flush()
        c = Campaign(name="Prefs Test", world_id=w.id, settings=settings or {})
        s.add(c)
        await s.flush()
        ids = (c.id, w.id)
        await s.commit()
    return ids


def _orch() -> Orchestrator:
    # _auto_resolve_note only reads the DB — skip __init__ so the test needs no API key.
    return object.__new__(Orchestrator)


async def test_note_absent_by_default(campaign_ids):
    ids = await _make_campaign(None)
    cid = ids[0]
    campaign_ids.append(ids)
    assert await _orch()._auto_resolve_note(cid) == ""


async def test_note_absent_when_flag_false(campaign_ids):
    ids = await _make_campaign({"auto_resolve_simple": False})
    cid = ids[0]
    campaign_ids.append(ids)
    assert await _orch()._auto_resolve_note(cid) == ""


async def test_note_present_when_opted_in(campaign_ids):
    ids = await _make_campaign({"auto_resolve_simple": True})
    cid = ids[0]
    campaign_ids.append(ids)
    assert await _orch()._auto_resolve_note(cid) == AUTO_RESOLVE_NOTE
