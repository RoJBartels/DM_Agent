"""Deterministic dice engine. The LLM decides *what* to roll; this code rolls it.

Expression grammar (case-insensitive, whitespace ignored):

    expression := term (("+" | "-") term)*
    term       := [N]"d"M["kh"X | "kl"X]   e.g. d20, 2d6, 2d20kh1 (advantage)
                | integer                   flat modifier

Examples: "d20", "2d6+3", "2d20kh1+5" (advantage), "2d20kl1" (disadvantage),
"1d8+2d6+3".
"""

import random
import re
from dataclasses import dataclass, field

MAX_DICE = 100
MAX_SIDES = 1000

_TERM_RE = re.compile(r"^(?:(\d*)d(\d+)(?:(kh|kl)(\d+))?|(\d+))$", re.IGNORECASE)


class DiceError(ValueError):
    pass


@dataclass
class DiceResult:
    expression: str
    rolls: list[int] = field(default_factory=list)  # every die rolled, in order
    total: int = 0


def roll(expression: str, rng: random.Random | None = None) -> DiceResult:
    rng = rng or random.SystemRandom()
    expr = expression.replace(" ", "").lower()
    if not expr:
        raise DiceError("empty dice expression")

    # Split into signed terms: "2d6+3-1d4" -> [("+","2d6"), ("+","3"), ("-","1d4")]
    parts = re.split(r"([+-])", expr)
    if parts[0] == "":
        raise DiceError(f"expression may not start with an operator: {expression!r}")
    signs = ["+"] + parts[1::2]
    terms = parts[0::2]
    if len(signs) != len(terms) or any(t == "" for t in terms):
        raise DiceError(f"malformed dice expression: {expression!r}")

    result = DiceResult(expression=expression)
    for sign, term in zip(signs, terms):
        m = _TERM_RE.match(term)
        if not m:
            raise DiceError(f"malformed term {term!r} in {expression!r}")
        mult = 1 if sign == "+" else -1
        if m.group(5) is not None:  # flat modifier
            result.total += mult * int(m.group(5))
            continue
        count = int(m.group(1) or 1)
        sides = int(m.group(2))
        if not (1 <= count <= MAX_DICE) or not (2 <= sides <= MAX_SIDES):
            raise DiceError(f"unreasonable dice term {term!r}")
        rolls = [rng.randint(1, sides) for _ in range(count)]
        result.rolls.extend(rolls)
        kept = rolls
        if m.group(3):  # kh / kl
            keep = int(m.group(4))
            if not (1 <= keep <= count):
                raise DiceError(f"cannot keep {keep} of {count} dice in {term!r}")
            kept = sorted(rolls, reverse=(m.group(3) == "kh"))[:keep]
        result.total += mult * sum(kept)
    return result


def ability_modifier(score: int) -> int:
    return (score - 10) // 2


@dataclass
class CheckResult:
    roll: DiceResult
    dc: int
    success: bool
    critical: bool  # natural 20 (success) or natural 1 (failure) on a single d20


def check(modifier: int, dc: int, advantage: bool = False, disadvantage: bool = False,
          rng: random.Random | None = None) -> CheckResult:
    """d20 check vs DC. Advantage and disadvantage cancel out."""
    if advantage and not disadvantage:
        expr = "2d20kh1"
    elif disadvantage and not advantage:
        expr = "2d20kl1"
    else:
        expr = "1d20"
    base = roll(expr, rng)
    total = base.total + modifier
    natural = base.total  # the kept die
    return CheckResult(
        roll=DiceResult(expression=f"{expr}{modifier:+d}", rolls=base.rolls, total=total),
        dc=dc,
        success=(natural == 20) or (natural != 1 and total >= dc),
        critical=natural in (1, 20),
    )
