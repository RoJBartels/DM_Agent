"""The 'dead duke' test: canon says the Duke is alive and hosts the banquet; a
session event says he was killed. gather_context must surface BOTH the canon node
and the recent death chunk so the synthesis step can apply the recency override.

Integration test — uses the real Postgres (skips if unreachable) with a fake,
torch-free embedder.
"""

import uuid

from sqlalchemy import delete

from dm_agent.db import Campaign, DynamicChunk, Edge, Node, db_session
from dm_agent.knowledge.retrieval import gather_context


async def _seed(world_id, campaign_id, session_id, embedder):
    async with db_session() as s:
        s.add(
            Node(
                world_id=world_id,
                id="duke-aldric",
                type="Character",
                name="Duke Aldric Vane",
                props={"status": "alive"},
                prose="Duke Aldric Vane rules the moor and hosts the Harvest Banquet each autumn.",
                embedding=embedder.embed_one("duke aldric hosts the harvest banquet"),
            )
        )
        s.add(
            Node(
                world_id=world_id,
                id="harvest-banquet",
                type="Event",
                name="Harvest Banquet",
                props={},
                prose="The Harvest Banquet is held in Vane Hall; every noble house attends.",
                embedding=embedder.embed_one("harvest banquet vane hall host"),
            )
        )
        s.add(
            Edge(
                world_id=world_id,
                src="harvest-banquet",
                dst="duke-aldric",
                type="HOSTED_BY",
                props={},
            )
        )
        s.add(
            DynamicChunk(
                session_id=session_id,
                campaign_id=campaign_id,
                text="Duke Aldric was killed by an assassin the night before the banquet.",
                entity_ids=["duke-aldric"],
                embedding=embedder.embed_one("duke aldric killed dead assassin"),
            )
        )
        await s.commit()


async def test_recency_override_surfaces_death(campaign, fake_embedder):
    world_id, campaign_id, session_id = campaign
    await _seed(world_id, campaign_id, session_id, fake_embedder)

    async with db_session() as s:
        ctx = await gather_context(
            s,
            world_id,
            campaign_id,
            question="Who hosts the Harvest Banquet?",
            entity_names=["Duke Aldric", "Harvest Banquet"],
            embedder=fake_embedder,
        )

    # canon entity resolved
    assert "duke-aldric" in ctx.source_ids
    # the recent death event was pulled in via the neighborhood (banquet -> duke)
    dynamic_texts = " ".join(h.text for h in ctx.dynamic_hits)
    assert "killed" in dynamic_texts
    # canon prose is also present, so synthesis sees the conflict to resolve
    node_ids = {h.id for h in ctx.node_hits}
    assert "duke-aldric" in node_ids or "harvest-banquet" in node_ids


async def test_unknown_entity_returns_empty_context(campaign, fake_embedder):
    world_id, campaign_id, session_id = campaign
    # nothing seeded → empty world
    async with db_session() as s:
        ctx = await gather_context(
            s, world_id, campaign_id, "Who is the Archmage of Nowhere?", ["Archmage"], fake_embedder
        )
    assert ctx.is_empty()


async def test_canon_is_shared_by_world_but_play_is_not(campaign, fake_embedder):
    """M2i's isolation claim, at the layer that matters: a second campaign in the
    same world inherits its canon, but never sees the first campaign's play."""
    world_id, campaign_id, session_id = campaign
    await _seed(world_id, campaign_id, session_id, fake_embedder)

    sibling_id = uuid.uuid4()
    async with db_session() as s:
        s.add(Campaign(id=sibling_id, world_id=world_id, name=f"sibling-{sibling_id}"))
        await s.commit()

    try:
        async with db_session() as s:
            ctx = await gather_context(
                s,
                world_id,
                sibling_id,
                question="Who hosts the Harvest Banquet?",
                entity_names=["Duke Aldric", "Harvest Banquet"],
                embedder=fake_embedder,
            )
        # Same setting: the Duke and his banquet are canon here too.
        assert "duke-aldric" in ctx.source_ids
        # Different playthrough: the other party's assassination never happened here.
        assert ctx.dynamic_hits == []
    finally:
        async with db_session() as s:
            await s.execute(delete(Campaign).where(Campaign.id == sibling_id))
            await s.commit()
