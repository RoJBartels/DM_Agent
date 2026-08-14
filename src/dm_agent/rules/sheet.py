"""What a hero can actually do — the sheet side of 5e (M2j).

`rules/dice.py` adjudicates a roll once the DM knows what to roll; this module
answers the question that comes first: what is this character proficient in, and
what does that make their modifier? It reads `Character.stats` (free-form JSONB,
so every key here is optional) and renders a compact capability block for the
per-turn party roster — so the narrator knows a rogue carries thieves' tools
without spending a `get_character_sheet` round-trip, and can itemize an accurate
M2h breakdown from the roster alone.

Hardcoded 5e on purpose; M8 (multi-ruleset) generalizes the skill/ability tables
later, exactly as it will for the creator UI.
"""

from collections.abc import Mapping
from typing import Any

from dm_agent.rules.dice import ability_modifier

ABILITIES = ("STR", "DEX", "CON", "INT", "WIS", "CHA")

# The 18 SRD skills and the ability that governs each. (CON governs none.)
SKILL_ABILITY: dict[str, str] = {
    "Acrobatics": "DEX",
    "Animal Handling": "WIS",
    "Arcana": "INT",
    "Athletics": "STR",
    "Deception": "CHA",
    "History": "INT",
    "Insight": "WIS",
    "Intimidation": "CHA",
    "Investigation": "INT",
    "Medicine": "WIS",
    "Nature": "INT",
    "Perception": "WIS",
    "Performance": "CHA",
    "Persuasion": "CHA",
    "Religion": "INT",
    "Sleight of Hand": "DEX",
    "Stealth": "DEX",
    "Survival": "WIS",
}
_SKILL_BY_LOWER = {name.lower(): name for name in SKILL_ABILITY}

# Per-group caps. The roster goes into every turn's context, so one absurd sheet
# (a wizard with 200 spells typed in) must not be able to inflate the prompt.
_CAPS = {"skills": 10, "tools": 6, "spells": 12, "features": 6}


def _fmt(value: int) -> str:
    return f"{value:+d}"


def _names(raw: Any) -> list[str]:
    """Coerce a stats entry into a clean, de-duplicated list of names.

    Tolerates the shapes a hand-edited sheet can hold: a list, or a comma- or
    newline-separated string. Anything else yields nothing rather than raising —
    a malformed sheet must never break a turn.
    """
    if isinstance(raw, str):
        raw = raw.replace("\n", ",").split(",")
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        name = str(item).strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def _capped(names: list[str], kind: str) -> str:
    limit = _CAPS[kind]
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f", +{len(names) - limit} more"


def _int(raw: Any) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def proficiency_bonus(stats: Mapping[str, Any]) -> int:
    """The sheet's stored bonus, or the 5e level-derived one (2 at levels 1–4,
    +1 every four levels) when it is missing or unusable."""
    stored = _int(stats.get("proficiency_bonus"))
    if stored is not None:
        return stored
    level = _int(stats.get("level")) or 1
    return 2 + (max(1, level) - 1) // 4


def sheet_ability_modifier(stats: Mapping[str, Any], ability: str) -> int | None:
    """The 5e modifier for one of a sheet's abilities, or None if it has no such
    score. (`dice.ability_modifier` does the arithmetic; this reads the sheet.)"""
    score = _int(stats.get(ability))
    return None if score is None else ability_modifier(score)


def has_ability_scores(stats: Mapping[str, Any]) -> bool:
    return any(_int(stats.get(a)) is not None for a in ABILITIES)


def canonical_skill(name: str) -> str | None:
    """Map a sheet's spelling onto an SRD skill, or None for a homebrew one."""
    return _SKILL_BY_LOWER.get(str(name).strip().lower())


def skill_modifier(stats: Mapping[str, Any], skill: str) -> int | None:
    """Total modifier for a skill check: ability + proficiency (doubled by
    expertise). None when the skill isn't an SRD skill or the sheet carries no
    ability scores — in that case a number would be a guess, and the whole point
    of this milestone is that the narrator stops guessing."""
    canon = canonical_skill(skill)
    if canon is None:
        return None
    base = sheet_ability_modifier(stats, SKILL_ABILITY[canon])
    if base is None:
        return None
    proficient = {s.lower() for s in _names(stats.get("skills"))}
    expert = {s.lower() for s in _names(stats.get("expertise"))}
    bonus = proficiency_bonus(stats)
    total = base
    if canon.lower() in proficient or canon.lower() in expert:
        total += bonus
    if canon.lower() in expert:
        total += bonus
    return total


def _skill_entries(stats: Mapping[str, Any], scored: bool) -> list[str]:
    """Proficient skills, expertise marked, in sheet order (expertise-only skills
    appended). A skill listed under expertise counts as proficient too."""
    expert = _names(stats.get("expertise"))
    expert_lower = {s.lower() for s in expert}
    ordered = _names(stats.get("skills"))
    ordered += [s for s in expert if s.lower() not in {o.lower() for o in ordered}]

    entries: list[str] = []
    for skill in ordered:
        canon = canonical_skill(skill) or skill
        mod = skill_modifier(stats, skill) if scored else None
        label = canon if mod is None else f"{canon} {_fmt(mod)}"
        if canon.lower() in expert_lower:
            label += " (expertise)"
        entries.append(label)
    return entries


def _save_entries(stats: Mapping[str, Any], scored: bool) -> list[str]:
    bonus = proficiency_bonus(stats)
    entries: list[str] = []
    for ability in _names(stats.get("save_proficiencies")):
        abil = ability.strip().upper()
        if abil not in ABILITIES:
            continue
        base = sheet_ability_modifier(stats, abil) if scored else None
        entries.append(abil if base is None else f"{abil} {_fmt(base + bonus)}")
    return entries


def format_capabilities(stats: Mapping[str, Any]) -> list[str]:
    """Render a character's capabilities as 0–3 compact lines for the roster.

    Grouped rather than one long line so the narrator can find a number fast, and
    every group is omitted when the sheet has nothing for it — a bare sheet (the
    pre-M2j shape) renders no capability lines at all.
    """
    if not isinstance(stats, Mapping):
        return []
    lines: list[str] = []
    scored = has_ability_scores(stats)

    if scored:
        mods = [
            f"{a} {_fmt(m)}"
            for a in ABILITIES
            if (m := sheet_ability_modifier(stats, a)) is not None
        ]
        lines.append(
            f"Abilities: {', '.join(mods)} · proficiency {_fmt(proficiency_bonus(stats))}"
        )

    trained: list[str] = []
    if skills := _skill_entries(stats, scored):
        trained.append(f"Skills: {_capped(skills, 'skills')}")
    if saves := _save_entries(stats, scored):
        trained.append(f"Saves: {', '.join(saves)}")
    if tools := _names(stats.get("tools")):
        trained.append(f"Tools: {_capped(tools, 'tools')}")
    if trained:
        lines.append(" · ".join(trained))

    known: list[str] = []
    if spells := _names(stats.get("spells")):
        known.append(f"Spells: {_capped(spells, 'spells')}")
    if features := _names(stats.get("features")):
        known.append(f"Features: {_capped(features, 'features')}")
    if known:
        lines.append(" · ".join(known))

    return lines
