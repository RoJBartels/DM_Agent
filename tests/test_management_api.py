"""Management API (M2b) CRUD + validation. Hits the real ASGI app against the
real Postgres (skips if unreachable); each test cleans up its own campaigns.

The world *build* itself isn't exercised here (it needs torch + a live API key);
these cover the routing, validation, and persistence around it.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text

from dm_agent.db import Campaign, Character, GameSession, StoryBeat, db_session
from dm_agent.main import app


async def _db_up() -> bool:
    try:
        async with db_session() as s:
            await s.execute(text("SELECT 1"))
        return True
    except Exception:  # pragma: no cover - environment guard
        return False


@pytest_asyncio.fixture
async def client():
    """(AsyncClient, created_ids) — register campaign ids in the list to have them
    torn down (characters + sessions + campaign) after the test."""
    if not await _db_up():
        pytest.skip("Postgres not reachable")
    created: list[uuid.UUID] = []
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, created
    async with db_session() as s:
        for cid in created:
            await s.execute(delete(Character).where(Character.campaign_id == cid))
            await s.execute(delete(StoryBeat).where(StoryBeat.campaign_id == cid))
            await s.execute(delete(GameSession).where(GameSession.campaign_id == cid))
            await s.execute(delete(Campaign).where(Campaign.id == cid))
        await s.commit()


async def test_campaign_and_character_crud(client):
    c, created = client

    r = await c.post("/api/campaigns", json={"name": "Test Realm"})
    assert r.status_code == 201
    camp = r.json()
    created.append(uuid.UUID(camp["id"]))
    assert camp["name"] == "Test Realm"
    assert camp["has_world"] is False
    assert camp["has_story"] is False
    assert camp["character_count"] == 0
    cid = camp["id"]

    # appears in the list
    r = await c.get("/api/campaigns")
    assert any(x["id"] == cid for x in r.json())

    # session get-or-create is idempotent
    r = await c.post(f"/api/campaigns/{cid}/session")
    assert r.status_code == 200
    sess = r.json()
    assert sess["campaign_id"] == cid
    r2 = await c.post(f"/api/campaigns/{cid}/session")
    assert r2.json()["session_id"] == sess["session_id"]

    # create a character with a homebrew stat key that must survive edits
    body = {
        "name": "Borin",
        "stats": {"STR": 16, "class": "fighter", "level": 2, "homebrew": "keep-me"},
        "max_hp": 20, "hp": 18, "ac": 16,
        "inventory": ["axe"], "notes": "gruff",
    }
    r = await c.post(f"/api/campaigns/{cid}/characters", json=body)
    assert r.status_code == 201
    ch = r.json()
    assert ch["name"] == "Borin"
    assert ch["stats"]["homebrew"] == "keep-me"
    char_id = ch["id"]

    r = await c.get(f"/api/campaigns/{cid}/characters")
    assert len(r.json()) == 1

    # campaign count reflects it
    r = await c.get("/api/campaigns")
    row = next(x for x in r.json() if x["id"] == cid)
    assert row["character_count"] == 1

    # PATCH updates only what it carries; unknown stat keys are untouched
    r = await c.patch(f"/api/characters/{char_id}", json={"hp": 5})
    assert r.status_code == 200
    patched = r.json()
    assert patched["hp"] == 5
    assert patched["name"] == "Borin"
    assert patched["stats"]["homebrew"] == "keep-me"

    r = await c.delete(f"/api/characters/{char_id}")
    assert r.status_code == 204
    r = await c.get(f"/api/campaigns/{cid}/characters")
    assert r.json() == []


async def test_unknown_campaign_404(client):
    c, _ = client
    missing = uuid.uuid4()
    assert (await c.get(f"/api/campaigns/{missing}/characters")).status_code == 404
    assert (await c.post(f"/api/campaigns/{missing}/session")).status_code == 404


async def test_world_upload_validation(client):
    c, created = client
    r = await c.post("/api/campaigns", json={"name": "Empty Realm"})
    created.append(uuid.UUID(r.json()["id"]))
    cid = r.json()["id"]
    # whitespace-only doc -> 422 (nothing to build)
    assert (
        await c.post(f"/api/campaigns/{cid}/world", json={"documents": ["   "]})
    ).status_code == 422
    # empty list -> 422 (pydantic min_length)
    assert (
        await c.post(f"/api/campaigns/{cid}/world", json={"documents": []})
    ).status_code == 422


async def test_story_upload_validation(client):
    c, created = client
    r = await c.post("/api/campaigns", json={"name": "Guideless Realm"})
    created.append(uuid.UUID(r.json()["id"]))
    cid = r.json()["id"]
    # whitespace-only doc -> 422 (nothing to build)
    assert (
        await c.post(f"/api/campaigns/{cid}/story", json={"documents": ["   "]})
    ).status_code == 422
    # empty list -> 422 (pydantic min_length)
    assert (
        await c.post(f"/api/campaigns/{cid}/story", json={"documents": []})
    ).status_code == 422
    # unknown campaign -> 404
    assert (
        await c.post(f"/api/campaigns/{uuid.uuid4()}/story", json={"documents": ["x"]})
    ).status_code == 404


async def test_unknown_job_404(client):
    c, _ = client
    assert (await c.get("/api/world-jobs/does-not-exist")).status_code == 404
    assert (await c.get("/api/story-jobs/does-not-exist")).status_code == 404
