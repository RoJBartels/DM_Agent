"""M2h: the roll_dice tool surfaces the check verdict + modifier provenance.

The tool emits a widened DiceRoll event and returns a string that states the
engine's outcome, so the narration the model writes matches the chip the player
sees. Flat expressions (e.g. "10") make the outcome deterministic without an
injectable RNG; real dice are used only where we assert shape, not exact faces.
"""

from __future__ import annotations

import uuid

from dm_agent.events import DiceRoll
from dm_agent.tools.base import ToolContext
from dm_agent.tools.core import _roll_dice


def _ctx(sink: list) -> ToolContext:
    async def emit(ev):
        sink.append(ev)

    return ToolContext(campaign_id=uuid.uuid4(), session_id=uuid.uuid4(), emit=emit)


async def test_check_reports_outcome_and_breakdown():
    events: list = []
    out = await _roll_dice(
        _ctx(events),
        {
            "expression": "10",  # flat 10, no natural → deterministic
            "purpose": "Perception (Kara)",
            "dc": 15,
            "breakdown": [
                {"source": "DEX", "value": 3},
                {"source": "proficiency", "value": 2},
                {"source": "junk"},  # malformed → dropped
            ],
        },
    )
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, DiceRoll)
    assert ev.dc == 15
    assert ev.outcome == "failure"  # 10 < 15, no natural to override
    assert ev.modifier == 10
    assert [b.source for b in ev.breakdown] == ["DEX", "proficiency"]
    assert "vs DC 15" in out and "FAILURE" in out


async def test_flat_check_can_succeed():
    events: list = []
    out = await _roll_dice(_ctx(events), {"expression": "10", "dc": 8})
    assert events[0].outcome == "success"
    assert "SUCCESS" in out


async def test_damage_roll_has_no_dc_or_outcome():
    events: list = []
    out = await _roll_dice(_ctx(events), {"expression": "2d6+3", "purpose": "Fire damage"})
    ev = events[0]
    assert ev.dc is None and ev.outcome is None
    assert ev.modifier == 3
    assert ev.kept is not None and len(ev.kept) == 2  # both d6 counted
    assert not ev.dropped
    assert ev.breakdown is None
    assert "vs DC" not in out


async def test_advantage_marks_kept_and_dropped():
    events: list = []
    await _roll_dice(_ctx(events), {"expression": "2d20kh1+5", "dc": 10})
    ev = events[0]
    assert len(ev.kept) == 1 and len(ev.dropped) == 1
    assert ev.outcome in {"success", "failure", "critical_success", "critical_failure"}


async def test_invalid_expression_errors_without_emitting():
    events: list = []
    out = await _roll_dice(_ctx(events), {"expression": "not-dice"})
    assert out.startswith("Error:")
    assert events == []
