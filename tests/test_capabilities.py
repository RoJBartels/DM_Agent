"""M2j: the sheet knows what a hero can do, and the roster says so.

The defect this closes: the narrator had no way to know a rogue carries thieves'
tools, so it couldn't judge what a character could attempt or itemize an accurate
modifier. These are pure-logic tests — no DB, no torch.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from dm_agent.orchestrator.loop import format_party_roster
from dm_agent.rules.sheet import (
    format_capabilities,
    proficiency_bonus,
    skill_modifier,
)

ROGUE = {
    "class": "Rogue",
    "level": 3,
    "proficiency_bonus": 2,
    "STR": 10, "DEX": 16, "CON": 14, "INT": 13, "WIS": 12, "CHA": 8,
    "skills": ["Stealth", "Sleight of Hand", "Perception", "Investigation"],
    "expertise": ["Stealth"],
    "save_proficiencies": ["DEX", "INT"],
    "tools": ["thieves' tools"],
    "spells": [],
    "features": ["Sneak Attack", "Cunning Action"],
}


# --- modifier arithmetic ---------------------------------------------------


def test_skill_modifier_adds_ability_and_proficiency():
    # Stealth: DEX +3, proficient +2, expertise doubles proficiency → +7.
    assert skill_modifier(ROGUE, "Stealth") == 7
    # Sleight of Hand: DEX +3, proficient, no expertise → +5.
    assert skill_modifier(ROGUE, "Sleight of Hand") == 5
    # Perception: WIS +1, proficient → +3.
    assert skill_modifier(ROGUE, "Perception") == 3


def test_skill_modifier_untrained_is_the_bare_ability():
    # Athletics isn't on the sheet: STR 10 → +0, no proficiency bonus.
    assert skill_modifier(ROGUE, "Athletics") == 0


def test_skill_modifier_is_case_insensitive_and_rejects_unknown_skills():
    assert skill_modifier(ROGUE, "stealth") == 7
    assert skill_modifier(ROGUE, "Basket Weaving") is None


def test_skill_modifier_without_scores_refuses_to_guess():
    # No ability scores on the sheet → a number would be invented, so: None.
    assert skill_modifier({"skills": ["Stealth"]}, "Stealth") is None


def test_proficiency_bonus_derives_from_level_when_absent():
    assert proficiency_bonus({"level": 1}) == 2
    assert proficiency_bonus({"level": 4}) == 2
    assert proficiency_bonus({"level": 5}) == 3
    assert proficiency_bonus({"level": 17}) == 6
    assert proficiency_bonus({}) == 2
    # A stored bonus wins — the creator lets it be overridden by hand.
    assert proficiency_bonus({"level": 1, "proficiency_bonus": 4}) == 4


# --- capability rendering --------------------------------------------------


def test_format_capabilities_renders_grouped_lines():
    lines = format_capabilities(ROGUE)
    assert len(lines) == 3
    assert lines[0] == (
        "Abilities: STR +0, DEX +3, CON +2, INT +1, WIS +1, CHA -1 · proficiency +2"
    )
    assert "Skills: Stealth +7 (expertise), Sleight of Hand +5" in lines[1]
    assert "Saves: DEX +5, INT +3" in lines[1]
    assert "Tools: thieves' tools" in lines[1]
    # Empty spell list contributes nothing; features still render.
    assert lines[2] == "Features: Sneak Attack, Cunning Action"


def test_format_capabilities_of_a_bare_sheet_is_empty():
    # The pre-M2j shape (and an NPC with nothing filled in) adds no lines at all.
    assert format_capabilities({}) == []
    assert format_capabilities({"class": "guard"}) == []


def test_format_capabilities_tolerates_hand_edited_shapes():
    # stats is free-form JSONB: a comma string, stray whitespace, and duplicates
    # are all things a hand-edited sheet can hold. None of them may raise.
    stats = {
        "DEX": 14,
        "skills": " Stealth , stealth,  Acrobatics ",
        "expertise": None,
        "tools": 42,
    }
    lines = format_capabilities(stats)
    assert "Skills: Stealth +4, Acrobatics +4" in lines[1]
    assert "Tools" not in lines[1]


def test_expertise_alone_implies_proficiency():
    stats = {"DEX": 16, "proficiency_bonus": 3, "expertise": ["Stealth"]}
    # Listed only under expertise → still proficient: 3 + 3 + 3 = +9.
    assert skill_modifier(stats, "Stealth") == 9
    assert "Stealth +9 (expertise)" in format_capabilities(stats)[1]


def test_capability_lists_are_capped():
    # The roster is re-sent every turn, so one absurd sheet must not inflate it.
    stats = {"INT": 10, "spells": [f"spell {i}" for i in range(20)]}
    line = format_capabilities(stats)[1]
    assert "spell 11" in line and "spell 12" not in line
    assert "+8 more" in line


def test_capabilities_without_scores_list_names_but_no_numbers():
    stats = {"skills": ["Stealth"], "save_proficiencies": ["DEX"], "tools": ["lute"]}
    lines = format_capabilities(stats)
    assert lines == ["Skills: Stealth · Saves: DEX · Tools: lute"]


# --- the roster block ------------------------------------------------------


def _char(**kw):
    base = dict(
        id=uuid.uuid4(), name="X", is_pc=True, stats={}, hp=10, max_hp=10, ac=10, notes=""
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_party_roster_carries_capabilities():
    out = format_party_roster([_char(name="Kara", stats=ROGUE, hp=24, max_hp=24, ac=15)])
    assert "Kara (PC) — Rogue 3 · HP 24/24 · AC 15." in out
    # Indented under the character's line, so it's clear whose they are.
    assert "\n    Abilities: STR +0, DEX +3" in out
    assert "thieves' tools" in out  # the "how does the DM know I can pick locks" fix
    assert "get_character_sheet" in out  # full sheets are still one call away


def test_party_roster_of_bare_characters_is_unchanged():
    out = format_party_roster([_char(name="Old Ben", is_pc=False, hp=8, max_hp=8, ac=11)])
    assert out.count("\n") == 1  # header + one line, no capability lines
    assert "Old Ben (NPC) — HP 8/8 · AC 11." in out
