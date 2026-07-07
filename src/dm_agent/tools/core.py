"""M1 tools: dice, character sheets, world state."""

import json
from typing import Any

from sqlalchemy import select

from dm_agent.db import Character, WorldFlag, db_session
from dm_agent.events import DiceRoll, StateUpdate
from dm_agent.rules import DiceError, roll
from dm_agent.tools.base import Tool, ToolContext


async def _roll_dice(ctx: ToolContext, args: dict[str, Any]) -> str:
    expression = args["expression"]
    purpose = args.get("purpose", "")
    try:
        result = roll(expression)
    except DiceError as e:
        return f"Error: {e}"
    await ctx.emit(
        DiceRoll(expression=expression, rolls=result.rolls, total=result.total, purpose=purpose)
    )
    return f"Rolled {expression}: dice {result.rolls} -> total {result.total}"


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
            "results. Supports NdM+K terms and advantage/disadvantage via 2d20kh1 / 2d20kl1 "
            "(e.g. 'd20', '2d6+3', '2d20kh1+5')."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Dice expression, e.g. '2d6+3'"},
                "purpose": {
                    "type": "string",
                    "description": "What the roll is for, e.g. 'DC 15 Dex save (Kara)'",
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
