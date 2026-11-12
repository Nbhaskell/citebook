import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from citebook.timeutil import (
    EN_DASH,
    BusinessClock,
    find_date_phrases,
    format_window,
    is_late,
    parse_utc,
    resolve_phrase,
    to_utc_text,
)

SETTINGS = json.loads(
    (Path(__file__).resolve().parent.parent / "db" / "seed" / "settings.json").read_text("utf-8")
)
CLOCK = BusinessClock.from_settings(SETTINGS)
TZ = ZoneInfo("America/Los_Angeles")


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=TZ)


# --- UTC storage -------------------------------------------------------------------------


def test_utc_text_round_trip() -> None:
    moment = at("2026-11-12T13:00")
    assert to_utc_text(moment) == "2026-11-12T21:00:00Z"
    assert parse_utc("2026-11-12T21:00:00Z") == moment


def test_naive_datetimes_are_refused() -> None:
    with pytest.raises(ValueError):
        to_utc_text(datetime(2026, 11, 12, 13, 0))
    with pytest.raises(ValueError):
        parse_utc("2026-11-12T21:00:00")


# --- visit windows, including daylight saving ---------------------------------------------


def test_weekday_and_saturday_windows() -> None:
    weekday = CLOCK.visit_windows(date(2026, 11, 12))
    assert [(s.strftime("%H:%M"), e.strftime("%H:%M")) for s, e in weekday] == [
        ("08:00", "10:00"),
        ("10:00", "12:00"),
        ("12:00", "14:00"),
        ("14:00", "16:00"),
    ]
    saturday = CLOCK.visit_windows(date(2026, 11, 14))
    assert [s.strftime("%H:%M") for s, _ in saturday] == ["08:00", "11:00"]


def test_no_windows_on_sundays_or_closed_dates() -> None:
    assert CLOCK.visit_windows(date(2026, 11, 15)) == []
    assert CLOCK.visit_windows(date(2026, 11, 26)) == []  # Thanksgiving
    assert CLOCK.visit_windows(date(2026, 12, 25)) == []


def test_windows_keep_local_time_across_daylight_saving_changes() -> None:
    # Clocks go forward on Sunday 14 March 2027 and back on Sunday 7 November 2027.
    before = CLOCK.visit_windows(date(2027, 3, 12))[0][0]
    after = CLOCK.visit_windows(date(2027, 3, 15))[0][0]
    assert to_utc_text(before) == "2027-03-12T16:00:00Z"  # PST, UTC-8
    assert to_utc_text(after) == "2027-03-15T15:00:00Z"  # PDT, UTC-7
    autumn = CLOCK.visit_windows(date(2027, 11, 8))[0][0]
    assert to_utc_text(autumn) == "2027-11-08T16:00:00Z"
    for start, end in CLOCK.visit_windows(date(2027, 3, 15)):
        assert end - start == timedelta(hours=2)


# --- office-hours arithmetic (hold expiry, D-012) -------------------------------------------


@pytest.mark.parametrize(
    ("start", "hours", "expected"),
    [
        ("2026-11-12T09:00", 4, "2026-11-12T13:00"),  # inside one day
        ("2026-11-12T15:00", 4, "2026-11-13T09:30"),  # 2 h today, 2 h from 7:30 tomorrow
        ("2026-11-13T21:00", 4, "2026-11-14T12:00"),  # Friday night: Saturday 8:00 to noon
        ("2026-11-14T13:00", 4, "2026-11-16T11:30"),  # Saturday after close: Monday
        ("2026-11-15T10:00", 4, "2026-11-16T11:30"),  # Sunday: Monday
        ("2026-11-25T16:00", 4, "2026-11-27T10:30"),  # Thanksgiving is skipped
        ("2026-11-12T06:00", 1, "2026-11-12T08:30"),  # before opening: counts from 7:30
        ("2026-11-12T17:00", 1, "2026-11-13T08:30"),  # exactly at closing
        ("2027-03-13T11:00", 4, "2027-03-15T10:30"),  # across the spring DST change
    ],
)
def test_add_office_hours(start: str, hours: int, expected: str) -> None:
    assert CLOCK.add_office_hours(at(start), hours) == at(expected)


def test_add_office_hours_refuses_negative_hours() -> None:
    with pytest.raises(ValueError):
        CLOCK.add_office_hours(at("2026-11-12T09:00"), -1)


# --- late changes ------------------------------------------------------------------------------


def test_late_means_less_than_free_cancel_hours_in_real_time() -> None:
    visit = at("2026-11-17T10:00")
    assert not is_late(at("2026-11-16T10:00"), visit, 24)  # exactly 24 h: free
    assert is_late(at("2026-11-16T10:01"), visit, 24)
    assert is_late(at("2026-11-17T11:00"), visit, 24)  # already past


def test_late_check_counts_real_hours_across_dst() -> None:
    # 23 real hours between Saturday 10:00 and Sunday 10:00 on the spring-forward weekend.
    assert is_late(at("2027-03-13T10:00"), at("2027-03-14T10:00"), 24)


# --- relative dates -------------------------------------------------------------------------

THURSDAY = at("2026-11-12T09:00")
SUNDAY = at("2026-11-15T09:00")


@pytest.mark.parametrize(
    ("phrase", "now", "first", "last"),
    [
        ("today", THURSDAY, "2026-11-12", "2026-11-12"),
        ("tomorrow", THURSDAY, "2026-11-13", "2026-11-13"),
        ("day after tomorrow", THURSDAY, "2026-11-14", "2026-11-14"),
        ("Friday", THURSDAY, "2026-11-13", "2026-11-13"),
        ("thursday", THURSDAY, "2026-11-19", "2026-11-19"),  # never today: today is "today"
        ("next Friday", THURSDAY, "2026-11-20", "2026-11-20"),  # Friday of next week
        ("next Monday", THURSDAY, "2026-11-16", "2026-11-16"),
        ("this Saturday", THURSDAY, "2026-11-14", "2026-11-14"),
        ("this week", THURSDAY, "2026-11-12", "2026-11-15"),
        ("next week", THURSDAY, "2026-11-16", "2026-11-22"),
        ("this weekend", THURSDAY, "2026-11-14", "2026-11-15"),
        ("weekend", SUNDAY, "2026-11-15", "2026-11-15"),
        ("next week", SUNDAY, "2026-11-16", "2026-11-22"),
        ("2026-11-30", THURSDAY, "2026-11-30", "2026-11-30"),
        ("Monday", at("2026-12-31T09:00"), "2027-01-04", "2027-01-04"),  # across a year end
    ],
)
def test_resolve_phrase(phrase: str, now: datetime, first: str, last: str) -> None:
    resolved = resolve_phrase(phrase, now)
    assert resolved is not None
    assert (resolved.first.isoformat(), resolved.last.isoformat()) == (first, last)


@pytest.mark.parametrize("phrase", ["this Monday", "someday", "soon", "the 31st", ""])
def test_unresolvable_or_ambiguous_phrases_give_none(phrase: str) -> None:
    # "this Monday" on a Thursday has passed: the customer must say which Monday.
    assert resolve_phrase(phrase, THURSDAY) is None


def test_find_date_phrases_in_a_message() -> None:
    found = find_date_phrases("Can you come Thursday afternoon, or next Friday?", THURSDAY)
    assert [(f.phrase, f.first.isoformat()) for f in found] == [
        ("Thursday", "2026-11-19"),
        ("next Friday", "2026-11-20"),
    ]


def test_a_weekday_takes_the_week_named_in_the_same_message() -> None:
    found = find_date_phrases("Next week, Tuesday morning if possible.", THURSDAY)
    assert [(f.phrase, f.first.isoformat(), f.last.isoformat()) for f in found] == [
        ("Next week", "2026-11-16", "2026-11-22"),
        ("Tuesday (next week)", "2026-11-17", "2026-11-17"),
    ]
    # The week may also come after the weekday.
    found = find_date_phrases("Friday, but next week please", THURSDAY)
    assert [(f.phrase, f.first.isoformat()) for f in found] == [
        ("Friday (next week)", "2026-11-20"),
        ("next week", "2026-11-16"),
    ]


def test_a_weekday_takes_the_week_from_an_earlier_message() -> None:
    # S-010: "sometime next week", then "Tuesday morning" in the next message.
    earlier = ["Hi, I need someone to look at a clogged tub drain sometime next week."]
    found = find_date_phrases("ZIP 00014. Tuesday morning if possible.", THURSDAY, earlier)
    assert [(f.phrase, f.first.isoformat()) for f in found] == [
        ("Tuesday (next week)", "2026-11-17"),
    ]


def test_only_the_latest_earlier_date_words_give_a_week() -> None:
    earlier = ["Anything next week?", "Actually, tomorrow would be better."]
    found = find_date_phrases("Or Tuesday.", THURSDAY, earlier)
    assert [(f.phrase, f.first.isoformat()) for f in found] == [("Tuesday", "2026-11-17")]
    # Here the plain answer is the same day; on a Monday the two readings differ.
    monday = at("2026-11-09T09:00")
    found = find_date_phrases("Or Tuesday.", monday, ["Anything next week?"])
    assert [(f.phrase, f.first.isoformat()) for f in found] == [
        ("Tuesday (next week)", "2026-11-17")
    ]
    found = find_date_phrases("Or Tuesday.", monday, ["Anything next week?", "Or tomorrow?"])
    assert [(f.phrase, f.first.isoformat()) for f in found] == [("Tuesday", "2026-11-10")]


def test_a_named_week_does_not_change_next_or_this_weekdays() -> None:
    found = find_date_phrases("This Saturday, or next Friday, any time next week", THURSDAY)
    assert [(f.phrase, f.first.isoformat()) for f in found] == [
        ("This Saturday", "2026-11-14"),
        ("next Friday", "2026-11-20"),
        ("next week", "2026-11-16"),
    ]


def test_a_passed_weekday_this_week_keeps_its_plain_meaning() -> None:
    found = find_date_phrases("This week, Monday or Friday", THURSDAY)
    assert [(f.phrase, f.first.isoformat()) for f in found] == [
        ("This week", "2026-11-12"),
        ("Monday", "2026-11-16"),
        ("Friday (this week)", "2026-11-13"),
    ]


def test_find_date_phrases_ignores_words_inside_words() -> None:
    assert find_date_phrases("My name is Monday-Smith and I like todays news", THURSDAY) == []


# --- display -------------------------------------------------------------------------------------


def test_format_window_names_the_day() -> None:
    start, end = at("2026-11-12T13:00"), at("2026-11-12T15:00")
    assert format_window(start, end) == f"Thursday 12 November, 1:00{EN_DASH}3:00 PM"
    morning = format_window(at("2026-11-14T11:00"), at("2026-11-14T13:00"))
    assert morning == f"Saturday 14 November, 11:00 AM{EN_DASH}1:00 PM"
