"""Demo campaign seeding so the stage is playable immediately."""

from sqlalchemy import select

from dm_agent.db import Campaign, Character, GameSession, db_session

DEMO_CAMPAIGN = "Demo Campaign"


async def ensure_demo_session() -> dict:
    """Get-or-create a demo campaign with one PC and return its latest session."""
    async with db_session() as s:
        campaign = (
            await s.execute(select(Campaign).where(Campaign.name == DEMO_CAMPAIGN))
        ).scalar_one_or_none()
        if campaign is None:
            campaign = Campaign(name=DEMO_CAMPAIGN)
            s.add(campaign)
            await s.flush()
            s.add(
                Character(
                    campaign_id=campaign.id,
                    name="Kara",
                    is_pc=True,
                    stats={"STR": 8, "DEX": 16, "CON": 12, "INT": 13, "WIS": 10, "CHA": 14,
                           "class": "rogue", "level": 3, "proficiency_bonus": 2},
                    max_hp=21,
                    hp=21,
                    ac=14,
                    inventory=["shortsword", "dagger", "thieves' tools", "50 ft rope"],
                    notes="Halfling rogue. Quick fingers, quicker exits.",
                )
            )

        session = (
            await s.execute(
                select(GameSession)
                .where(GameSession.campaign_id == campaign.id)
                .order_by(GameSession.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if session is None:
            session = GameSession(campaign_id=campaign.id, title="Session 1", history=[])
            s.add(session)
        await s.commit()
        return {"session_id": str(session.id), "campaign_id": str(campaign.id)}
