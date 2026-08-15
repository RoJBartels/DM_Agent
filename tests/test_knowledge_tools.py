"""Knowledge tools degrade gracefully when the embeddings backend is missing.

The running server needs sentence-transformers, but if it's ever absent the
lore/rules tools must return a clear, non-fatal message (so the turn continues)
rather than raising. No DB or torch needed — the guard fires first.
"""

from __future__ import annotations

import uuid

from dm_agent.tools import knowledge as kn
from dm_agent.tools.base import ToolContext


def _ctx():
    async def emit(_ev):  # pragma: no cover - never reached in these tests
        raise AssertionError("read-only tools must not emit events")

    return ToolContext(
        world_id=uuid.uuid4(), campaign_id=uuid.uuid4(), session_id=uuid.uuid4(), emit=emit
    )


async def test_lookup_rules_degrades_without_embeddings(monkeypatch):
    monkeypatch.setattr(kn, "embeddings_available", lambda: False)
    out = await kn._lookup_rules(_ctx(), {"query": "grappling"})
    assert "unavailable" in out.lower()


async def test_lookup_lore_degrades_without_embeddings(monkeypatch):
    monkeypatch.setattr(kn, "embeddings_available", lambda: False)
    out = await kn._lookup_lore_tool(_ctx(), {"question": "who rules here?"})
    assert "unavailable" in out.lower()
