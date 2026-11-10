"""Find money amounts, clock times, dates and numbers with units in text, for the reply checks.

Extraction is deliberately conservative (TRD section 8): anything that looks like a figure is
found, including number words ("a hundred and forty-nine dollars", "twenty-four hours"), and the
reply checks fail any figure they cannot match. The same functions read the allowed sources, so a
figure is compared in one normal form on both sides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

UNITS = {
    "zero": 0, "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}  # fmt: skip
TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}  # fmt: skip
SCALES = {"hundred": 100, "thousand": 1000}
_NUMBER_WORD = "|".join(sorted([*UNITS, *TENS, *SCALES], key=len, reverse=True))
# A run of number words, joined by spaces, hyphens or "and".
WORDS = rf"(?:(?:{_NUMBER_WORD})(?:(?:\s+and\s+|[\s-]+)(?:{_NUMBER_WORD}))*)"
WORDS_RE = re.compile(rf"\b{WORDS}\b", re.IGNORECASE)


def words_to_int(text: str) -> int | None:
    """'a hundred and forty-nine' -> 149; None when the words are not one number."""
    tokens = [t for t in re.split(r"[\s-]+", text.lower().strip()) if t and t != "and"]
    if not tokens:
        return None
    total, current, seen = 0, 0, False
    for token in tokens:
        if token in UNITS:
            if current % 10 and token not in {"a", "an"}:
                return None  # "five three" is not one number
            current += UNITS[token]
        elif token in TENS:
            if current % 100:
                return None
            current += TENS[token]
        elif token == "hundred":
            current = (current or 1) * 100
        elif token == "thousand":
            total += (current or 1) * 1000
            current = 0
        else:
            return None
        seen = True
    if tokens == ["a"] or tokens == ["an"]:
        return None  # an article alone is not a number
    return total + current if seen else None


def _number(text: str) -> int | None:
    text = text.replace(",", "")
    if re.fullmatch(r"\d+", text):
        return int(text)
    return words_to_int(text)


# --- money ------------------------------------------------------------------------------------

_DOLLAR_SIGN = re.compile(r"(-)?\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?")
_DOLLAR_WORD = re.compile(
    rf"\b(\d{{1,3}}(?:,\d{{3}})+|\d+|{WORDS})(?:\.(\d{{2}}))?\s+(?:dollars?|bucks|usd)\b"
    rf"(?:\s+and\s+(\d{{1,2}}|{WORDS})\s+cents?)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Found:
    raw: str
    value: Any


def find_money(text: str) -> list[Found]:
    """Amounts in cents, as positive numbers (a credit line '-$89.00' is 8900)."""
    found: list[Found] = []
    taken: list[tuple[int, int]] = []
    for match in _DOLLAR_SIGN.finditer(text):
        cents = int((match.group(3) or "0").ljust(2, "0"))
        found.append(Found(match.group(0), int(match.group(2).replace(",", "")) * 100 + cents))
        taken.append(match.span())
    for match in _DOLLAR_WORD.finditer(text):
        if any(start <= match.start() < end for start, end in taken):
            continue
        dollars = _number(match.group(1))
        if dollars is None:
            continue
        cents = int(match.group(2) or 0)
        if match.group(3):
            cents = _number(match.group(3)) or 0
        found.append(Found(match.group(0), dollars * 100 + cents))
    return found


# --- clock times ------------------------------------------------------------------------------

_MERIDIEM = r"(a\.?\s?m\.?|p\.?\s?m\.?)"
_RANGE = re.compile(
    rf"\b(\d{{1,2}})(?::(\d\d))?\s*{_MERIDIEM}?\s*(?:-|\u2013|\u2014|to|until)\s*"
    rf"(\d{{1,2}})(?::(\d\d))?\s*{_MERIDIEM}(?![a-z])",
    re.IGNORECASE,
)
_SINGLE = re.compile(
    rf"\b(\d{{1,2}})(?::(\d\d))?\s*{_MERIDIEM}(?![a-z])|\b(\d{{1,2}}):(\d\d)\b", re.IGNORECASE
)
_NAMED = re.compile(r"\b(noon|midday|midnight)\b", re.IGNORECASE)


def _minutes(hour: int, minute: int, meridiem: str | None) -> frozenset[int]:
    if hour > 23 or minute > 59:
        return frozenset()
    if meridiem is None:
        if hour > 12 or hour == 0:
            return frozenset({hour * 60 + minute})
        return frozenset({(hour % 12) * 60 + minute, (hour % 12 + 12) * 60 + minute})
    pm = meridiem.lower().startswith("p")
    if hour > 12:
        return frozenset()
    return frozenset({((hour % 12) + (12 if pm else 0)) * 60 + minute})


def find_times(text: str) -> list[Found]:
    """Clock times as sets of possible minutes after midnight (a time without AM or PM has two).
    In a range such as '1:00-3:00 PM' the first time takes the second one's AM or PM."""
    found: list[Found] = []
    taken: list[tuple[int, int]] = []
    for match in _RANGE.finditer(text):
        h1, m1, mer1, h2, m2, mer2 = match.groups()
        first_mer = mer1
        if first_mer is None:  # "11:00-1:00 PM" starts in the morning
            first_mer = mer2 if int(h1) % 12 <= int(h2) % 12 else ("a" if mer2[0] in "pP" else "p")
        found.append(Found(match.group(0), _minutes(int(h1), int(m1 or 0), first_mer)))
        found.append(Found(match.group(0), _minutes(int(h2), int(m2 or 0), mer2)))
        taken.append(match.span())
    for match in _SINGLE.finditer(text):
        if any(start <= match.start() < end for start, end in taken):
            continue
        if match.group(1):
            minutes = _minutes(int(match.group(1)), int(match.group(2) or 0), match.group(3))
        else:
            minutes = _minutes(int(match.group(4)), int(match.group(5)), None)
        found.append(Found(match.group(0), minutes))
    for match in _NAMED.finditer(text):
        found.append(Found(match.group(0), frozenset({0 if match.group(1) == "midnight" else 720})))
    return [f for f in found if f.value]


# --- dates ------------------------------------------------------------------------------------

MONTHS = [
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december",
]  # fmt: skip
_MONTH = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_DAY_MONTH = re.compile(rf"\b{_DAY}(?:\s+of)?\s+{_MONTH}\b", re.IGNORECASE)
_MONTH_DAY = re.compile(rf"\b{_MONTH}\.?\s+{_DAY}\b", re.IGNORECASE)
_ISO = re.compile(r"\b(\d{4})-(\d\d)-(\d\d)\b")


def _month(name: str) -> int:
    return next(i for i, m in enumerate(MONTHS, start=1) if m.startswith(name.lower()[:3]))


def find_dates(text: str) -> list[Found]:
    """Specific dates as (month, day). Weekday names alone are not dates."""
    found: list[Found] = []
    for match in _ISO.finditer(text):
        found.append(Found(match.group(0), (int(match.group(2)), int(match.group(3)))))
    for match in _DAY_MONTH.finditer(text):
        found.append(Found(match.group(0), (_month(match.group(2)), int(match.group(1)))))
    for match in _MONTH_DAY.finditer(text):
        found.append(Found(match.group(0), (_month(match.group(1)), int(match.group(2)))))
    return [f for f in found if 1 <= f.value[1] <= 31]


# --- numbers with policy units ----------------------------------------------------------------

_UNIT = re.compile(
    rf"\b(\d+|{WORDS})(?:[\s-]+business)?[\s-]+(hours?|days?|minutes?|weeks?|months?|years?|"
    r"times|attempts?)\b",
    re.IGNORECASE,
)
_UNIT_NAMES = {
    "hour": "hours", "day": "days", "minute": "minutes", "week": "weeks", "month": "months",
    "year": "years", "time": "times", "times": "times", "attempt": "attempts",
}  # fmt: skip


def find_policy_numbers(text: str) -> list[Found]:
    """Figures such as '24 hours', 'twelve months' or '2 times' as (number, unit)."""
    found: list[Found] = []
    for match in _UNIT.finditer(text):
        value = _number(match.group(1))
        if value is None:
            continue
        unit = match.group(2).lower()
        unit = _UNIT_NAMES.get(unit, _UNIT_NAMES.get(unit.rstrip("s"), unit))
        found.append(Found(match.group(0), (value, unit)))
    return found
