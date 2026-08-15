"""Shared test fixtures for the knowledge layer.

A keyword bag-of-words embedder stands in for sentence-transformers so ranking
tests are deterministic and torch-free. The `campaign` fixture provisions a
throwaway world+campaign+session in the real Postgres and skips if the DB is down.
"""

from __future__ import annotations

import math
import re
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, text

from dm_agent.db import (
    EMBED_DIM,
    Campaign,
    CommunitySummary,
    DynamicChunk,
    Edge,
    GameSession,
    Node,
    StoryBeat,
    World,
    db_session,
)

_VOCAB = [
    "duke", "aldric", "banquet", "harvest", "hall", "vane", "barrow", "moor",
    "clan", "order", "lantern", "maren", "ashcroft", "guild", "secret", "killed",
    "dead", "host", "hosts", "rival", "sealed", "town", "muireach", "ferry",
]
_INDEX = {w: i for i, w in enumerate(_VOCAB)}


class FakeEmbedder:
    """Deterministic bag-of-words embedder over a fixed vocabulary. Shared words
    → higher cosine similarity; unknown words are ignored. Vectors are unit-norm."""

    dimension = EMBED_DIM

    def _vec(self, text_: str) -> list[float]:
        vec = [0.0] * EMBED_DIM
        for tok in re.findall(r"[a-z]+", text_.lower()):
            if tok in _INDEX:
                vec[_INDEX[tok]] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            vec[EMBED_DIM - 1] = 1.0  # non-zero so cosine distance is defined
            return vec
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_one(self, text_: str) -> list[float]:
        return self._vec(text_)


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest_asyncio.fixture
async def campaign() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Yield (world_id, campaign_id, session_id) for a fresh throwaway campaign in
    a throwaway world; clean up after. Canon is world-scoped and play is
    campaign-scoped (M2i), so a test that touches the knowledge layer needs both.
    Skips the test if Postgres is unreachable."""
    try:
        async with db_session() as s:
            await s.execute(text("SELECT 1"))
    except Exception as e:  # pragma: no cover - environment guard
        pytest.skip(f"Postgres not reachable: {e}")

    world_id, campaign_id = uuid.uuid4(), uuid.uuid4()
    async with db_session() as s:
        s.add(World(id=world_id, name=f"test-world-{world_id}"))
        await s.flush()
        camp = Campaign(id=campaign_id, world_id=world_id, name=f"test-{campaign_id}")
        s.add(camp)
        await s.flush()
        sess = GameSession(campaign_id=campaign_id, title="test", history=[])
        s.add(sess)
        await s.flush()
        session_id = sess.id
        await s.commit()

    try:
        yield world_id, campaign_id, session_id
    finally:
        async with db_session() as s:
            await s.execute(delete(DynamicChunk).where(DynamicChunk.campaign_id == campaign_id))
            await s.execute(
                delete(CommunitySummary).where(CommunitySummary.world_id == world_id)
            )
            await s.execute(delete(Edge).where(Edge.world_id == world_id))
            await s.execute(delete(Node).where(Node.world_id == world_id))
            await s.execute(delete(StoryBeat).where(StoryBeat.campaign_id == campaign_id))
            await s.execute(delete(GameSession).where(GameSession.campaign_id == campaign_id))
            await s.execute(delete(Campaign).where(Campaign.id == campaign_id))
            await s.execute(delete(World).where(World.id == world_id))
            await s.commit()
