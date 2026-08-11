"""M2g: the auto-resolve softening is injected into the turn context only when a
campaign has opted in — off by default. DB-backed (skips if Postgres is down)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import delete, text

from dm_agent.db import Campaign, db_session
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
        for cid in ids:
            await s.execute(delete(Campaign).where(Campaign.id == cid))
        await s.commit()


async def _make_campaign(settings: dict | None):
    async with db_session() as s:
        c = Campaign(name="Prefs Test", settings=settings or {})
        s.add(c)
        await s.flush()
        cid = c.id
        await s.commit()
    return cid


def _orch() -> Orchestrator:
    # _auto_resolve_note only reads the DB — skip __init__ so the test needs no API key.
    return object.__new__(Orchestrator)


async def test_note_absent_by_default(campaign_ids):
    cid = await _make_campaign(None)
    campaign_ids.append(cid)
    assert await _orch()._auto_resolve_note(cid) == ""


async def test_note_absent_when_flag_false(campaign_ids):
    cid = await _make_campaign({"auto_resolve_simple": False})
    campaign_ids.append(cid)
    assert await _orch()._auto_resolve_note(cid) == ""


async def test_note_present_when_opted_in(campaign_ids):
    cid = await _make_campaign({"auto_resolve_simple": True})
    campaign_ids.append(cid)
    assert await _orch()._auto_resolve_note(cid) == AUTO_RESOLVE_NOTE
