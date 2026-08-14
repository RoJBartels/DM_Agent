"""Pure-logic tests for M2d transcript replay and M2e party roster — no DB, no
torch. Both functions are the reconstruction/formatting core the endpoints and
orchestrator wrap."""

from __future__ import annotations

from types import SimpleNamespace

from dm_agent.api.management import reconstruct_transcript
from dm_agent.orchestrator.loop import format_party_roster


# --- transcript reconstruction (M2d) ---------------------------------------


def test_reconstruct_transcript_interleaves_prose_and_events():
    history = [
        {"role": "user", "content": "I open the door."},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "hidden reasoning"},
                {"type": "text", "text": "The door creaks open."},
                {"type": "tool_use", "id": "t1", "name": "roll_dice", "input": {"expression": "d20"}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok", "is_error": False}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "You spot a rune."}]},
        {"role": "user", "content": "I touch the rune."},
        {"role": "assistant", "content": [{"type": "text", "text": "It glows."}]},
    ]
    events = [
        {"type": "turn_start", "turn_id": "turn-0"},
        {"type": "dice_roll", "expression": "d20", "rolls": [14], "total": 14, "purpose": "perception"},
        {"type": "state_update", "entity": "world", "changes": {"rune_found": "true"}},
        {"type": "turn_end", "turn_id": "turn-0"},
        {"type": "turn_start", "turn_id": "turn-2"},
        {"type": "turn_end", "turn_id": "turn-2"},
    ]

    items = reconstruct_transcript(history, events)
    kinds = [it["kind"] for it in items]
    assert kinds == ["player", "narration", "narration", "dice", "state", "player", "narration"]

    assert items[0] == {"kind": "player", "text": "I open the door."}
    assert items[1]["text"] == "The door creaks open."
    assert items[2]["text"] == "You spot a rune."
    # A pre-M2h logged event carries no check fields → they default (chip degrades).
    assert items[3] == {
        "kind": "dice",
        "expression": "d20",
        "rolls": [14],
        "total": 14,
        "purpose": "perception",
        "modifier": 0,
        "kept": None,
        "dropped": None,
        "dc": None,
        "outcome": None,
        "breakdown": None,
    }
    assert items[4] == {"kind": "state", "entity": "world", "changes": {"rune_found": "true"}}
    assert items[5]["text"] == "I touch the rune."
    assert items[6]["text"] == "It glows."


def test_reconstruct_transcript_carries_m2h_check_fields():
    # A check event's dc/outcome/kept/dropped/modifier/breakdown must survive into
    # the replay item so a resumed chip reads identically to the live one (M2h).
    history = [
        {"role": "user", "content": "I search for traps."},
        {"role": "assistant", "content": [{"type": "text", "text": "You scan the floor."}]},
    ]
    events = [
        {"type": "turn_start", "turn_id": "turn-0"},
        {
            "type": "dice_roll",
            "expression": "2d20kh1+5",
            "rolls": [7, 14],
            "kept": [14],
            "dropped": [7],
            "modifier": 5,
            "total": 19,
            "dc": 15,
            "outcome": "success",
            "purpose": "Perception (Kara)",
            "breakdown": [{"source": "DEX", "value": 3}, {"source": "proficiency", "value": 2}],
        },
        {"type": "turn_end", "turn_id": "turn-0"},
    ]
    items = reconstruct_transcript(history, events)
    dice = next(it for it in items if it["kind"] == "dice")
    assert dice["dc"] == 15
    assert dice["outcome"] == "success"
    assert dice["kept"] == [14]
    assert dice["dropped"] == [7]
    assert dice["modifier"] == 5
    assert dice["breakdown"][0] == {"source": "DEX", "value": 3}


def test_reconstruct_transcript_keeps_hidden_rolls_screened():
    # M2l — a screened roll is logged already redacted; replay must not widen it
    # back out. Even if a stray field survived in the log, it stays out of the item.
    history = [
        {"role": "user", "content": "I cast hold person on the guard."},
        {"role": "assistant", "content": [{"type": "text", "text": "The guard shrugs it off."}]},
    ]
    events = [
        {"type": "turn_start", "turn_id": "turn-0"},
        {"type": "dice_roll", "hidden": True, "dc": 14, "total": 18, "purpose": "Guard WIS save"},
        {"type": "turn_end", "turn_id": "turn-0"},
    ]
    items = reconstruct_transcript(history, events)
    dice = next(it for it in items if it["kind"] == "dice")
    assert dice == {"kind": "dice", "hidden": True}
    assert "14" not in str(items) and "18" not in str(items)


def test_reconstruct_transcript_empty():
    assert reconstruct_transcript([], []) == []


def test_reconstruct_transcript_history_only_no_events():
    # A session with no logged typed events still replays the narrative backbone.
    history = [
        {"role": "user", "content": "Look around."},
        {"role": "assistant", "content": [{"type": "text", "text": "A quiet tavern."}]},
    ]
    items = reconstruct_transcript(history, [])
    assert [it["kind"] for it in items] == ["player", "narration"]


def test_reconstruct_transcript_skips_tool_and_thinking_blocks():
    # Tool-use / tool-result / thinking blocks are mechanical, not transcript.
    history = [
        {"role": "user", "content": "Hi."},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "secret"},
                {"type": "tool_use", "id": "x", "name": "get_character_sheet", "input": {}},
            ],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "{}"}]},
    ]
    items = reconstruct_transcript(history, [])
    assert items == [{"kind": "player", "text": "Hi."}]


# --- party roster (M2e) ----------------------------------------------------


def _char(**kw):
    base = dict(name="X", is_pc=True, stats={}, hp=10, max_hp=10, ac=10, notes="")
    base.update(kw)
    return SimpleNamespace(**base)


def test_format_party_roster_empty():
    assert format_party_roster([]) == ""


def test_format_party_roster_content():
    chars = [
        _char(
            name="Kara", is_pc=True,
            stats={"class": "rogue", "level": 3}, hp=24, max_hp=24, ac=15,
            notes="Quick-tempered scout.\nSecond line ignored.",
        ),
        _char(name="Old Ben", is_pc=False, stats={}, hp=8, max_hp=8, ac=11, notes=""),
    ]
    out = format_party_roster(chars)
    # Header frames it as advisory + explains the "As <Name>:" convention.
    assert "As <Name>:" in out
    assert "player agency remains absolute" in out.lower()
    # PC line: role, class/level, HP, AC, first notes line only.
    assert "Kara (PC)" in out
    assert "rogue 3" in out
    assert "HP 24/24" in out
    assert "AC 15" in out
    assert "Quick-tempered scout." in out
    assert "Second line ignored." not in out
    # NPC line present and labelled.
    assert "Old Ben (NPC)" in out
    assert "HP 8/8" in out
