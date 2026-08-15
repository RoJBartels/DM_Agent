"""World, campaign & character management API + content studio (M2b, M2i).

Wraps the buckets M1 created (campaigns, characters, sessions) with CRUD the
stage's menu can drive, plus upload endpoints that run M2's static builds
(`knowledge.build.build_world`, `knowledge.story.build_story`) so a user never
needs the terminal.

M2i added the layer above: a **world** owns the lore graph, a campaign is one
playthrough inside it, and picking a world is what decides which campaigns,
characters and stories exist. So lore uploads and lore CRUD hang off
`/worlds/...` while story guides, characters and play stay on `/campaigns/...`.
It also filled in the editing gap the playtest hit — until now content could
only be replaced by uploading over the top, and nothing could be deleted at all.

The builds are slow (LLM extraction + embeddings), so an upload returns a job id
immediately and the client polls `GET /api/world-jobs/{id}`. The job runs as a
task on the app's event loop — fine for this single-user local tool; the brief
CPU-bound embedding step during a build is an accepted trade-off, not something
to thread out for one operator.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dm_agent.db import (
    Campaign,
    Character,
    CommunitySummary,
    DynamicChunk,
    Edge,
    EventLog,
    GameSession,
    Node,
    StoryBeat,
    World,
    WorldFlag,
    db_session,
)
from dm_agent.orchestrator.compaction import build_recap, turn_starts

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# --- schemas ---------------------------------------------------------------


class WorldIn(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""


class WorldPatch(BaseModel):
    # All optional: a PATCH updates only the fields it carries.
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None


class WorldOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    created_at: datetime
    campaign_count: int
    node_count: int
    # Reserved M8 anchor. Surfaced read-only so the UI can say which rules a world
    # runs on once that binding exists; null means the 5e SRD default.
    ruleset_id: str | None = None


class CampaignIn(BaseModel):
    name: str = Field(min_length=1)
    world_id: uuid.UUID


class CampaignOut(BaseModel):
    id: uuid.UUID
    world_id: uuid.UUID
    name: str
    created_at: datetime
    character_count: int
    has_world: bool  # the campaign's WORLD has lore built
    has_story: bool
    has_history: bool  # latest play has a transcript → the start menu offers "Continue"
    # Per-campaign prefs: auto_resolve_simple (M2g), play_mode (M2e refinement).
    settings: dict[str, Any] = Field(default_factory=dict)


class CampaignPatch(BaseModel):
    # All optional: a PATCH updates only the fields it carries.
    name: str | None = Field(default=None, min_length=1)
    settings: dict[str, Any] | None = None


class NodeOut(BaseModel):
    id: str  # slug
    type: str
    name: str
    prose: str
    props: dict[str, Any] = Field(default_factory=dict)
    community_id: int | None = None


class NodePatch(BaseModel):
    # All optional: a PATCH updates only the fields it carries. The slug is the
    # join key used by `entity_ids` arrays across the DB, so it is NOT editable —
    # renaming a node changes its display name, never its identity.
    type: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1)
    prose: str | None = None
    props: dict[str, Any] | None = None


class EdgeOut(BaseModel):
    id: int
    src: str
    dst: str
    type: str


class GraphOut(BaseModel):
    nodes: list[NodeOut]
    edges: list[EdgeOut]


class BeatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_index: int
    title: str
    summary: str
    read_aloud: str
    trigger_condition: str
    entity_ids: list[str]
    status: str


class BeatPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    summary: str | None = None
    read_aloud: str | None = None
    trigger_condition: str | None = None
    status: str | None = None


class SessionOut(BaseModel):
    session_id: uuid.UUID
    campaign_id: uuid.UUID


class CharacterIn(BaseModel):
    name: str = Field(min_length=1)
    is_pc: bool = True
    stats: dict[str, Any] = Field(default_factory=dict)
    max_hp: int = 10
    hp: int = 10
    ac: int = 10
    inventory: list[Any] = Field(default_factory=list)
    notes: str = ""


class CharacterPatch(BaseModel):
    # All optional: a PATCH updates only the fields it carries.
    name: str | None = Field(default=None, min_length=1)
    is_pc: bool | None = None
    stats: dict[str, Any] | None = None
    max_hp: int | None = None
    hp: int | None = None
    ac: int | None = None
    inventory: list[Any] | None = None
    notes: str | None = None


class CharacterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    name: str
    is_pc: bool
    stats: dict[str, Any]
    max_hp: int
    hp: int
    ac: int
    inventory: list[Any]
    notes: str


class RecapOut(BaseModel):
    text: str  # "" when the campaign has never been played
    turns: int


class UploadIn(BaseModel):
    documents: list[str] = Field(min_length=1)


class JobOut(BaseModel):
    job_id: str
    status: str  # running | done | error
    stats: dict[str, int] | None = None
    error: str | None = None


# --- helpers ---------------------------------------------------------------


async def _get_campaign(s: AsyncSession, campaign_id: uuid.UUID) -> Campaign:
    campaign = await s.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail=f"unknown campaign {campaign_id}")
    return campaign


async def _get_world(s: AsyncSession, world_id: uuid.UUID) -> World:
    world = await s.get(World, world_id)
    if world is None:
        raise HTTPException(status_code=404, detail=f"unknown world {world_id}")
    return world


async def _wipe_lore(s: AsyncSession, world_id: uuid.UUID) -> dict[str, int]:
    """Delete a world's whole canon. Campaigns, characters and play survive —
    they just have no lore to look up until something is uploaded again."""
    counts = {
        "nodes": (await s.execute(delete(Node).where(Node.world_id == world_id))).rowcount,
        "edges": (await s.execute(delete(Edge).where(Edge.world_id == world_id))).rowcount,
        "summaries": (
            await s.execute(delete(CommunitySummary).where(CommunitySummary.world_id == world_id))
        ).rowcount,
    }
    return counts


async def get_or_create_session(s: AsyncSession, campaign_id: uuid.UUID) -> GameSession:
    """Latest session for a campaign, creating a first one if none exists."""
    session = (
        await s.execute(
            select(GameSession)
            .where(GameSession.campaign_id == campaign_id)
            .order_by(GameSession.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if session is None:
        session = GameSession(campaign_id=campaign_id, title="Session 1", history=[])
        s.add(session)
        await s.flush()
    return session


def reconstruct_transcript(
    history: list[Any], events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Rebuild a display-only transcript so a reconnecting player sees where they
    left off (M2d). The on-screen log is wiped on connect, but two persisted
    sources survive: `GameSession.history` (player text + DM narration, in order,
    but no narration is in the event log) and the session's `event_log` rows
    (clean typed dice/state/error events, but no narration). We use history as the
    ordering backbone and fold each turn's typed events in after its prose.

    A turn = one player-string message in history ↔ one turn_start..turn_end span
    in the event log; both are written 1:1 per `run_turn`, so zipping by turn index
    is exact and never relies on fuzzy matching. Replay is display-only — it must
    never resolve a turn."""
    # Bucket the typed events by turn. Anything before the first turn_start (there
    # shouldn't be any) opens an implicit bucket so nothing is dropped.
    event_turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] | None = None
    for ev in events:
        etype = ev.get("type")
        if etype == "turn_start":
            current = []
            event_turns.append(current)
        elif etype in ("dice_roll", "state_update", "error"):
            if current is None:
                current = []
                event_turns.append(current)
            current.append(ev)

    # Split history into the same turns: a user string message starts a new turn;
    # assistant text blocks are that turn's narration (tool-use/result blocks and
    # thinking are skipped — they're mechanical, not transcript).
    history_turns: list[dict[str, Any]] = []
    turn: dict[str, Any] | None = None
    for msg in history:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if msg.get("role") == "user" and isinstance(content, str):
            turn = {"player": content, "narration": []}
            history_turns.append(turn)
        elif msg.get("role") == "assistant" and isinstance(content, list):
            if turn is None:  # assistant before any player message — defensive
                turn = {"player": None, "narration": []}
                history_turns.append(turn)
            text = "".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
            if text.strip():
                turn["narration"].append(text)

    items: list[dict[str, Any]] = []
    for i, ht in enumerate(history_turns):
        if ht["player"] is not None:
            items.append({"kind": "player", "text": ht["player"]})
        for text in ht["narration"]:
            items.append({"kind": "narration", "text": text})
        for ev in event_turns[i] if i < len(event_turns) else []:
            if ev["type"] == "dice_roll":
                if ev.get("hidden"):
                    # M2l — logged already redacted; re-emit only the flag so a
                    # replayed screened roll can never reconstitute the numbers.
                    items.append({"kind": "dice", "hidden": True})
                    continue
                items.append(
                    {
                        "kind": "dice",
                        "expression": ev.get("expression", ""),
                        "rolls": ev.get("rolls", []),
                        "total": ev.get("total"),
                        "purpose": ev.get("purpose", ""),
                        # M2h — carried through so a replayed chip reads identically to
                        # the live one. Absent on pre-M2h logged events (chip degrades).
                        "modifier": ev.get("modifier", 0),
                        "kept": ev.get("kept"),
                        "dropped": ev.get("dropped"),
                        "dc": ev.get("dc"),
                        "outcome": ev.get("outcome"),
                        "breakdown": ev.get("breakdown"),
                    }
                )
            elif ev["type"] == "state_update":
                items.append(
                    {"kind": "state", "entity": ev.get("entity", ""), "changes": ev.get("changes", {})}
                )
            elif ev["type"] == "error":
                items.append({"kind": "error", "message": ev.get("message", "")})
    return items


# --- worlds ----------------------------------------------------------------


@router.get("/worlds", response_model=list[WorldOut])
async def list_worlds() -> list[WorldOut]:
    """Every setting on the box. The stage's world switcher sits above the
    campaign switcher and drives everything below it."""
    async with db_session() as s:
        worlds = (await s.execute(select(World).order_by(World.created_at))).scalars().all()
        campaign_counts = dict(
            (
                await s.execute(
                    select(Campaign.world_id, func.count()).group_by(Campaign.world_id)
                )
            ).all()
        )
        node_counts = dict(
            (await s.execute(select(Node.world_id, func.count()).group_by(Node.world_id))).all()
        )
    return [
        WorldOut(
            id=w.id,
            name=w.name,
            description=w.description or "",
            created_at=w.created_at,
            campaign_count=campaign_counts.get(w.id, 0),
            node_count=node_counts.get(w.id, 0),
            ruleset_id=w.ruleset_id,
        )
        for w in worlds
    ]


@router.post("/worlds", response_model=WorldOut, status_code=201)
async def create_world(body: WorldIn) -> WorldOut:
    async with db_session() as s:
        world = World(name=body.name.strip(), description=body.description.strip())
        s.add(world)
        await s.commit()
        await s.refresh(world)
    return WorldOut(
        id=world.id,
        name=world.name,
        description=world.description or "",
        created_at=world.created_at,
        campaign_count=0,
        node_count=0,
        ruleset_id=world.ruleset_id,
    )


@router.patch("/worlds/{world_id}", response_model=WorldOut)
async def update_world(world_id: uuid.UUID, body: WorldPatch) -> WorldOut:
    async with db_session() as s:
        world = await _get_world(s, world_id)
        data = body.model_dump(exclude_unset=True)
        if "name" in data:
            world.name = data["name"].strip()
        if "description" in data:
            world.description = data["description"].strip()
        await s.commit()
        await s.refresh(world)
        campaign_count = (
            await s.execute(
                select(func.count()).select_from(Campaign).where(Campaign.world_id == world_id)
            )
        ).scalar_one()
        node_count = (
            await s.execute(
                select(func.count()).select_from(Node).where(Node.world_id == world_id)
            )
        ).scalar_one()
    return WorldOut(
        id=world.id,
        name=world.name,
        description=world.description or "",
        created_at=world.created_at,
        campaign_count=campaign_count,
        node_count=node_count,
        ruleset_id=world.ruleset_id,
    )


@router.delete("/worlds/{world_id}", status_code=204)
async def delete_world(world_id: uuid.UUID) -> None:
    """Delete a world and its canon. Refuses while campaigns still live in it —
    deleting a setting must never silently take somebody's saved games with it;
    the client deletes those first, deliberately, one at a time."""
    async with db_session() as s:
        await _get_world(s, world_id)
        campaigns = (
            await s.execute(
                select(func.count()).select_from(Campaign).where(Campaign.world_id == world_id)
            )
        ).scalar_one()
        if campaigns:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"world still has {campaigns} campaign(s) — delete them first "
                    "(their characters and play history go with them)"
                ),
            )
        await _wipe_lore(s, world_id)
        await s.execute(delete(World).where(World.id == world_id))
        await s.commit()


@router.get("/worlds/{world_id}/graph", response_model=GraphOut)
async def world_graph(world_id: uuid.UUID) -> GraphOut:
    """A world's whole canon graph for the explorer. Sent in one shot and laid out
    client-side: these graphs are tens of nodes, not thousands, so paging or
    server-side layout would be complexity with nothing to buy."""
    async with db_session() as s:
        await _get_world(s, world_id)
        nodes = (
            await s.execute(
                select(Node).where(Node.world_id == world_id).order_by(Node.type, Node.name)
            )
        ).scalars().all()
        edges = (
            await s.execute(select(Edge).where(Edge.world_id == world_id).order_by(Edge.id))
        ).scalars().all()
    return GraphOut(
        nodes=[
            NodeOut(
                id=n.id,
                type=n.type,
                name=n.name,
                prose=n.prose,
                props=n.props or {},
                community_id=n.community_id,
            )
            for n in nodes
        ],
        edges=[EdgeOut(id=e.id, src=e.src, dst=e.dst, type=e.type) for e in edges],
    )


@router.get("/worlds/{world_id}/lore-nodes")
async def world_lore_nodes(world_id: uuid.UUID, types: str | None = None) -> list[dict[str, str]]:
    """List a world's lore entities (id/type/name), optionally filtered to a
    comma-separated set of types, e.g. ?types=Faction,Location,Deity."""
    wanted = {t.strip() for t in types.split(",") if t.strip()} if types else None
    async with db_session() as s:
        await _get_world(s, world_id)
        q = select(Node.id, Node.type, Node.name).where(Node.world_id == world_id)
        if wanted:
            q = q.where(Node.type.in_(wanted))
        rows = (await s.execute(q.order_by(Node.type, Node.name))).all()
    return [{"id": r[0], "type": r[1], "name": r[2]} for r in rows]


@router.patch("/worlds/{world_id}/nodes/{slug}", response_model=NodeOut)
async def update_node(world_id: uuid.UUID, slug: str, body: NodePatch) -> NodeOut:
    """Edit one canon entity in place, without re-uploading the whole world."""
    async with db_session() as s:
        await _get_world(s, world_id)
        node = await s.get(Node, (world_id, slug))
        if node is None:
            raise HTTPException(status_code=404, detail=f"unknown node {slug}")
        data = body.model_dump(exclude_unset=True)
        for field in ("type", "name", "props"):
            if field in data and data[field] is not None:
                setattr(node, field, data[field])
        if "prose" in data and data["prose"] is not None and data["prose"] != node.prose:
            node.prose = data["prose"]
            # The vector describes the prose, so edited prose must be re-embedded.
            # If the backend is missing we drop the vector instead of keeping one
            # that describes text no longer in the row — retrieval can't tell a
            # stale vector from a good one, but it copes fine with a missing one
            # (the node is still reachable by name match and neighborhood).
            from dm_agent.knowledge.embeddings import embeddings_available, get_embedder

            if embeddings_available():
                node.embedding = get_embedder().embed_one(node.prose)
            else:
                log.warning("no embeddings backend — dropping stale vector for node %s", slug)
                node.embedding = None
        await s.commit()
        await s.refresh(node)
        return NodeOut(
            id=node.id,
            type=node.type,
            name=node.name,
            prose=node.prose,
            props=node.props or {},
            community_id=node.community_id,
        )


@router.delete("/worlds/{world_id}/nodes/{slug}", status_code=204)
async def delete_node(world_id: uuid.UUID, slug: str) -> None:
    """Remove a canon entity and any edge touching it, so the graph never keeps a
    dangling reference to something the user deleted on purpose."""
    async with db_session() as s:
        await _get_world(s, world_id)
        result = await s.execute(
            delete(Node).where(Node.world_id == world_id).where(Node.id == slug)
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"unknown node {slug}")
        await s.execute(
            delete(Edge)
            .where(Edge.world_id == world_id)
            .where((Edge.src == slug) | (Edge.dst == slug))
        )
        await s.commit()


@router.delete("/worlds/{world_id}/lore")
async def delete_lore(world_id: uuid.UUID) -> dict[str, int]:
    """Wipe a world's canon but keep the world itself, so it can be re-ingested
    from different documents without losing the campaigns played in it."""
    async with db_session() as s:
        await _get_world(s, world_id)
        counts = await _wipe_lore(s, world_id)
        await s.commit()
    return counts


# --- campaigns -------------------------------------------------------------


@router.get("/campaigns", response_model=list[CampaignOut])
async def list_campaigns(world_id: uuid.UUID | None = None) -> list[CampaignOut]:
    """Campaigns, optionally only those in one world — which is how the stage
    keeps unrelated settings from ever mixing in the menu."""
    async with db_session() as s:
        q = select(Campaign).order_by(Campaign.created_at)
        if world_id is not None:
            q = q.where(Campaign.world_id == world_id)
        campaigns = (await s.execute(q)).scalars().all()

        # Grouped counts so this stays one query each regardless of campaign count.
        char_counts = dict(
            (
                await s.execute(
                    select(Character.campaign_id, func.count()).group_by(Character.campaign_id)
                )
            ).all()
        )
        # Lore is world-scoped now, so "has a world" is a fact about the world the
        # campaign belongs to — every campaign in a built world inherits it.
        built_worlds = set((await s.execute(select(Node.world_id).distinct())).scalars().all())
        story_campaigns = set(
            (await s.execute(select(StoryBeat.campaign_id).distinct())).scalars().all()
        )
        # Campaigns whose play has actually started (any session with a non-empty
        # message history) — drives Continue vs. Begin on the start menu.
        played_campaigns = set(
            (
                await s.execute(
                    select(GameSession.campaign_id)
                    .where(func.jsonb_array_length(GameSession.history) > 0)
                    .distinct()
                )
            ).scalars().all()
        )

    return [
        CampaignOut(
            id=c.id,
            world_id=c.world_id,
            name=c.name,
            created_at=c.created_at,
            character_count=char_counts.get(c.id, 0),
            has_world=c.world_id in built_worlds,
            has_story=c.id in story_campaigns,
            has_history=c.id in played_campaigns,
            settings=c.settings or {},
        )
        for c in campaigns
    ]


@router.post("/campaigns", response_model=CampaignOut, status_code=201)
async def create_campaign(body: CampaignIn) -> CampaignOut:
    """Create a campaign inside a world. The world is required — a campaign with
    no setting has no lore to resolve against."""
    async with db_session() as s:
        await _get_world(s, body.world_id)
        campaign = Campaign(name=body.name.strip(), world_id=body.world_id)
        s.add(campaign)
        await s.commit()
        await s.refresh(campaign)
        has_world = (
            await s.execute(select(Node.world_id).where(Node.world_id == body.world_id).limit(1))
        ).first() is not None
    return CampaignOut(
        id=campaign.id,
        world_id=campaign.world_id,
        name=campaign.name,
        created_at=campaign.created_at,
        character_count=0,
        has_world=has_world,
        has_story=False,
        has_history=False,
        settings=campaign.settings or {},
    )


@router.delete("/campaigns/{campaign_id}", status_code=204)
async def delete_campaign(campaign_id: uuid.UUID) -> None:
    """Delete a campaign and everything that belongs to only it: characters, its
    story guide, its sessions and their logged events, its scene chunks and world
    flags. The world's canon is untouched — it belongs to the setting, and other
    campaigns may still be playing in it."""
    async with db_session() as s:
        await _get_campaign(s, campaign_id)
        session_ids = (
            await s.execute(select(GameSession.id).where(GameSession.campaign_id == campaign_id))
        ).scalars().all()
        if session_ids:
            await s.execute(delete(EventLog).where(EventLog.session_id.in_(session_ids)))
        await s.execute(delete(DynamicChunk).where(DynamicChunk.campaign_id == campaign_id))
        await s.execute(delete(GameSession).where(GameSession.campaign_id == campaign_id))
        await s.execute(delete(StoryBeat).where(StoryBeat.campaign_id == campaign_id))
        await s.execute(delete(Character).where(Character.campaign_id == campaign_id))
        await s.execute(delete(WorldFlag).where(WorldFlag.campaign_id == campaign_id))
        await s.execute(delete(Campaign).where(Campaign.id == campaign_id))
        await s.commit()


@router.patch("/campaigns/{campaign_id}", response_model=CampaignOut)
async def update_campaign(campaign_id: uuid.UUID, body: CampaignPatch) -> CampaignOut:
    """Rename a campaign and/or replace its settings blob (M2g auto-resolve toggle,
    future per-campaign prefs). settings is replaced wholesale — the client sends the
    full object it wants stored."""
    async with db_session() as s:
        campaign = await _get_campaign(s, campaign_id)
        data = body.model_dump(exclude_unset=True)
        if "name" in data:
            campaign.name = data["name"].strip()
        if "settings" in data:
            campaign.settings = data["settings"]
        await s.commit()
        await s.refresh(campaign)

        char_count = (
            await s.execute(
                select(func.count())
                .select_from(Character)
                .where(Character.campaign_id == campaign_id)
            )
        ).scalar_one()
        has_world = (
            await s.execute(
                select(Node.world_id).where(Node.world_id == campaign.world_id).limit(1)
            )
        ).first() is not None
        has_story = (
            await s.execute(
                select(StoryBeat.campaign_id).where(StoryBeat.campaign_id == campaign_id).limit(1)
            )
        ).first() is not None
        has_history = (
            await s.execute(
                select(GameSession.campaign_id)
                .where(GameSession.campaign_id == campaign_id)
                .where(func.jsonb_array_length(GameSession.history) > 0)
                .limit(1)
            )
        ).first() is not None
    return CampaignOut(
        id=campaign.id,
        world_id=campaign.world_id,
        name=campaign.name,
        created_at=campaign.created_at,
        character_count=char_count,
        has_world=has_world,
        has_story=has_story,
        has_history=has_history,
        settings=campaign.settings or {},
    )


@router.post("/campaigns/{campaign_id}/session", response_model=SessionOut)
async def campaign_session(campaign_id: uuid.UUID) -> SessionOut:
    """Get-or-create the campaign's latest session and return its id to connect."""
    async with db_session() as s:
        await _get_campaign(s, campaign_id)
        session = await get_or_create_session(s, campaign_id)
        await s.commit()
        return SessionOut(session_id=session.id, campaign_id=campaign_id)


@router.get("/campaigns/{campaign_id}/lore-nodes")
async def lore_nodes(campaign_id: uuid.UUID, types: str | None = None) -> list[dict[str, str]]:
    """The lore entities available to a campaign, for the character creator's
    world-aligned pick-lists (M2f) — i.e. its world's nodes, reached from the
    campaign so the creator needn't know about worlds. Optionally filter to a
    comma-separated set of node types. Empty when no lore is built."""
    wanted = {t.strip() for t in types.split(",") if t.strip()} if types else None
    async with db_session() as s:
        campaign = await _get_campaign(s, campaign_id)
        q = select(Node.id, Node.type, Node.name).where(Node.world_id == campaign.world_id)
        if wanted:
            q = q.where(Node.type.in_(wanted))
        rows = (await s.execute(q.order_by(Node.type, Node.name))).all()
    return [{"id": r[0], "type": r[1], "name": r[2]} for r in rows]


@router.get("/campaigns/{campaign_id}/recap", response_model=RecapOut)
async def campaign_recap(campaign_id: uuid.UUID) -> RecapOut:
    """A short player-facing "previously, on..." for the campaign's latest session
    (M2k) — the catch-up tool for picking a campaign back up days later.

    Distinct from the transcript, which replays everything verbatim, and from the
    model-context compaction, though it reads that summary for the distant past.
    Cached against the history length so re-opening the menu costs nothing; never
    creates a session, so browsing the menu can't start a game by accident.
    """
    async with db_session() as s:
        await _get_campaign(s, campaign_id)
        session = (
            await s.execute(
                select(GameSession)
                .where(GameSession.campaign_id == campaign_id)
                .order_by(GameSession.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        history = list(session.history or []) if session else []
        context = dict(session.context or {}) if session else {}
        if not history:
            return RecapOut(text="", turns=0)

        turns = len(turn_starts(history))
        cached = context.get("recap") or {}
        if cached.get("text") and cached.get("upto") == len(history):
            return RecapOut(text=str(cached["text"]), turns=turns)

        text = await build_recap(history, context)
        if text and session is not None:
            context["recap"] = {"text": text, "upto": len(history)}
            session.context = context
            await s.commit()
    return RecapOut(text=text, turns=turns)


@router.get("/sessions/{session_id}/transcript")
async def session_transcript(session_id: uuid.UUID) -> list[dict[str, Any]]:
    """Display-only replay of a session's prior play (M2d), so a reconnecting
    player sees the story so far. Never resolves a turn."""
    async with db_session() as s:
        session = await s.get(GameSession, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
        rows = (
            await s.execute(
                select(EventLog.event)
                .where(EventLog.session_id == session_id)
                .order_by(EventLog.id)
            )
        ).scalars().all()
    return reconstruct_transcript(session.history or [], list(rows))


# --- story guide (M2c content, M2i editing) --------------------------------


@router.get("/campaigns/{campaign_id}/story", response_model=list[BeatOut])
async def list_beats(campaign_id: uuid.UUID) -> list[StoryBeat]:
    async with db_session() as s:
        await _get_campaign(s, campaign_id)
        beats = (
            await s.execute(
                select(StoryBeat)
                .where(StoryBeat.campaign_id == campaign_id)
                .order_by(StoryBeat.order_index)
            )
        ).scalars().all()
    return list(beats)


@router.patch("/story-beats/{beat_id}", response_model=BeatOut)
async def update_beat(beat_id: uuid.UUID, body: BeatPatch) -> StoryBeat:
    """Edit a beat by hand — fix an extraction the model got wrong, rewrite the
    read-aloud text, or set the status directly rather than waiting for play to
    reach it. Beats are advisory, so nothing here can break a campaign."""
    async with db_session() as s:
        beat = await s.get(StoryBeat, beat_id)
        if beat is None:
            raise HTTPException(status_code=404, detail=f"unknown beat {beat_id}")
        data = body.model_dump(exclude_unset=True)
        if "status" in data and data["status"] not in ("upcoming", "active", "completed", "skipped"):
            raise HTTPException(status_code=422, detail=f"invalid status {data['status']!r}")
        for field, value in data.items():
            if value is not None:
                setattr(beat, field, value)
        await s.commit()
        await s.refresh(beat)
    return beat


@router.delete("/story-beats/{beat_id}", status_code=204)
async def delete_beat(beat_id: uuid.UUID) -> None:
    async with db_session() as s:
        result = await s.execute(delete(StoryBeat).where(StoryBeat.id == beat_id))
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"unknown beat {beat_id}")
        await s.commit()


@router.delete("/campaigns/{campaign_id}/story")
async def delete_story(campaign_id: uuid.UUID) -> dict[str, int]:
    """Drop a campaign's whole guide. It goes back to playing improvised, which is
    the same state a campaign that never had one is in."""
    async with db_session() as s:
        await _get_campaign(s, campaign_id)
        removed = (
            await s.execute(delete(StoryBeat).where(StoryBeat.campaign_id == campaign_id))
        ).rowcount
        await s.commit()
    return {"beats": removed}


# --- characters ------------------------------------------------------------


@router.get("/campaigns/{campaign_id}/characters", response_model=list[CharacterOut])
async def list_characters(campaign_id: uuid.UUID) -> list[Character]:
    async with db_session() as s:
        await _get_campaign(s, campaign_id)
        chars = (
            await s.execute(
                select(Character)
                .where(Character.campaign_id == campaign_id)
                .order_by(Character.is_pc.desc(), Character.name)
            )
        ).scalars().all()
    return list(chars)


@router.post(
    "/campaigns/{campaign_id}/characters", response_model=CharacterOut, status_code=201
)
async def create_character(campaign_id: uuid.UUID, body: CharacterIn) -> Character:
    async with db_session() as s:
        await _get_campaign(s, campaign_id)
        char = Character(campaign_id=campaign_id, **body.model_dump())
        s.add(char)
        await s.commit()
        await s.refresh(char)
    return char


@router.patch("/characters/{character_id}", response_model=CharacterOut)
async def update_character(character_id: uuid.UUID, body: CharacterPatch) -> Character:
    async with db_session() as s:
        char = await s.get(Character, character_id)
        if char is None:
            raise HTTPException(status_code=404, detail=f"unknown character {character_id}")
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(char, field, value)
        await s.commit()
        await s.refresh(char)
    return char


@router.delete("/characters/{character_id}", status_code=204)
async def delete_character(character_id: uuid.UUID) -> None:
    async with db_session() as s:
        result = await s.execute(delete(Character).where(Character.id == character_id))
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"unknown character {character_id}")
        await s.commit()


# --- background build jobs (world lore + story guide) -----------------------
#
# Both uploads are slow (LLM extraction + embeddings), so each returns a job id
# immediately and the client polls. World and story ids share one registry; the
# ids are random hex so they never collide.

_jobs: dict[str, JobOut] = {}


def _non_empty(documents: list[str]) -> list[str]:
    docs = [d for d in documents if d.strip()]
    if not docs:
        raise HTTPException(status_code=422, detail="no non-empty documents provided")
    return docs


async def _validated_docs(campaign_id: uuid.UUID, documents: list[str]) -> list[str]:
    docs = _non_empty(documents)
    async with db_session() as s:
        await _get_campaign(s, campaign_id)
    return docs


async def _validated_world_docs(world_id: uuid.UUID, documents: list[str]) -> list[str]:
    docs = _non_empty(documents)
    async with db_session() as s:
        await _get_world(s, world_id)
    return docs


async def _run_world_build(job_id: str, world_id: uuid.UUID, documents: list[str]) -> None:
    try:
        from dm_agent.knowledge.build import build_world  # lazy: pulls the knowledge extra

        stats = await build_world(world_id, documents)
        _jobs[job_id] = JobOut(job_id=job_id, status="done", stats=stats)
    except Exception as exc:  # noqa: BLE001 — surface any failure to the poller
        log.exception("lore build failed for world %s", world_id)
        _jobs[job_id] = JobOut(job_id=job_id, status="error", error=str(exc))


async def _run_story_build(job_id: str, campaign_id: uuid.UUID, documents: list[str]) -> None:
    try:
        from dm_agent.knowledge.story import build_story  # lazy, mirrors world build

        stats = await build_story(campaign_id, documents)
        _jobs[job_id] = JobOut(job_id=job_id, status="done", stats=stats)
    except Exception as exc:  # noqa: BLE001 — surface any failure to the poller
        log.exception("story build failed for campaign %s", campaign_id)
        _jobs[job_id] = JobOut(job_id=job_id, status="error", error=str(exc))


def _start_job(runner: Any, scope_id: uuid.UUID, docs: list[str]) -> JobOut:
    """Kick a build off in the background. `scope_id` is a world for lore and a
    campaign for a story guide — whichever the runner was written against."""
    job_id = uuid.uuid4().hex
    job = JobOut(job_id=job_id, status="running")
    _jobs[job_id] = job
    asyncio.create_task(runner(job_id, scope_id, docs))
    return job


def _get_job(job_id: str) -> JobOut:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id}")
    return job


@router.post("/worlds/{world_id}/lore", response_model=JobOut, status_code=202)
async def upload_lore(world_id: uuid.UUID, body: UploadIn) -> JobOut:
    """Ingest worldbuilding documents into a world's canon. Idempotent — a second
    upload replaces the graph, and every campaign in the world sees the new canon
    on its next lookup."""
    docs = await _validated_world_docs(world_id, body.documents)
    return _start_job(_run_world_build, world_id, docs)


@router.get("/world-jobs/{job_id}", response_model=JobOut)
async def world_job(job_id: str) -> JobOut:
    return _get_job(job_id)


@router.post("/campaigns/{campaign_id}/story", response_model=JobOut, status_code=202)
async def upload_story(campaign_id: uuid.UUID, body: UploadIn) -> JobOut:
    docs = await _validated_docs(campaign_id, body.documents)
    return _start_job(_run_story_build, campaign_id, docs)


@router.get("/story-jobs/{job_id}", response_model=JobOut)
async def story_job(job_id: str) -> JobOut:
    return _get_job(job_id)
