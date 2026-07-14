"""Leiden community detection + Haiku community summaries (the GraphRAG "global"
half). Communities group tightly-connected entities; a short LLM summary of each
becomes a retrievable chunk that answers broad questions no single node covers.
"""

from __future__ import annotations

import anthropic

from dm_agent.config import get_settings


def detect_communities(node_ids: list[str], edges: list[tuple[str, str]]) -> dict[str, int]:
    """Partition nodes into communities with the Leiden algorithm (undirected,
    modularity). Isolated nodes each become their own singleton community.

    Returns a mapping slug -> community_id.
    """
    if not node_ids:
        return {}

    import igraph as ig
    import leidenalg as la

    index = {slug: i for i, slug in enumerate(node_ids)}
    g = ig.Graph()
    g.add_vertices(len(node_ids))
    ig_edges = [
        (index[s], index[d])
        for s, d in edges
        if s in index and d in index and s != d
    ]
    if ig_edges:
        g.add_edges(ig_edges)

    partition = la.find_partition(g, la.ModularityVertexPartition, seed=1)
    return {node_ids[v]: cid for cid, community in enumerate(partition) for v in community}


_SUMMARY_SYSTEM = """\
You summarize a cluster of related entities from a tabletop RPG world into one tight \
paragraph (3-6 sentences). Name the key entities explicitly and state how they relate. \
This summary is used for retrieval, so be concrete and factual — no preamble, no \
"this community contains". Just the substance.
"""


async def summarize_community(
    members: list[tuple[str, str, str]],
    client: anthropic.AsyncAnthropic | None = None,
    model: str | None = None,
) -> str:
    """Write a retrieval summary for one community.

    `members` is a list of (name, type, prose). Uses Haiku by default.
    """
    settings = get_settings()
    client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)
    model = model or settings.utility_model

    body = "\n\n".join(f"[{typ}] {name}: {prose}" for name, typ, prose in members)
    msg = await client.messages.create(
        model=model,
        max_tokens=512,
        system=_SUMMARY_SYSTEM,
        messages=[{"role": "user", "content": body}],
    )
    return "".join(block.text for block in msg.content if block.type == "text").strip()
