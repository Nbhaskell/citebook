"""Business time: storage in UTC, display and rules in the business time zone.

All datetimes here are timezone-aware. Wall-clock rules (office hours, visit windows) work in the
business zone; elapsed-time rules (24 hours' notice) work in UTC, because Python subtracts two
times in the same zone as wall-clock times and would be an hour out on a daylight-saving weekend.
The model never does date maths: phrases such as "next Friday" are resolved here (D-044).
"""

from __future__ import annotations

import re
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
_PHRASE = re.compile(
    r"(?<![\w-])("
    + "|".join(re.escape(p) for p in _FIXED)
    + rf"|(?:next |this )?(?:{_DAYS})"
    + r"|\d{4}-\d\d-\d\d"
    + r")(?![\w-])",
    re.IGNORECASE,
)


def resolve_phrase(phrase: str, now: datetime) -> DateRange | None:
    """One date phrase, in the business-local `now`, to a date range; None if unclear."""
    key = " ".join(phrase.lower().split())
    if re.fullmatch(r"\d{4}-\d\d-\d\d", key):
        try:
            day = date.fromisoformat(key)
        except ValueError:
            return None
        return DateRange(phrase, day, day)
    resolved = _resolve(key, now.date()) if key else None
    return DateRange(phrase, *resolved) if resolved else None


def find_date_phrases(text: str, now: datetime) -> list[DateRange]:
    """Every date phrase in a customer message that code can resolve, in order."""
    found = []
    for match in _PHRASE.finditer(text):
        resolved = resolve_phrase(match.group(1), now)
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
