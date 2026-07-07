import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from dm_agent.events import Event

EmitFn = Callable[[Event], Awaitable[None]]


@dataclass
class ToolContext:
    campaign_id: uuid.UUID
    session_id: uuid.UUID
    emit: EmitFn  # push a typed event onto the session's stream


ToolHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[str]]


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler

    @property
    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
