"""Money in integer minor units (cents). Never a float; rounding is half up, away from zero."""

from decimal import ROUND_HALF_UP, Decimal

_CENT = Decimal("0.01")


def _check_minor(minor: int) -> None:
    if not isinstance(minor, int) or isinstance(minor, bool):
        raise TypeError("money amounts are integer minor units")


def _check_decimal(value: Decimal) -> None:
    if not isinstance(value, Decimal):
        raise TypeError("use Decimal for rates and dollar amounts, never float")


def to_minor(dollars: Decimal) -> int:
    """Dollars as a Decimal to cents, rounding half up."""
    _check_decimal(dollars)
    return int((dollars / 1).quantize(_CENT, rounding=ROUND_HALF_UP) * 100)


def multiply(minor: int, rate: Decimal) -> int:
    """An amount times a rate (for example a tax rate), rounded half up to the cent."""
    _check_minor(minor)
    _check_decimal(rate)
    return int((Decimal(minor) * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def format_money(minor: int) -> str:
    """Cents as customer-facing text: 145000 -> "$1,450.00", -8900 -> "-$89.00"."""
    _check_minor(minor)
    sign = "-" if minor < 0 else ""
    dollars, cents = divmod(abs(minor), 100)
    return f"{sign}${dollars:,}.{cents:02d}"
