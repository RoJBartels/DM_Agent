"""Typed event stream: the wire format between server and stage (§6 of the architecture).

The narration model's output is an event stream, not just text — prose deltas
interleaved with typed events. The rules engine emits events too. The stage
renders whatever arrives; adding new event types must never break old clients,
so clients ignore unknown types.
"""

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter


class TurnStart(BaseModel):
    type: Literal["turn_start"] = "turn_start"
    turn_id: str


class NarrationDelta(BaseModel):
    type: Literal["narration_delta"] = "narration_delta"
    text: str


class DiceRoll(BaseModel):
    type: Literal["dice_roll"] = "dice_roll"
    expression: str
    rolls: list[int]
    total: int
    purpose: str = ""


class StateUpdate(BaseModel):
    type: Literal["state_update"] = "state_update"
    entity: str
    changes: dict[str, Any]


class Sfx(BaseModel):
    type: Literal["sfx"] = "sfx"
    tag: str


class Ambience(BaseModel):
    type: Literal["ambience"] = "ambience"
    tag: str


class Effect(BaseModel):
    type: Literal["effect"] = "effect"
    tag: str
    x: int | None = None
    y: int | None = None


class Visual(BaseModel):
    type: Literal["visual"] = "visual"
    url: str
    caption: str = ""


class MapUpdate(BaseModel):
    type: Literal["map_update"] = "map_update"
    payload: dict[str, Any]


class TurnEnd(BaseModel):
    type: Literal["turn_end"] = "turn_end"
    turn_id: str


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


Event = Annotated[
    Union[
        TurnStart,
        NarrationDelta,
        DiceRoll,
        StateUpdate,
        Sfx,
        Ambience,
        Effect,
        Visual,
        MapUpdate,
        TurnEnd,
        ErrorEvent,
    ],
    Field(discriminator="type"),
]

event_adapter: TypeAdapter[Event] = TypeAdapter(Event)


class PlayerAction(BaseModel):
    """Client → server message."""

    type: Literal["player_action"] = "player_action"
    text: str
