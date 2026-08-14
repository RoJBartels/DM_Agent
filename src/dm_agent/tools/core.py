"""M1 tools: dice, character sheets, world state."""

import json
from typing import Any

from sqlalchemy import select

from dm_agent.db import Character, WorldFlag, db_session
from dm_agent.events import DiceRoll, ModifierSource, StateUpdate
from dm_agent.rules import DiceError, DiceResult, check_outcome, roll
from dm_agent.tools.base import Tool, ToolContext

_OUTCOME_WORD = {
    "success": "SUCCESS",
    "failure": "FAILURE",
    "critical_success": "CRITICAL SUCCESS",
    "critical_failure": "CRITICAL FAILURE",
}


def _clean_breakdown(raw: Any) -> list[ModifierSource] | None:
    """Coerce the model's breakdown arg into typed sources, ignoring junk. Returns
    None when there's nothing usable so the field stays absent for damage rolls."""
    if not isinstance(raw, list):
        return None
    items: list[ModifierSource] = []
    for entry in raw:
        if isinstance(entry, dict) and "source" in entry and "value" in entry:
            try:
                items.append(ModifierSource(source=str(entry["source"]), value=int(entry["value"])))
            except (TypeError, ValueError):
                continue
    return items or None


_HIDDEN_PREFIX = (
    "[HIDDEN ROLL — rolled behind the DM screen. The player sees only that dice were "
    "rolled, never these numbers. Narrate only what their character can perceive: the "
    "effect, not the die, the DC, or the target's modifier.] "
)


def _describe_roll(result: DiceResult, dc: int | None, outcome: str | None,
                   breakdown: list[ModifierSource] | None) -> str:
    """The tool-result string handed back to the model. It states the engine's
    verdict explicitly so the narration matches the chip the player sees."""
    parts = [f"Rolled {result.expression}: dice {result.rolls}"]
    if result.modifier:
        parts.append(f"modifier {result.modifier:+d}")
    parts.append(f"total {result.total}")
    line = ", ".join(parts)
    if dc is not None:
        line += f" vs DC {dc} -> {_OUTCOME_WORD.get(outcome, outcome or 'unknown')}"
    if breakdown:
        line += " (" + ", ".join(f"{b.value:+d} {b.source}" for b in breakdown) + ")"
    return line


async def _roll_dice(ctx: ToolContext, args: dict[str, Any]) -> str:
    expression = args["expression"]
    purpose = args.get("purpose", "")
    try:
        result = roll(expression)
    except DiceError as e:
        return f"Error: {e}"
    dc = args.get("dc")
    dc = int(dc) if dc is not None else None
    outcome = check_outcome(result, dc) if dc is not None else None
    breakdown = _clean_breakdown(args.get("breakdown"))
    if args.get("hidden"):
        # M2l — behind the DM screen. The engine still rolled and judged (the model
        # gets the full result back to narrate from), but the event is emitted with
        # NOTHING mechanical: no expression, no faces, no modifier, no DC, no verdict.
        # `purpose` is dropped too — it is model-authored free text and could itself
        # carry the numbers we are screening ("Guard WIS save vs DC 14").
        await ctx.emit(DiceRoll(hidden=True))
        return _HIDDEN_PREFIX + _describe_roll(result, dc, outcome, breakdown)
    await ctx.emit(
        DiceRoll(
            expression=expression,
            rolls=result.rolls,
            total=result.total,
            purpose=purpose,
            modifier=result.modifier,
            kept=result.kept,
            dropped=result.dropped,
            dc=dc,
            outcome=outcome,
            breakdown=breakdown,
        )
    )
    return _describe_roll(result, dc, outcome, breakdown)


async def _find_character(ctx: ToolContext, session, name: str) -> Character | None:
    rows = await session.execute(select(Character).where(Character.campaign_id == ctx.campaign_id))
    for ch in rows.scalars():
        if ch.name.lower() == name.lower():
            return ch
    return None


def _sheet(ch: Character) -> dict[str, Any]:
    return {
        "name": ch.name,
        "is_pc": ch.is_pc,
        "stats": ch.stats,
        "hp": ch.hp,
        "max_hp": ch.max_hp,
        "ac": ch.ac,
        "inventory": ch.inventory,
        "notes": ch.notes,
    }


async def _get_character_sheet(ctx: ToolContext, args: dict[str, Any]) -> str:
    name = args.get("name")
    async with db_session() as session:
        if name:
            ch = await _find_character(ctx, session, name)
            if ch is None:
                return f"Error: no character named {name!r} in this campaign."
            return json.dumps(_sheet(ch))
        rows = await session.execute(
            select(Character).where(Character.campaign_id == ctx.campaign_id)
        )
        return json.dumps([_sheet(c) for c in rows.scalars()])


async def _update_character_sheet(ctx: ToolContext, args: dict[str, Any]) -> str:
    name = args["name"]
    changes: dict[str, Any] = {}
    async with db_session() as session:
        ch = await _find_character(ctx, session, name)
        if ch is None:
            return f"Error: no character named {name!r} in this campaign."
        if (delta := args.get("hp_delta")) is not None:
            ch.hp = max(0, min(ch.max_hp, ch.hp + int(delta)))
            changes["hp"] = ch.hp
        if (hp := args.get("hp")) is not None:
            ch.hp = max(0, min(ch.max_hp, int(hp)))
            changes["hp"] = ch.hp
        for item in args.get("add_items", []):
            ch.inventory = [*ch.inventory, item]
            changes["inventory"] = ch.inventory
        for item in args.get("remove_items", []):
            if item in ch.inventory:
                inv = list(ch.inventory)
                inv.remove(item)
                ch.inventory = inv
                changes["inventory"] = ch.inventory
        if (notes := args.get("notes")) is not None:
            ch.notes = notes
            changes["notes"] = notes
        await session.commit()
        sheet = _sheet(ch)
    if changes:
        await ctx.emit(StateUpdate(entity=name, changes=changes))
    return f"Updated {name}. Current sheet: {json.dumps(sheet)}"


async def _update_world_state(ctx: ToolContext, args: dict[str, Any]) -> str:
    key = args["key"]
    value = {"value": args["value"], "summary": args.get("summary", "")}
    async with db_session() as session:
        flag = await session.get(WorldFlag, (ctx.campaign_id, key))
        if flag is None:
            session.add(WorldFlag(campaign_id=ctx.campaign_id, key=key, value=value))
        else:
            flag.value = value
        await session.commit()
    await ctx.emit(StateUpdate(entity="world", changes={key: value["value"]}))
    return f"World state updated: {key} = {args['value']}"


TOOLS: list[Tool] = [
    Tool(
        name="roll_dice",
        description=(
            "Roll dice with a deterministic engine. Call this for EVERY roll — never invent "
            "results. Put the whole modifier in the expression (e.g. 'd20+5'); supports NdM+K "
            "terms and advantage/disadvantage via 2d20kh1 / 2d20kl1. For an ability check, save, "
            "or attack made against a target number, ALWAYS pass dc — the engine then reports "
            "success/failure/critical, and you must narrate the outcome it returns (don't "
            "re-judge it). Omit dc for damage and other free rolls. Whenever there's a modifier, "
            "itemize it in breakdown so the player sees where each bonus/penalty comes from. "
            "Set hidden=true for a roll the players are not entitled to see the numbers of — "
            "above all an NPC's or monster's saving throw, attack, or contested check, whose "
            "modifier would give away its stat block."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Dice expression, e.g. 'd20+5'"},
                "purpose": {
                    "type": "string",
                    "description": "What the roll is for, e.g. 'Perception (Kara)' or 'Fire damage'",
                },
                "dc": {
                    "type": "integer",
                    "description": (
                        "Target number for a d20 check/save/attack (roll ≥ dc succeeds; a "
                        "natural 20 always succeeds, a natural 1 always fails). Omit for damage."
                    ),
                },
                "breakdown": {
                    "type": "array",
                    "description": (
                        "The modifier's sources, e.g. "
                        "[{\"source\":\"DEX\",\"value\":3},{\"source\":\"proficiency\",\"value\":2}]."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "value": {"type": "integer"},
                        },
                        "required": ["source", "value"],
                    },
                },
                "hidden": {
                    "type": "boolean",
                    "description": (
                        "Roll it behind the DM screen. The player is shown only that a hidden "
                        "roll happened — never the die, modifier, DC, or verdict — so use it "
                        "for any roll that would reveal an NPC's or monster's stats (their "
                        "saving throws, attacks, opposed Stealth/Deception) or a secret the "
                        "characters can't know. A player's own declared check stays open: "
                        "when in doubt, roll openly."
                    ),
                },
            },
            "required": ["expression"],
        },
        handler=_roll_dice,
    ),
    Tool(
        name="get_character_sheet",
        description=(
            "Read character sheets (stats, HP, AC, inventory, notes). Call with a name for one "
            "character, or without arguments for all characters in the campaign."
        ),
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Character name (optional)"}},
        },
        handler=_get_character_sheet,
    ),
    Tool(
        name="update_character_sheet",
        description=(
            "Apply mechanical changes to a character: damage/healing via hp_delta (negative for "
            "damage), inventory changes, or notes. Use after rolls resolve."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "hp_delta": {"type": "integer", "description": "HP change; negative = damage"},
                "hp": {"type": "integer", "description": "Set HP to an absolute value"},
                "add_items": {"type": "array", "items": {"type": "string"}},
                "remove_items": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string", "description": "Replace the character's notes"},
            },
            "required": ["name"],
        },
        handler=_update_character_sheet,
    ),
    Tool(
        name="update_world_state",
        description=(
            "Record a durable fact or quest flag about the world, e.g. key='gate_of_thorns', "
            "value='opened'. Use for consequences that must persist across scenes."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "snake_case flag name"},
                "value": {"type": "string"},
                "summary": {"type": "string", "description": "One-line context for the change"},
            },
            "required": ["key", "value"],
        },
        handler=_update_world_state,
    ),
]
