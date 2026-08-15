"""Management API (M2b, M2i) CRUD + validation. Hits the real ASGI app against
the real Postgres (skips if unreachable); each test cleans up its own campaigns.

The lore *build* itself isn't exercised here (it needs torch + a live API key);
these cover the routing, validation, and persistence around it — including M2i's
world container and the edit/delete routes that closed the "upload over the top
is the only edit, and nothing can be deleted" gap.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text

from dm_agent.db import (
    Campaign,
    Character,
    Edge,
    EventLog,
    GameSession,
    Node,
    StoryBeat,
    World,
    db_session,
)
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
    """(AsyncClient, created_ids, world_id) — a throwaway world to hang test
    campaigns off, plus a list to register campaign ids in so they get torn down
    (characters + story + sessions + campaign) after the test. The world and its
    lore go last."""
    if not await _db_up():
        pytest.skip("Postgres not reachable")
    created: list[uuid.UUID] = []
    world_id = uuid.uuid4()
    async with db_session() as s:
        s.add(World(id=world_id, name=f"test-world-{world_id}"))
        await s.commit()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, created, world_id
    async with db_session() as s:
        for cid in created:
            await s.execute(delete(Character).where(Character.campaign_id == cid))
            await s.execute(delete(StoryBeat).where(StoryBeat.campaign_id == cid))
            # EventLog references sessions (FK) — delete it before the sessions.
            sess_ids = (
                await s.execute(select(GameSession.id).where(GameSession.campaign_id == cid))
            ).scalars().all()
            if sess_ids:
                await s.execute(delete(EventLog).where(EventLog.session_id.in_(sess_ids)))
            await s.execute(delete(GameSession).where(GameSession.campaign_id == cid))
            await s.execute(delete(Campaign).where(Campaign.id == cid))
        await s.execute(delete(Edge).where(Edge.world_id == world_id))
        await s.execute(delete(Node).where(Node.world_id == world_id))
        await s.execute(delete(World).where(World.id == world_id))
        await s.commit()


async def test_campaign_and_character_crud(client):
    c, created, world_id = client

    r = await c.post("/api/campaigns", json={"name": "Test Realm", "world_id": str(world_id)})
    assert r.status_code == 201
    camp = r.json()
    created.append(uuid.UUID(camp["id"]))
    assert camp["name"] == "Test Realm"
    assert camp["has_world"] is False
    assert camp["has_story"] is False
    assert camp["has_history"] is False
    assert camp["settings"] == {}
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


async def test_campaign_settings_patch(client):
    """M2g: the auto-resolve toggle round-trips through PATCH and the list, and a
    rename leaves settings untouched when not sent."""
    c, created, world_id = client
    r = await c.post("/api/campaigns", json={"name": "Settings Realm", "world_id": str(world_id)})
    cid = r.json()["id"]
    created.append(uuid.UUID(cid))
    assert r.json()["settings"] == {}

    r = await c.patch(f"/api/campaigns/{cid}", json={"settings": {"auto_resolve_simple": True}})
    assert r.status_code == 200
    assert r.json()["settings"]["auto_resolve_simple"] is True

    row = next(x for x in (await c.get("/api/campaigns")).json() if x["id"] == cid)
    assert row["settings"]["auto_resolve_simple"] is True

    # rename only — settings must survive an unset field
    r = await c.patch(f"/api/campaigns/{cid}", json={"name": "Renamed Realm"})
    assert r.json()["name"] == "Renamed Realm"
    assert r.json()["settings"]["auto_resolve_simple"] is True

    assert (await c.patch(f"/api/campaigns/{uuid.uuid4()}", json={"name": "x"})).status_code == 404


async def test_lore_nodes_listing(client):
    """M2f: the creator's world-aligned pick-lists read lore nodes by type — now
    the campaign's *world's* nodes (M2i), reached from the campaign."""
    c, created, world_id = client
    r = await c.post("/api/campaigns", json={"name": "Lore Realm", "world_id": str(world_id)})
    cid = uuid.UUID(r.json()["id"])
    created.append(cid)

    # No lore built yet → empty list, not an error.
    assert (await c.get(f"/api/campaigns/{cid}/lore-nodes")).json() == []

    async with db_session() as s:
        s.add_all(
            [
                Node(world_id=world_id, id="order-x", type="Faction", name="Order of X", props={}, prose=""),
                Node(world_id=world_id, id="ravenkeep", type="Location", name="Ravenkeep", props={}, prose=""),
                Node(world_id=world_id, id="duke", type="Character", name="Duke Aldric", props={}, prose=""),
            ]
        )
        await s.commit()

    all_nodes = (await c.get(f"/api/campaigns/{cid}/lore-nodes")).json()
    assert {n["type"] for n in all_nodes} == {"Faction", "Location", "Character"}
    assert all(set(n) == {"id", "type", "name"} for n in all_nodes)

    filtered = (
        await c.get(f"/api/campaigns/{cid}/lore-nodes?types=Faction,Location")
    ).json()
    assert {n["name"] for n in filtered} == {"Order of X", "Ravenkeep"}

    assert (await c.get(f"/api/campaigns/{uuid.uuid4()}/lore-nodes")).status_code == 404


async def test_unknown_campaign_404(client):
    c, _, world_id = client
    missing = uuid.uuid4()
    assert (await c.get(f"/api/campaigns/{missing}/characters")).status_code == 404
    assert (await c.post(f"/api/campaigns/{missing}/session")).status_code == 404


async def test_lore_upload_validation(client):
    """Lore is uploaded to a WORLD (M2i), not a campaign."""
    c, _, world_id = client
    # whitespace-only doc -> 422 (nothing to build)
    assert (
        await c.post(f"/api/worlds/{world_id}/lore", json={"documents": ["   "]})
    ).status_code == 422
    # empty list -> 422 (pydantic min_length)
    assert (
        await c.post(f"/api/worlds/{world_id}/lore", json={"documents": []})
    ).status_code == 422
    # unknown world -> 404
    assert (
        await c.post(f"/api/worlds/{uuid.uuid4()}/lore", json={"documents": ["x"]})
    ).status_code == 404


async def test_story_upload_validation(client):
    c, created, world_id = client
    r = await c.post("/api/campaigns", json={"name": "Guideless Realm", "world_id": str(world_id)})
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
    c, _, world_id = client
    assert (await c.get("/api/world-jobs/does-not-exist")).status_code == 404
    assert (await c.get("/api/story-jobs/does-not-exist")).status_code == 404


async def test_transcript_replay_and_history_flag(client):
    """M2d: a session's prior play is replayable, and has_history flips once a
    campaign has been played (so the start menu can offer Continue)."""
    c, created, world_id = client
    r = await c.post("/api/campaigns", json={"name": "Replay Realm", "world_id": str(world_id)})
    cid = uuid.UUID(r.json()["id"])
    created.append(cid)

    # A fresh, never-played campaign: has_history is False.
    row = next(x for x in (await c.get("/api/campaigns")).json() if x["id"] == str(cid))
    assert row["has_history"] is False

    r = await c.post(f"/api/campaigns/{cid}/session")
    sid = uuid.UUID(r.json()["session_id"])

    # Seed one played turn directly: history (player + narration) + typed events.
    async with db_session() as s:
        sess = await s.get(GameSession, sid)
        sess.history = [
            {"role": "user", "content": "I open the door."},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "The door creaks open."},
                    {"type": "tool_use", "id": "t1", "name": "roll_dice", "input": {"expression": "d20"}},
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "You spot a rune."}]},
        ]
        s.add(EventLog(session_id=sid, event={"type": "turn_start", "turn_id": "turn-0"}))
        s.add(
            EventLog(
                session_id=sid,
                event={
                    "type": "dice_roll",
                    "expression": "d20",
                    "rolls": [14],
                    "total": 14,
                    "purpose": "perception",
                },
            )
        )
        s.add(EventLog(session_id=sid, event={"type": "turn_end", "turn_id": "turn-0"}))
        await s.commit()

    # Now the campaign reads as played.
    row = next(x for x in (await c.get("/api/campaigns")).json() if x["id"] == str(cid))
    assert row["has_history"] is True

    items = (await c.get(f"/api/sessions/{sid}/transcript")).json()
    assert [it["kind"] for it in items] == ["player", "narration", "narration", "dice"]
    assert items[0]["text"] == "I open the door."
    assert items[3]["total"] == 14

    # Unknown session -> 404.
    assert (await c.get(f"/api/sessions/{uuid.uuid4()}/transcript")).status_code == 404


async def test_recap_is_empty_before_play_and_cached_after(client, monkeypatch):
    """M2k: the catch-up recap. Costs nothing for a campaign nobody has played,
    and is generated once per state of the history, not once per menu open."""
    c, created, world_id = client
    r = await c.post("/api/campaigns", json={"name": "Recap Reach", "world_id": str(world_id)})
    cid = uuid.UUID(r.json()["id"])
    created.append(cid)

    calls: list[int] = []

    async def fake_recap(history, context=None, **kw):
        calls.append(len(history))
        return "Previously: you burned a barn."

    monkeypatch.setattr("dm_agent.api.management.build_recap", fake_recap)

    # Never played: no session exists, so no session is created and no LLM is called.
    r = await c.get(f"/api/campaigns/{cid}/recap")
    assert r.json() == {"text": "", "turns": 0}
    assert calls == []

    r = await c.post(f"/api/campaigns/{cid}/session")
    sid = uuid.UUID(r.json()["session_id"])
    async with db_session() as s:
        sess = await s.get(GameSession, sid)
        sess.history = [
            {"role": "user", "content": "I open the door."},
            {"role": "assistant", "content": [{"type": "text", "text": "It creaks open."}]},
        ]
        await s.commit()

    assert (await c.get(f"/api/campaigns/{cid}/recap")).json() == {
        "text": "Previously: you burned a barn.",
        "turns": 1,
    }
    # Second open: served from the cached recap on the session's context blob.
    assert (await c.get(f"/api/campaigns/{cid}/recap")).json()["text"].startswith("Previously")
    assert calls == [2]

    assert (await c.get(f"/api/campaigns/{uuid.uuid4()}/recap")).status_code == 404


# --- M2i: worlds as the container, and the content studio -------------------


async def test_worlds_isolate_campaigns(client):
    """The structural claim of M2i: two unrelated worlds never mix. Picking a
    world decides which campaigns you can see."""
    c, created, world_id = client

    r = await c.post("/api/worlds", json={"name": "Sundered Reach", "description": "A test world"})
    assert r.status_code == 201
    other = r.json()
    assert other["campaign_count"] == 0 and other["node_count"] == 0
    # The M8 anchor ships inert: stored and surfaced, meaning "the 5e default".
    assert other["ruleset_id"] is None

    try:
        here = (
            await c.post("/api/campaigns", json={"name": "Here", "world_id": str(world_id)})
        ).json()
        created.append(uuid.UUID(here["id"]))
        there = (
            await c.post("/api/campaigns", json={"name": "There", "world_id": other["id"]})
        ).json()

        assert here["world_id"] == str(world_id)

        mine = await c.get(f"/api/campaigns?world_id={world_id}")
        assert [x["name"] for x in mine.json()] == ["Here"]
        theirs = await c.get(f"/api/campaigns?world_id={other['id']}")
        assert [x["name"] for x in theirs.json()] == ["There"]

        # Unfiltered still lists everything, so the menu can count worlds.
        assert {"Here", "There"} <= {x["name"] for x in (await c.get("/api/campaigns")).json()}

        row = next(w for w in (await c.get("/api/worlds")).json() if w["id"] == other["id"])
        assert row["campaign_count"] == 1

        # A world with campaigns in it refuses to be deleted...
        r = await c.delete(f"/api/worlds/{other['id']}")
        assert r.status_code == 409
        assert "campaign" in r.json()["detail"]

        # ...and goes quietly once they're gone.
        assert (await c.delete(f"/api/campaigns/{there['id']}")).status_code == 204
        assert (await c.delete(f"/api/worlds/{other['id']}")).status_code == 204
        assert all(w["id"] != other["id"] for w in (await c.get("/api/worlds")).json())
    finally:
        async with db_session() as s:
            await s.execute(delete(Campaign).where(Campaign.world_id == uuid.UUID(other["id"])))
            await s.execute(delete(World).where(World.id == uuid.UUID(other["id"])))
            await s.commit()

    assert (await c.post("/api/campaigns", json={"name": "Orphan", "world_id": str(uuid.uuid4())})
            ).status_code == 404


async def test_campaign_creation_requires_a_world(client):
    c, _, _ = client
    assert (await c.post("/api/campaigns", json={"name": "No World"})).status_code == 422


async def test_graph_and_node_editing(client, monkeypatch):
    """The content studio: read the graph, edit an entity in place, delete one and
    take its edges with it. Editing prose re-embeds, since the vector describes it."""
    c, _, world_id = client

    embedded: list[str] = []

    class _Fake:
        dimension = 384

        def embed_one(self, text):
            embedded.append(text)
            return [0.0] * 384

    monkeypatch.setattr("dm_agent.knowledge.embeddings.get_embedder", lambda: _Fake())

    async with db_session() as s:
        s.add_all(
            [
                Node(world_id=world_id, id="duke", type="Character", name="Duke Aldric",
                     props={}, prose="He rules the moor."),
                Node(world_id=world_id, id="hall", type="Location", name="Vane Hall",
                     props={}, prose="A draughty seat."),
                Edge(world_id=world_id, src="duke", dst="hall", type="LOCATED_IN", props={}),
            ]
        )
        await s.commit()

    graph = (await c.get(f"/api/worlds/{world_id}/graph")).json()
    assert {n["id"] for n in graph["nodes"]} == {"duke", "hall"}
    assert graph["edges"][0]["src"] == "duke"

    # Rename + rewrite: the slug is the join key, so it is not editable.
    r = await c.patch(
        f"/api/worlds/{world_id}/nodes/duke",
        json={"name": "Duke Aldric Vane", "prose": "He ruled the moor until the assassin came."},
    )
    assert r.status_code == 200
    assert r.json()["id"] == "duke"
    assert r.json()["name"] == "Duke Aldric Vane"
    assert embedded == ["He ruled the moor until the assassin came."]

    # An edit that doesn't touch prose doesn't pay to re-embed.
    await c.patch(f"/api/worlds/{world_id}/nodes/duke", json={"type": "Deity"})
    assert len(embedded) == 1

    assert (await c.delete(f"/api/worlds/{world_id}/nodes/duke")).status_code == 204
    graph = (await c.get(f"/api/worlds/{world_id}/graph")).json()
    assert {n["id"] for n in graph["nodes"]} == {"hall"}
    assert graph["edges"] == []  # the dangling edge went with it

    assert (await c.delete(f"/api/worlds/{world_id}/nodes/duke")).status_code == 404
    assert (await c.get(f"/api/worlds/{uuid.uuid4()}/graph")).status_code == 404


async def test_delete_lore_keeps_the_world_and_its_campaigns(client):
    """Re-ingesting from scratch must not cost you the campaigns played there."""
    c, created, world_id = client
    camp = (
        await c.post("/api/campaigns", json={"name": "Survivor", "world_id": str(world_id)})
    ).json()
    created.append(uuid.UUID(camp["id"]))

    async with db_session() as s:
        s.add(Node(world_id=world_id, id="keep", type="Location", name="Keep", props={}, prose=""))
        s.add(Edge(world_id=world_id, src="keep", dst="keep", type="RULES", props={}))
        await s.commit()

    row = next(x for x in (await c.get("/api/campaigns")).json() if x["id"] == camp["id"])
    assert row["has_world"] is True  # inherited from the world, not the campaign

    r = await c.delete(f"/api/worlds/{world_id}/lore")
    assert r.json() == {"nodes": 1, "edges": 1, "summaries": 0}

    assert (await c.get(f"/api/worlds/{world_id}/graph")).json() == {"nodes": [], "edges": []}
    row = next(x for x in (await c.get("/api/campaigns")).json() if x["id"] == camp["id"])
    assert row["has_world"] is False


async def test_story_beat_editing(client):
    """Beats are advisory, so they are freely editable — including status, which
    play would otherwise be the only way to change."""
    c, created, world_id = client
    camp = (
        await c.post("/api/campaigns", json={"name": "Guided", "world_id": str(world_id)})
    ).json()
    cid = uuid.UUID(camp["id"])
    created.append(cid)

    async with db_session() as s:
        s.add_all(
            [
                StoryBeat(campaign_id=cid, order_index=0, title="Arrival", status="active"),
                StoryBeat(campaign_id=cid, order_index=1, title="Banquet", status="upcoming"),
            ]
        )
        await s.commit()

    beats = (await c.get(f"/api/campaigns/{cid}/story")).json()
    assert [b["title"] for b in beats] == ["Arrival", "Banquet"]

    r = await c.patch(
        f"/api/story-beats/{beats[0]['id']}",
        json={"title": "Arrival at Vane Hall", "status": "completed"},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Arrival at Vane Hall"
    assert r.json()["status"] == "completed"
    assert r.json()["order_index"] == 0  # untouched fields survive

    assert (
        await c.patch(f"/api/story-beats/{beats[0]['id']}", json={"status": "nonsense"})
    ).status_code == 422

    assert (await c.delete(f"/api/story-beats/{beats[1]['id']}")).status_code == 204
    assert len((await c.get(f"/api/campaigns/{cid}/story")).json()) == 1

    assert (await c.delete(f"/api/campaigns/{cid}/story")).json() == {"beats": 1}
    assert (await c.get(f"/api/campaigns/{cid}/story")).json() == []


async def test_deleting_a_campaign_spares_the_world(client):
    """A campaign takes its own play with it and nothing else — the setting and
    any sibling campaign's canon survive."""
    c, created, world_id = client
    doomed = (
        await c.post("/api/campaigns", json={"name": "Doomed", "world_id": str(world_id)})
    ).json()
    cid = uuid.UUID(doomed["id"])
    keeper = (
        await c.post("/api/campaigns", json={"name": "Keeper", "world_id": str(world_id)})
    ).json()
    created.append(uuid.UUID(keeper["id"]))

    await c.post(f"/api/campaigns/{cid}/characters", json={"name": "Doomed Hero"})
    sid = uuid.UUID((await c.post(f"/api/campaigns/{cid}/session")).json()["session_id"])
    async with db_session() as s:
        s.add(Node(world_id=world_id, id="shared", type="Location", name="Shared", props={}, prose=""))
        s.add(StoryBeat(campaign_id=cid, order_index=0, title="Beat", status="active"))
        s.add(EventLog(session_id=sid, event={"type": "turn_start", "turn_id": "t0"}))
        await s.commit()

    assert (await c.delete(f"/api/campaigns/{cid}")).status_code == 204
    assert (await c.get(f"/api/campaigns/{cid}/characters")).status_code == 404
    assert (await c.get(f"/api/sessions/{sid}/transcript")).status_code == 404

    # The world's canon and the sibling campaign are untouched.
    assert {n["id"] for n in (await c.get(f"/api/worlds/{world_id}/graph")).json()["nodes"]} == {
        "shared"
    }
    assert any(x["id"] == keeper["id"] for x in (await c.get("/api/campaigns")).json())

    assert (await c.delete(f"/api/campaigns/{uuid.uuid4()}")).status_code == 404


async def test_world_rename(client):
    c, _, world_id = client
    r = await c.patch(f"/api/worlds/{world_id}", json={"description": "  Now described.  "})
    assert r.status_code == 200
    assert r.json()["description"] == "Now described."
    assert r.json()["name"].startswith("test-world-")  # unset fields survive
    assert (await c.patch(f"/api/worlds/{uuid.uuid4()}", json={"name": "x"})).status_code == 404
