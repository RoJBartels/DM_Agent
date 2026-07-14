"""The 'dead duke' test: canon says the Duke is alive and hosts the banquet; a
session event says he was killed. gather_context must surface BOTH the canon node
and the recent death chunk so the synthesis step can apply the recency override.

Integration test — uses the real Postgres (skips if unreachable) with a fake,
torch-free embedder.
"""

from dm_agent.db import DynamicChunk, Edge, Node, db_session
from dm_agent.knowledge.retrieval import gather_context


async def _seed(campaign_id, session_id, embedder):
    async with db_session() as s:
        s.add(
            Node(
                campaign_id=campaign_id,
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
                campaign_id=campaign_id,
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
                campaign_id=campaign_id,
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
    campaign_id, session_id = campaign
    await _seed(campaign_id, session_id, fake_embedder)

    async with db_session() as s:
        ctx = await gather_context(
            s,
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
    campaign_id, session_id = campaign
    # nothing seeded → empty campaign
    async with db_session() as s:
        ctx = await gather_context(
            s, campaign_id, "Who is the Archmage of Nowhere?", ["Archmage"], fake_embedder
        )
    assert ctx.is_empty()
