"""Campaign & character management API + world-upload endpoint (M2b).

Wraps the buckets M1 created (campaigns, characters, sessions) with CRUD the
stage's sidebar can drive, plus an upload endpoint that runs M2's static world
build (`knowledge.build.build_world`) so a user never needs the terminal.

The build is slow (LLM extraction + embeddings), so upload returns a job id
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

from dm_agent.db import Campaign, Character, EventLog, GameSession, Node, StoryBeat, db_session

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# --- schemas ---------------------------------------------------------------


class CampaignIn(BaseModel):
    name: str = Field(min_length=1)


class CampaignOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    character_count: int
    has_world: bool
    has_story: bool
    has_history: bool  # latest play has a transcript → the start menu offers "Continue"
    settings: dict[str, Any] = Field(default_factory=dict)  # per-campaign prefs (M2g)


class CampaignPatch(BaseModel):
    # All optional: a PATCH updates only the fields it carries.
    name: str | None = Field(default=None, min_length=1)
    settings: dict[str, Any] | None = None


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


# --- campaigns -------------------------------------------------------------


@router.get("/campaigns", response_model=list[CampaignOut])
async def list_campaigns() -> list[CampaignOut]:
    async with db_session() as s:
        campaigns = (
            await s.execute(select(Campaign).order_by(Campaign.created_at))
        ).scalars().all()

        # Grouped counts so this stays one query each regardless of campaign count.
        char_counts = dict(
            (
                await s.execute(
                    select(Character.campaign_id, func.count()).group_by(Character.campaign_id)
                )
            ).all()
        )
        world_campaigns = set(
            (await s.execute(select(Node.campaign_id).distinct())).scalars().all()
        )
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
            name=c.name,
            created_at=c.created_at,
            character_count=char_counts.get(c.id, 0),
            has_world=c.id in world_campaigns,
            has_story=c.id in story_campaigns,
            has_history=c.id in played_campaigns,
            settings=c.settings or {},
        )
        for c in campaigns
    ]


@router.post("/campaigns", response_model=CampaignOut, status_code=201)
async def create_campaign(body: CampaignIn) -> CampaignOut:
    async with db_session() as s:
        campaign = Campaign(name=body.name.strip())
        s.add(campaign)
        await s.commit()
        await s.refresh(campaign)
    return CampaignOut(
        id=campaign.id,
        name=campaign.name,
        created_at=campaign.created_at,
        character_count=0,
        has_world=False,
        has_story=False,
        has_history=False,
        settings=campaign.settings or {},
    )


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
                select(Node.campaign_id).where(Node.campaign_id == campaign_id).limit(1)
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
    """List a campaign's lore entities (id/type/name) for the character creator's
    world-aligned pick-lists (M2f). Optionally filter to a comma-separated set of
    node types, e.g. ?types=Faction,Location,Deity. Empty when no world is built."""
    wanted = {t.strip() for t in types.split(",") if t.strip()} if types else None
    async with db_session() as s:
        await _get_campaign(s, campaign_id)
        q = select(Node.id, Node.type, Node.name).where(Node.campaign_id == campaign_id)
        if wanted:
            q = q.where(Node.type.in_(wanted))
        rows = (await s.execute(q.order_by(Node.type, Node.name))).all()
    return [{"id": r[0], "type": r[1], "name": r[2]} for r in rows]


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


async def _validated_docs(campaign_id: uuid.UUID, documents: list[str]) -> list[str]:
    docs = [d for d in documents if d.strip()]
    if not docs:
        raise HTTPException(status_code=422, detail="no non-empty documents provided")
    async with db_session() as s:
        await _get_campaign(s, campaign_id)
    return docs


async def _run_world_build(job_id: str, campaign_id: uuid.UUID, documents: list[str]) -> None:
    try:
        from dm_agent.knowledge.build import build_world  # lazy: pulls the knowledge extra

        stats = await build_world(campaign_id, documents)
        _jobs[job_id] = JobOut(job_id=job_id, status="done", stats=stats)
    except Exception as exc:  # noqa: BLE001 — surface any failure to the poller
        log.exception("world build failed for campaign %s", campaign_id)
        _jobs[job_id] = JobOut(job_id=job_id, status="error", error=str(exc))


async def _run_story_build(job_id: str, campaign_id: uuid.UUID, documents: list[str]) -> None:
    try:
        from dm_agent.knowledge.story import build_story  # lazy, mirrors world build

        stats = await build_story(campaign_id, documents)
        _jobs[job_id] = JobOut(job_id=job_id, status="done", stats=stats)
    except Exception as exc:  # noqa: BLE001 — surface any failure to the poller
        log.exception("story build failed for campaign %s", campaign_id)
        _jobs[job_id] = JobOut(job_id=job_id, status="error", error=str(exc))


def _start_job(runner: Any, campaign_id: uuid.UUID, docs: list[str]) -> JobOut:
    job_id = uuid.uuid4().hex
    job = JobOut(job_id=job_id, status="running")
    _jobs[job_id] = job
    asyncio.create_task(runner(job_id, campaign_id, docs))
    return job


def _get_job(job_id: str) -> JobOut:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id}")
    return job


@router.post("/campaigns/{campaign_id}/world", response_model=JobOut, status_code=202)
async def upload_world(campaign_id: uuid.UUID, body: UploadIn) -> JobOut:
    docs = await _validated_docs(campaign_id, body.documents)
    return _start_job(_run_world_build, campaign_id, docs)


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
