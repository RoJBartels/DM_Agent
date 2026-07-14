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

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dm_agent.db import Campaign, Character, GameSession, Node, db_session

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


class WorldIn(BaseModel):
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

    return [
        CampaignOut(
            id=c.id,
            name=c.name,
            created_at=c.created_at,
            character_count=char_counts.get(c.id, 0),
            has_world=c.id in world_campaigns,
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
    )


@router.post("/campaigns/{campaign_id}/session", response_model=SessionOut)
async def campaign_session(campaign_id: uuid.UUID) -> SessionOut:
    """Get-or-create the campaign's latest session and return its id to connect."""
    async with db_session() as s:
        await _get_campaign(s, campaign_id)
        session = await get_or_create_session(s, campaign_id)
        await s.commit()
        return SessionOut(session_id=session.id, campaign_id=campaign_id)


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


# --- world upload (background build job) ------------------------------------

_world_jobs: dict[str, JobOut] = {}


async def _run_world_build(job_id: str, campaign_id: uuid.UUID, documents: list[str]) -> None:
    try:
        from dm_agent.knowledge.build import build_world  # lazy: pulls the knowledge extra

        stats = await build_world(campaign_id, documents)
        _world_jobs[job_id] = JobOut(job_id=job_id, status="done", stats=stats)
    except Exception as exc:  # noqa: BLE001 — surface any failure to the poller
        log.exception("world build failed for campaign %s", campaign_id)
        _world_jobs[job_id] = JobOut(job_id=job_id, status="error", error=str(exc))


@router.post("/campaigns/{campaign_id}/world", response_model=JobOut, status_code=202)
async def upload_world(campaign_id: uuid.UUID, body: WorldIn) -> JobOut:
    docs = [d for d in body.documents if d.strip()]
    if not docs:
        raise HTTPException(status_code=422, detail="no non-empty documents provided")
    async with db_session() as s:
        await _get_campaign(s, campaign_id)

    job_id = uuid.uuid4().hex
    job = JobOut(job_id=job_id, status="running")
    _world_jobs[job_id] = job

    import asyncio

    asyncio.create_task(_run_world_build(job_id, campaign_id, docs))
    return job


@router.get("/world-jobs/{job_id}", response_model=JobOut)
async def world_job(job_id: str) -> JobOut:
    job = _world_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id}")
    return job
