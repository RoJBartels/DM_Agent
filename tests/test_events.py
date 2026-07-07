import pytest
from pydantic import ValidationError

from dm_agent import events
from dm_agent.events import PlayerAction, event_adapter

SAMPLES = [
    events.TurnStart(turn_id="turn-1"),
    events.NarrationDelta(text="The door creaks open."),
    events.DiceRoll(expression="2d6+3", rolls=[4, 2], total=9, purpose="damage"),
    events.StateUpdate(entity="Kara", changes={"hp": 17}),
    events.Sfx(tag="door_creak"),
    events.Ambience(tag="tavern_busy"),
    events.Effect(tag="fireball", x=3, y=7),
    events.Visual(url="https://example.com/a.png", caption="the gate"),
    events.MapUpdate(payload={"tokens": []}),
    events.TurnEnd(turn_id="turn-1"),
    events.ErrorEvent(message="boom"),
]


@pytest.mark.parametrize("event", SAMPLES, ids=lambda e: e.type)
def test_event_round_trip(event):
    wire = event.model_dump_json()
    parsed = event_adapter.validate_json(wire)
    assert parsed == event


def test_unknown_event_type_rejected():
    with pytest.raises(ValidationError):
        event_adapter.validate_python({"type": "teleport", "to": "moon"})


def test_player_action_parses():
    action = PlayerAction.model_validate_json('{"type": "player_action", "text": "open the door"}')
    assert action.text == "open the door"


def test_app_imports():
    from dm_agent.main import app  # noqa: F401 — catches wiring/syntax errors early
