# DM Agent

An AI Dungeon Master: **the LLM narrates, code adjudicates.** Everything mechanical
(dice, HP, DCs) runs through deterministic tools — never invented by the model.

Architecture reference: [dm-agent-architecture.md](dm-agent-architecture.md).

## Quickstart

Requirements: [uv](https://docs.astral.sh/uv/), Docker with Compose, an Anthropic API key.

```sh
# 1. Database (Postgres 17 + pgvector on localhost:5433)
docker compose up -d --wait

# 2. Dependencies + schema
uv sync
uv run alembic upgrade head

# 3. Credentials
cp .env.example .env      # then set DM_ANTHROPIC_API_KEY=sk-ant-...

# 4. Run
uv run dm-agent           # serves http://127.0.0.1:8000
```

Open http://127.0.0.1:8000 — the stage creates a demo campaign (one PC: Kara,
halfling rogue) on first load. Type an action and play.

## Development

```sh
uv run pytest             # rules engine + event schema tests
uv run alembic revision --autogenerate -m "..."   # after model changes
```

## Layout

| Path | What |
|---|---|
| `src/dm_agent/events.py` | Typed event stream — the websocket wire format (§6) |
| `src/dm_agent/orchestrator/` | Streaming tool-use agent loop |
| `src/dm_agent/rules/` | Deterministic dice/check engine |
| `src/dm_agent/tools/` | Agent tools: `roll_dice`, character sheets, world state |
| `src/dm_agent/db/` | SQLAlchemy models (Alembic migrations in `alembic/`) |
| `static/` | The stage: web client rendering the event stream |

## Status

Milestones M0 (scaffolding) and M1 (core loop) of the implementation plan are done:
streaming narration, deterministic dice, persistent character sheets and world flags,
resumable sessions. Next up (M2): the hybrid knowledge layer — static lore graph +
dynamic session memory behind a single `lookup_lore` tool.
