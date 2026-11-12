"""Business time: storage in UTC, display and rules in the business time zone.

All datetimes here are timezone-aware. Wall-clock rules (office hours, visit windows) work in the
business zone; elapsed-time rules (24 hours' notice) work in UTC, because Python subtracts two
times in the same zone as wall-clock times and would be an hour out on a daylight-saving weekend.
The model never does date maths: phrases such as "next Friday" are resolved here (D-044).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def to_utc_text(moment: datetime) -> str:
    """An aware datetime as stored: ISO 8601 UTC with a Z, to the second."""
    if moment.tzinfo is None:
        raise ValueError("naive datetime: attach the business time zone first")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(text: str) -> datetime:
    if not text.endswith("Z"):
        raise ValueError("stored times end with Z (UTC)")
    return datetime.fromisoformat(text[:-1] + "+00:00")


def is_late(now: datetime, visit_start: datetime, free_cancel_hours: int) -> bool:
    """True when the visit starts less than `free_cancel_hours` real hours from now (D-005)."""
    remaining = visit_start.astimezone(UTC) - now.astimezone(UTC)
    return remaining < timedelta(hours=free_cancel_hours)


def _hhmm(text: str) -> time:
    return time.fromisoformat(text)


@dataclass(frozen=True)
class BusinessClock:
    tz: ZoneInfo
    office_hours: dict[int, tuple[time, time]]  # weekday -> (open, close); missing = closed
    visit_starts: dict[int, list[time]]  # weekday -> window start times
    window: timedelta
    closed_dates: frozenset[date]

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> BusinessClock:
        office: dict[int, tuple[time, time]] = {}
        visits: dict[int, list[time]] = {}
        weekday_hours = settings["office_hours"]["monday_to_friday"]
        saturday_hours = settings["office_hours"]["saturday"]
        for day in range(5):
            office[day] = (_hhmm(weekday_hours["open"]), _hhmm(weekday_hours["close"]))
            visits[day] = [_hhmm(t) for t in settings["visit_hours"]["monday_to_friday"]]
        office[5] = (_hhmm(saturday_hours["open"]), _hhmm(saturday_hours["close"]))
        visits[5] = [_hhmm(t) for t in settings["visit_hours"]["saturday"]]
        return cls(
            tz=ZoneInfo(settings["business_tz"]),
            office_hours=office,
            visit_starts=visits,
            window=timedelta(minutes=settings["arrival_window_minutes"]),
            closed_dates=frozenset(date.fromisoformat(d) for d in settings["closed_dates"]),
        )

    def local(self, moment: datetime) -> datetime:
        return moment.astimezone(self.tz)

    def _open(self, day: date) -> bool:
        return day not in self.closed_dates

    def visit_windows(self, day: date) -> list[tuple[datetime, datetime]]:
        if not self._open(day):
            return []
        starts = [
            datetime.combine(day, t, self.tz) for t in self.visit_starts.get(day.weekday(), [])
        ]
        return [(s, s + self.window) for s in starts]

    def add_office_hours(self, start: datetime, hours: int) -> datetime:
        """The moment `hours` office hours after `start`, skipping closed days (D-012)."""
        if hours < 0:
            raise ValueError("hours must not be negative")
        remaining = timedelta(hours=hours)
        cursor = self.local(start)
        for _ in range(366):
            day = cursor.date()
            span = self.office_hours.get(day.weekday())
            if span is not None and self._open(day):
                opens = datetime.combine(day, span[0], self.tz)
                closes = datetime.combine(day, span[1], self.tz)
                if cursor < closes:
                    cursor = max(cursor, opens)
                    available = closes - cursor
                    if remaining <= available:
                        return cursor + remaining
                    remaining -= available
            cursor = datetime.combine(day + timedelta(days=1), time(0), self.tz)
        raise ValueError("no office hours within a year")


# --- relative date phrases --------------------------------------------------------------------


@dataclass(frozen=True)
class DateRange:
    phrase: str
    first: date
    last: date


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _resolve(key: str, today: date) -> tuple[date, date] | None:
    if key == "today":
        return today, today
    if key == "tomorrow":
        return today + timedelta(days=1), today + timedelta(days=1)
    if key == "day after tomorrow":
        return today + timedelta(days=2), today + timedelta(days=2)
    this_monday = _week_start(today)
    next_monday = this_monday + timedelta(days=7)
    if key == "this week":
        return today, this_monday + timedelta(days=6)
    if key == "next week":
        return next_monday, next_monday + timedelta(days=6)
    if key in {"weekend", "this weekend"}:
        first = max(today, this_monday + timedelta(days=5))
        return first, this_monday + timedelta(days=6)
    if key == "next weekend":
        return next_monday + timedelta(days=5), next_monday + timedelta(days=6)
    words = key.split()
    if words[-1] in WEEKDAYS and len(words) <= 2:
        target = WEEKDAYS.index(words[-1])
        if len(words) == 1:  # the next such day after today; today itself is "today"
            ahead = (target - today.weekday() - 1) % 7 + 1
            day = today + timedelta(days=ahead)
        elif words[0] == "next":  # that day in the next Monday-to-Sunday week
            day = next_monday + timedelta(days=target)
        elif words[0] == "this":  # that day in this week, if it has not passed
            day = this_monday + timedelta(days=target)
            if day < today:
                return None
        else:
            return None
        return day, day
    return None


_FIXED = [
    "day after tomorrow",
    "today",
    "tomorrow",
    "this week",
    "next week",
    "this weekend",
    "next weekend",
    "weekend",
]
_DAYS = "|".join(WEEKDAYS)
# An explicit date: "Friday 27 November", "Mon 16 Nov" (a time chip), "November 27th",
# "the 27th of November", "Thu, Nov 19", with an optional year. "May" counts only with a
# capital, so "I may 2 or 3 things" is not a date.
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_DAY_NAME = (
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|mon|tues?|wed|thu(?:rs?)?|fri|sat|sun)"
)
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|(?-i:May)|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:\.(?=\s+\d))?"
)
_ORDINAL = r"(?:st|nd|rd|th)?"
_EXPLICIT = (
    rf"(?:(?P<wd>{_DAY_NAME})\.?,?\s+)?"
    rf"(?:(?P<d1>\d{{1,2}}){_ORDINAL}(?:\s+of)?\s+(?P<m1>{_MONTH})"
    rf"|(?P<m2>{_MONTH})\s+(?P<d2>\d{{1,2}}){_ORDINAL})"
    r"(?:,?\s+(?P<y>\d{4}))?"
)
_EXPLICIT_RE = re.compile(_EXPLICIT, re.IGNORECASE)
_PHRASE = re.compile(
    r"(?<![\w-])("
    + re.sub(r"\?P<\w+>", "?:", _EXPLICIT)  # the same pattern without its group names
    + "|"
    + "|".join(re.escape(p) for p in _FIXED)
    + rf"|(?:next |this )?(?:{_DAYS})"
    + r"|\d{4}-\d\d-\d\d"
    + r")(?![\w-])",
    re.IGNORECASE,
)


def _explicit_date(match: re.Match[str], today: date) -> date | None:
    """The date an explicit phrase names; without a year, the next such date from today.

    None for a date that does not exist, or when the day name does not match the date (the
    customer must then say which day they mean)."""
    month = _MONTHS.index((match["m1"] or match["m2"])[:3].lower()) + 1
    number = int(match["d1"] or match["d2"])
    try:
        if match["y"]:
            day = date(int(match["y"]), month, number)
        else:
            day = date(today.year, month, number)
            if day < today:
                day = date(today.year + 1, month, number)
    except ValueError:
        return None
    if match["wd"] and WEEKDAYS[day.weekday()][:3] != match["wd"][:3].lower():
        return None
    return day


def resolve_phrase(phrase: str, now: datetime) -> DateRange | None:
    """One date phrase, in the business-local `now`, to a date range; None if unclear."""
    explicit = _EXPLICIT_RE.fullmatch(" ".join(phrase.split()))
    if explicit:
        day = _explicit_date(explicit, now.date())
        return DateRange(phrase, day, day) if day else None
    key = " ".join(phrase.lower().split())
    if re.fullmatch(r"\d{4}-\d\d-\d\d", key):
        try:
            day = date.fromisoformat(key)
        except ValueError:
            return None
        return DateRange(phrase, day, day)
    resolved = _resolve(key, now.date()) if key else None
    return DateRange(phrase, *resolved) if resolved else None


_WEEKS = ("this week", "next week")


def _named_week(phrases: list[str]) -> str | None:
    """The last 'this week' or 'next week' among phrases, lower case; None if there is none."""
    named = [key for key in (" ".join(p.lower().split()) for p in phrases) if key in _WEEKS]
    return named[-1] if named else None


def find_date_phrases(text: str, now: datetime, earlier: Sequence[str] = ()) -> list[DateRange]:
    """Every date phrase in a customer message that code can resolve, in order.

    A bare weekday takes the week the customer named: "next week ... Tuesday" is Tuesday of
    next week, not the coming Tuesday. The week comes from the same message, or else from the
    most recent earlier customer message (`earlier`, oldest first) that has date words, when
    those words name a week. A weekday that the named week cannot give (a passed day this week)
    keeps its plain meaning."""
    phrases = [match.group(1) for match in _PHRASE.finditer(text)]
    week = _named_week(phrases)
    if week is None:
        for message in reversed(earlier):
            before = [match.group(1) for match in _PHRASE.finditer(message)]
            if before:
                week = _named_week(before[-1:])
                break
    found = []
    for phrase in phrases:
        key = phrase.lower()
        resolved = None
        if week is not None and key in WEEKDAYS:
            in_week = _resolve(f"{week.split()[0]} {key}", now.date())
            if in_week is not None:
                resolved = DateRange(f"{phrase} ({week})", *in_week)
        if resolved is None:
            resolved = resolve_phrase(phrase, now)
        if resolved is not None:
            found.append(resolved)
    return found


# --- display ----------------------------------------------------------------------------------


EN_DASH = chr(0x2013)  # between the two times of a window, as in the UI copy


def _clock(moment: datetime) -> tuple[str, str]:
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d}", "AM" if moment.hour < 12 else "PM"


def format_window(start: datetime, end: datetime) -> str:
    """'Thursday 12 November, 1:00-3:00 PM' (with an en dash): day name and date in words."""
    begin, (finish, finish_half) = _clock(start), _clock(end)
    if begin[1] == finish_half:
        times = f"{begin[0]}{EN_DASH}{finish} {finish_half}"
    else:
        times = f"{begin[0]} {begin[1]}{EN_DASH}{finish} {finish_half}"
    return f"{start:%A} {start.day} {start:%B}, {times}"


def format_arrival(start: datetime, end: datetime) -> str:
    """'Thursday 12 November, arrival 1:00-3:00 PM' (with an en dash), as on cards and drafts."""
    day, times = format_window(start, end).split(", ", 1)
    return f"{day}, arrival {times}"


def format_slot_short(start: datetime, end: datetime) -> str:
    """'Thu 12 Nov, arrival 1:00-3:00 PM' (with an en dash), as in availability results."""
    times = format_window(start, end).split(", ", 1)[1]
    return f"{start:%a} {start.day} {start:%b}, arrival {times}"


def format_clock(moment: datetime) -> str:
    """'5:40 PM'."""
    digits, half = _clock(moment)
    return f"{digits} {half}"
