"""Static extraction: worldbuilding markdown → the doc's typed ontology (§2).

Opus reads prose and emits typed nodes + edges via structured output. This is
the "semantic" half of the GraphRAG-style pipeline, but with the doc's fixed
ontology so entity IDs stay a reliable join key (the reason the untyped MS
GraphRAG library was rejected). A light dedup pass merges obvious duplicates and
rewrites edge endpoints so repeated names across sections collapse to one node.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

import anthropic
from pydantic import BaseModel, Field

from dm_agent.config import get_settings

NODE_TYPES = ["Character", "Location", "Faction", "Item", "Deity", "Event", "Law"]
EDGE_TYPES = [
    "RULES",
    "LOCATED_IN",
    "MEMBER_OF",
    "ENEMY_OF",
    "KNOWS_SECRET",
    "GOVERNED_BY_LAW",
]


class Attribute(BaseModel):
    key: str
    value: str


class ExtractedNode(BaseModel):
    id: str = Field(description="stable kebab-case slug, e.g. 'duke-aldric'")
    type: str = Field(description=f"one of: {', '.join(NODE_TYPES)}")
    name: str = Field(description="canonical display name")
    prose: str = Field(
        description="2-4 sentence self-contained description of this entity, "
        "synthesizing what the source says. This text is what gets embedded."
    )
    attributes: list[Attribute] = Field(
        default_factory=list, description="salient key/value facts (e.g. title, alignment)"
    )


class ExtractedEdge(BaseModel):
    src: str = Field(description="source node slug")
    dst: str = Field(description="destination node slug")
    type: str = Field(description=f"one of: {', '.join(EDGE_TYPES)}")


class Extraction(BaseModel):
    nodes: list[ExtractedNode]
    edges: list[ExtractedEdge]


_EXTRACTION_SYSTEM = f"""\
You are a knowledge-graph extractor for a tabletop RPG world. Read the worldbuilding \
document and extract a typed graph.

Node types (use exactly these): {", ".join(NODE_TYPES)}.
Edge types (use exactly these): {", ".join(EDGE_TYPES)}.

Rules:
- Give each node a stable, kebab-case `id` slug derived from its name (e.g. "Duke Aldric" -> \
"duke-aldric"). Reuse the same slug everywhere that entity appears.
- `prose` must be a self-contained 2-4 sentence description usable on its own, since it is \
embedded for retrieval. Do not write "see above" or reference the document structure.
- Only create edges between nodes you emitted; `src`/`dst` must be slugs from your node list.
- Prefer fewer, well-typed nodes over many trivial ones. Extract people, places, factions, \
notable items, deities, significant events, and laws — not every common noun.
- Capture secrets and rivalries as edges (KNOWS_SECRET, ENEMY_OF) where the text implies them.
"""


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "node"


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def dedup(extraction: Extraction, threshold: float = 0.92) -> Extraction:
    """Merge near-duplicate nodes (same normalized name / high slug similarity) and
    rewrite edge endpoints onto the surviving slug."""
    survivors: list[ExtractedNode] = []
    remap: dict[str, str] = {}

    for node in extraction.nodes:
        match = None
        for s in survivors:
            if _norm(s.name) == _norm(node.name) or _norm(s.id) == _norm(node.id):
                match = s
                break
            if SequenceMatcher(None, _norm(s.name), _norm(node.name)).ratio() >= threshold:
                match = s
                break
        if match is None:
            survivors.append(node)
            remap[node.id] = node.id
        else:
            remap[node.id] = match.id
            # union attributes we don't already have
            seen = {(a.key, a.value) for a in match.attributes}
            for a in node.attributes:
                if (a.key, a.value) not in seen:
                    match.attributes.append(a)
            if len(node.prose) > len(match.prose):
                match.prose = node.prose

    survivor_ids = {n.id for n in survivors}
    merged_edges: list[ExtractedEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for e in extraction.edges:
        src = remap.get(e.src, e.src)
        dst = remap.get(e.dst, e.dst)
        if src not in survivor_ids or dst not in survivor_ids or src == dst:
            continue
        key = (src, dst, e.type)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        merged_edges.append(ExtractedEdge(src=src, dst=dst, type=e.type))

    return Extraction(nodes=survivors, edges=merged_edges)


async def extract_graph(
    document: str,
    client: anthropic.AsyncAnthropic | None = None,
    model: str | None = None,
) -> Extraction:
    """Extract a typed graph from one worldbuilding document, then dedup it."""
    settings = get_settings()
    client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)
    model = model or settings.narrator_model  # Opus for extraction quality

    parsed = await client.messages.parse(
        model=model,
        max_tokens=8192,
        thinking={"type": "adaptive"},
        system=_EXTRACTION_SYSTEM,
        messages=[{"role": "user", "content": document}],
        output_format=Extraction,
    )
    result = parsed.parsed_output
    if result is None:
        raise RuntimeError("extraction returned no structured output")

    # normalize slugs defensively (models occasionally emit non-slug ids)
    for n in result.nodes:
        n.id = _slugify(n.id)
    for e in result.edges:
        e.src, e.dst = _slugify(e.src), _slugify(e.dst)

    return dedup(result)


def node_props(node: ExtractedNode) -> dict[str, str]:
    """Flatten a node's attribute list into the JSONB props dict stored on `nodes`."""
    return {a.key: a.value for a in node.attributes}
