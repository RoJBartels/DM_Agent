import random

import pytest

from dm_agent.rules import DiceError, ability_modifier, check, roll


class FakeRng:
    """Deterministic randint source for exact-value assertions."""

    def __init__(self, values: list[int]):
        self.values = list(values)

    def randint(self, a: int, b: int) -> int:
        v = self.values.pop(0)
        assert a <= v <= b, f"fake value {v} outside [{a}, {b}]"
        return v


def test_simple_roll_shape():
    result = roll("2d6+3", random.Random(42))
    assert len(result.rolls) == 2
    assert all(1 <= r <= 6 for r in result.rolls)
    assert result.total == sum(result.rolls) + 3


def test_deterministic_with_seed():
    assert roll("4d8+1", random.Random(7)).total == roll("4d8+1", random.Random(7)).total


def test_single_die_defaults_to_one():
    result = roll("d20", FakeRng([13]))
    assert result.rolls == [13]
    assert result.total == 13


def test_advantage_keeps_highest():
    result = roll("2d20kh1", FakeRng([4, 17]))
    assert result.rolls == [4, 17]
    assert result.total == 17


def test_disadvantage_keeps_lowest():
    result = roll("2d20kl1+5", FakeRng([4, 17]))
    assert result.total == 4 + 5


def test_multi_term_and_negative():
    result = roll("1d8+2d6-2", FakeRng([5, 3, 4]))
    assert result.total == 5 + 3 + 4 - 2
    assert len(result.rolls) == 3


def test_subtracted_dice():
    result = roll("1d20-1d4", FakeRng([15, 3]))
    assert result.total == 12


def test_whitespace_and_case():
    result = roll(" 2D6 + 3 ", FakeRng([1, 2]))
    assert result.total == 6


@pytest.mark.parametrize(
    "expr",
    ["", "d", "0d6", "2d1", "+2d6", "2d6kh3", "2d6kh0", "abc", "2d6++3", "999d999999"],
)
def test_malformed_expressions_raise(expr):
    with pytest.raises(DiceError):
        roll(expr, random.Random(1))


def test_rolls_within_bounds_statistically():
    rng = random.Random(123)
    for _ in range(200):
        result = roll("3d6", rng)
        assert 3 <= result.total <= 18


def test_ability_modifier():
    assert ability_modifier(10) == 0
    assert ability_modifier(16) == 3
    assert ability_modifier(8) == -1
    assert ability_modifier(3) == -4


def test_check_success_and_failure():
    ok = check(modifier=5, dc=15, rng=FakeRng([12]))  # 12 + 5 = 17 >= 15
    assert ok.success and not ok.critical
    bad = check(modifier=1, dc=15, rng=FakeRng([10]))  # 10 + 1 = 11 < 15
    assert not bad.success


def test_natural_twenty_always_succeeds():
    result = check(modifier=-100, dc=15, rng=FakeRng([20]))
    assert result.success and result.critical


def test_natural_one_always_fails():
    result = check(modifier=100, dc=15, rng=FakeRng([1]))
    assert not result.success and result.critical


def test_check_advantage_uses_two_dice():
    result = check(modifier=0, dc=10, advantage=True, rng=FakeRng([3, 18]))
    assert result.roll.rolls == [3, 18]
    assert result.roll.total == 18
    assert result.success


def test_advantage_and_disadvantage_cancel():
    result = check(modifier=0, dc=10, advantage=True, disadvantage=True, rng=FakeRng([9]))
    assert result.roll.rolls == [9]
