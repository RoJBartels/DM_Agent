import asyncio
import logging
import uuid
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from dm_agent.db import GameSession, db_session
from dm_agent.events import ErrorEvent, Event, PlayerAction
from dm_agent.orchestrator import Orchestrator
from dm_agent.seed import ensure_demo_session

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"

app = FastAPI(title="DM Agent")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_orchestrator: Orchestrator | None = None
_session_locks: dict[uuid.UUID, asyncio.Lock] = defaultdict(asyncio.Lock)


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/demo-session")
async def demo_session() -> dict:
    return await ensure_demo_session()


@app.websocket("/ws/session/{session_id}")
async def session_ws(ws: WebSocket, session_id: uuid.UUID) -> None:
    await ws.accept()

    async with db_session() as s:
        game_session = await s.get(GameSession, session_id)
    if game_session is None:
        await ws.send_text(ErrorEvent(message=f"unknown session {session_id}").model_dump_json())
        await ws.close()
        return

    async def emit(event: Event) -> None:
        await ws.send_text(event.model_dump_json())

    try:
        while True:
            raw = await ws.receive_text()
            try:
                action = PlayerAction.model_validate_json(raw)
            except ValidationError:
                await emit(ErrorEvent(message="expected {type: 'player_action', text: ...}"))
                continue
            if not action.text.strip():
                continue
            async with _session_locks[session_id]:
                try:
                    await get_orchestrator().run_turn(game_session, action.text, emit)
                except Exception:
                    log.exception("turn failed for session %s", session_id)
                    await emit(ErrorEvent(message="The DM stumbled (server error). Try again."))
    except WebSocketDisconnect:
        pass


def run() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run("dm_agent.main:app", host="127.0.0.1", port=8000, reload=True)
