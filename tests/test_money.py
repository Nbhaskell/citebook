from decimal import Decimal

import pytest

from citebook.money import format_money, multiply, to_minor


@pytest.mark.parametrize(
    ("minor", "text"),
    [
        (0, "$0.00"),
        (8900, "$89.00"),
        (14900, "$149.00"),
        (145000, "$1,450.00"),
        (-8900, "-$89.00"),
        (5, "$0.05"),
        (123456789, "$1,234,567.89"),
    ],
)
def test_format_money(minor: int, text: str) -> None:
    assert format_money(minor) == text


@pytest.mark.parametrize(
    ("dollars", "minor"),
    [
        (Decimal("89"), 8900),
        (Decimal("0.005"), 1),  # half up, not to even
        (Decimal("0.015"), 2),
        (Decimal("0.025"), 3),  # banker's rounding would give 2
        (Decimal("-0.005"), -1),  # half away from zero
        (Decimal("1.004"), 100),
    ],
)
def test_to_minor_rounds_half_up(dollars: Decimal, minor: int) -> None:
    assert to_minor(dollars) == minor


def test_multiply_rounds_half_up() -> None:
    assert multiply(11500, Decimal("1.5")) == 17250
    assert multiply(1001, Decimal("0.07")) == 70  # 70.07
    assert multiply(50, Decimal("0.07")) == 4  # 3.5 rounds up
    assert multiply(150, Decimal("0.07")) == 11  # 10.5 rounds up


@pytest.mark.parametrize("bad", [1.5, "1.5", None])
def test_floats_and_other_types_are_refused(bad: object) -> None:
    with pytest.raises(TypeError):
        to_minor(bad)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        multiply(100, bad)  # type: ignore[arg-type]


def test_minor_units_must_be_integers() -> None:
    with pytest.raises(TypeError):
        format_money(89.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        format_money(True)
