"""session context (M2k): game_sessions.context jsonb

Holds the rolling compaction state (running summary + how much of the history it
replaces on the wire) and the cached player-facing recap. Derived data only —
`history` stays whole, so an empty context just means "send everything", which is
exactly the pre-M2k behaviour.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default backfills running sessions with an empty context object.
    op.add_column(
        "game_sessions",
        sa.Column(
            "context",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("game_sessions", "context")
