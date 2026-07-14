"""Static-build CLI: worldbuilding docs -> typed graph + embeddings + community
summaries, and SRD text -> rules_chunks. This is the offline, expensive half of
the hybrid; play-time retrieval reads what this produces.

Usage:
    python -m dm_agent.knowledge.build world --campaign "Demo Campaign" doc1.md doc2.md
    python -m dm_agent.knowledge.build rules --ruleset dnd5e srd.txt
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from pathlib import Path

import anthropic
from sqlalchemy import delete, select

from dm_agent.config import get_settings
from dm_agent.db import (
    Campaign,
    CommunitySummary,
    Edge,
    Node,
    RulesChunk,
    db_session,
)
from dm_agent.knowledge.communities import detect_communities, summarize_community
from dm_agent.knowledge.embeddings import EmbeddingProvider, get_embedder
from dm_agent.knowledge.extract import extract_graph, node_props
from dm_agent.knowledge.store import GraphStore

log = logging.getLogger(__name__)


async def resolve_campaign(name: str) -> uuid.UUID:
    """Get-or-create a campaign by name; return its id."""
    async with db_session() as session:
        campaign = (
            await session.execute(select(Campaign).where(Campaign.name == name))
        ).scalar_one_or_none()
        if campaign is None:
            campaign = Campaign(name=name)
            session.add(campaign)
            await session.flush()
        cid = campaign.id
        await session.commit()
    return cid


async def build_world(
    campaign_id: uuid.UUID,
    documents: list[str],
    *,
    embedder: EmbeddingProvider | None = None,
    client: anthropic.AsyncAnthropic | None = None,
) -> dict[str, int]:
    """Full world build for a campaign. Idempotent: wipes and rebuilds this
    campaign's canon (nodes/edges/community_summaries); leaves session history."""
    settings = get_settings()
    embedder = embedder or get_embedder()
    client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)
    store = GraphStore()

    # 1. Extract each document, then merge across documents with one dedup pass.
    from dm_agent.knowledge.extract import Extraction, dedup

    merged = Extraction(nodes=[], edges=[])
    for doc in documents:
        ex = await extract_graph(doc, client=client)
        merged.nodes.extend(ex.nodes)
        merged.edges.extend(ex.edges)
        log.info("extracted %d nodes, %d edges from a document", len(ex.nodes), len(ex.edges))
    merged = dedup(merged)
    log.info("after cross-doc dedup: %d nodes, %d edges", len(merged.nodes), len(merged.edges))

    # 2. Embed node prose (one batch).
    prose = [n.prose for n in merged.nodes]
    vectors = embedder.embed(prose) if prose else []

    # 3. Communities over the node/edge graph.
    node_ids = [n.id for n in merged.nodes]
    community_of = detect_communities(node_ids, [(e.src, e.dst) for e in merged.edges])

    # 4. Wipe + write canon.
    async with db_session() as session:
        await session.execute(delete(Edge).where(Edge.campaign_id == campaign_id))
        await session.execute(
            delete(CommunitySummary).where(CommunitySummary.campaign_id == campaign_id)
        )
        await session.execute(delete(Node).where(Node.campaign_id == campaign_id))
        await session.flush()

        for node, vec in zip(merged.nodes, vectors, strict=True):
            session.add(
                Node(
                    campaign_id=campaign_id,
                    id=node.id,
                    type=node.type,
                    name=node.name,
                    props=node_props(node),
                    prose=node.prose,
                    embedding=vec,
                    community_id=community_of.get(node.id),
                )
            )
        for e in merged.edges:
            await store.add_edge(session, campaign_id, src=e.src, dst=e.dst, type=e.type)
        await session.commit()

    # 5. Community summaries (Haiku), embedded and stored.
    by_community: dict[int, list[tuple[str, str, str]]] = {}
    for node in merged.nodes:
        cid = community_of.get(node.id)
        if cid is None:
            continue
        by_community.setdefault(cid, []).append((node.name, node.type, node.prose))

    n_summaries = 0
    async with db_session() as session:
        for cid, members in by_community.items():
            if len(members) < 2:
                continue  # singletons: the node's own prose already covers it
            text = await summarize_community(members, client=client)
            emb = embedder.embed_one(text)
            member_ids = [
                n.id for n in merged.nodes if community_of.get(n.id) == cid
            ]
            session.add(
                CommunitySummary(
                    campaign_id=campaign_id,
                    community_id=cid,
                    member_ids=member_ids,
                    text=text,
                    embedding=emb,
                )
            )
            n_summaries += 1
        await session.commit()

    stats = {
        "nodes": len(merged.nodes),
        "edges": len(merged.edges),
        "communities": len(by_community),
        "summaries": n_summaries,
    }
    log.info("world build complete: %s", stats)
    return stats


def _chunk_rules(text: str, target: int = 900) -> list[str]:
    """Split rules text into ~paragraph chunks near `target` chars."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if buf and len(buf) + len(p) + 2 > target:
            chunks.append(buf)
            buf = p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    return chunks


async def ingest_rules(
    documents: list[str],
    ruleset_id: str,
    *,
    embedder: EmbeddingProvider | None = None,
) -> int:
    """Chunk + embed rules text into `rules_chunks`. Idempotent per ruleset_id."""
    embedder = embedder or get_embedder()
    chunks: list[str] = []
    for doc in documents:
        chunks.extend(_chunk_rules(doc))
    vectors = embedder.embed(chunks) if chunks else []

    async with db_session() as session:
        await session.execute(delete(RulesChunk).where(RulesChunk.ruleset_id == ruleset_id))
        for text, vec in zip(chunks, vectors, strict=True):
            session.add(RulesChunk(ruleset_id=ruleset_id, text=text, embedding=vec))
        await session.commit()
    log.info("ingested %d rules chunks for ruleset %s", len(chunks), ruleset_id)
    return len(chunks)


def _read(paths: list[str]) -> list[str]:
    return [Path(p).read_text(encoding="utf-8") for p in paths]


async def _main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dm-build", description="Knowledge static build")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pw = sub.add_parser("world", help="build a campaign's lore graph from documents")
    pw.add_argument("--campaign", required=True, help="campaign name (get-or-create)")
    pw.add_argument("paths", nargs="+", help="markdown/text worldbuilding docs")

    pr = sub.add_parser("rules", help="ingest a rules corpus into rules_chunks")
    pr.add_argument("--ruleset", default=get_settings().default_ruleset)
    pr.add_argument("paths", nargs="+", help="rules text files")

    ps = sub.add_parser("story", help="build a campaign's advisory story guide from an adventure")
    ps.add_argument("--campaign", required=True, help="campaign name (get-or-create)")
    ps.add_argument("paths", nargs="+", help="adventure/outline markdown docs")

    args = parser.parse_args(argv)

    if args.cmd == "world":
        campaign_id = await resolve_campaign(args.campaign)
        stats = await build_world(campaign_id, _read(args.paths))
        print(f"Built world for campaign {campaign_id}: {stats}")
    elif args.cmd == "rules":
        n = await ingest_rules(_read(args.paths), args.ruleset)
        print(f"Ingested {n} rules chunks for ruleset {args.ruleset!r}")
    elif args.cmd == "story":
        from dm_agent.knowledge.story import build_story

        campaign_id = await resolve_campaign(args.campaign)
        stats = await build_story(campaign_id, _read(args.paths))
        print(f"Built story guide for campaign {campaign_id}: {stats}")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())


if __name__ == "__main__":
    main()
