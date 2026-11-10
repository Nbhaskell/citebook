"""The calendar seed is deterministic and follows the published hours and the seeded bookings."""

from collections import Counter
from datetime import date, datetime, timedelta

import pytest

from tools.seed_calendar import (
    BUSINESS_TZ,
    calendar_digest,
    generate_calendar,
    main,
    open_windows,
    state_now,
)


@pytest.fixture(scope="module")
def cal() -> dict[str, list[dict[str, object]]]:
    return generate_calendar()


def local(slot: dict[str, object]) -> datetime:
    return datetime.fromisoformat(str(slot["starts_at"]).replace("Z", "+00:00")).astimezone(
        BUSINESS_TZ
    )


def test_same_seed_gives_the_same_calendar() -> None:
    assert generate_calendar() == generate_calendar()
    assert calendar_digest(generate_calendar()) == calendar_digest(generate_calendar())


def test_digest_is_pinned() -> None:
    """Catches an accidental change, or a platform that builds a different calendar (CI runs
    on Linux). After a deliberate change to the seed, update this value."""
    assert calendar_digest(generate_calendar()) == (
        "0aa3ff1716da7106497755684ada9ef896e858754f1b3f6fadfc9de87f7fe16a"
    )


def test_a_different_seed_changes_only_the_blocked_slots() -> None:
    a, b = generate_calendar(), generate_calendar(seed=1)
    assert [s["id"] for s in a["slots"]] == [s["id"] for s in b["slots"]]
    assert [s["status"] for s in a["slots"]] != [s["status"] for s in b["slots"]]
    assert a["bookings"] == b["bookings"]


def test_slots_follow_the_published_visit_hours(cal: dict[str, list[dict[str, object]]]) -> None:
    starts: Counter[object] = Counter()
    for slot in cal["slots"]:
        when = local(slot)
        assert when.weekday() != 6, "no Sunday visits"
        assert when.date() != date(2026, 11, 26), "closed on Thanksgiving"
        expected = (
            ["08:00", "11:00"] if when.weekday() == 5 else ["08:00", "10:00", "12:00", "14:00"]
        )
        assert when.strftime("%H:%M") in expected
        end = datetime.fromisoformat(str(slot["ends_at"]).replace("Z", "+00:00"))
        assert end - when == timedelta(minutes=120)
        assert str(slot["starts_at"]).endswith("Z")
        starts[slot["technician_key"]] += 1
    # 19 weekdays x 4 windows + 4 Saturdays x 2 windows, for each of the two technicians.
    assert starts == {"finch": 84, "marrow": 84}


def test_times_are_stored_in_utc(cal: dict[str, list[dict[str, object]]]) -> None:
    first = cal["slots"][0]
    assert first["starts_at"] == "2026-11-09T16:00:00Z"  # 8:00 am Pacific Standard Time


def test_seeded_bookings_hold_their_slots(cal: dict[str, list[dict[str, object]]]) -> None:
    slots = {s["id"]: s for s in cal["slots"]}
    booked = {s["id"] for s in cal["slots"] if s["status"] == "booked"}
    used: set[object] = set()
    for booking in cal["bookings"]:
        ids = booking["slot_ids"]
        assert isinstance(ids, list)
        assert booking["slot_id"] == ids[0]
        used.update(ids)
        assert all(slots[i]["technician_key"] == slots[ids[0]]["technician_key"] for i in ids)
    assert booked == used
    home = next(b for b in cal["bookings"] if b["reference"] == "CB-8YTF5G")
    home_ids = home["slot_ids"]
    assert isinstance(home_ids, list)
    assert [local(slots[i]).strftime("%H:%M") for i in home_ids] == ["08:00", "10:00"]


def test_about_a_fifth_of_free_slots_are_blocked(cal: dict[str, list[dict[str, object]]]) -> None:
    statuses = Counter(s["status"] for s in cal["slots"])
    assert set(statuses) == {"open", "blocked", "booked"}
    share = statuses["blocked"] / len(cal["slots"])
    assert 0.1 < share < 0.3


def test_open_windows_respect_skill(cal: dict[str, list[dict[str, object]]]) -> None:
    now = state_now("base_week_1")
    windows = open_windows(cal, "CAMERA_INSPECTION", now, days=7)
    assert windows
    assert all(w["technicians"] == ["Obadiah Finch"] for w in windows)


def test_two_window_services_need_two_open_windows_in_a_row(
    cal: dict[str, list[dict[str, object]]],
) -> None:
    now = state_now("base_week_1")
    for w in open_windows(cal, "HOME_INSPECTION", now, days=28):
        start = datetime.fromisoformat(str(w["starts_at"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(w["ends_at"]).replace("Z", "+00:00"))
        assert end - start == timedelta(hours=4)
        assert start.astimezone(BUSINESS_TZ).strftime("%H:%M") in {"08:00", "10:00", "12:00"}


def test_open_windows_start_after_now(cal: dict[str, list[dict[str, object]]]) -> None:
    now = state_now("base_week_2")
    windows = open_windows(cal, "DRAIN_CLEANING", now, days=7)
    assert windows
    assert all(
        datetime.fromisoformat(str(w["starts_at"]).replace("Z", "+00:00")) > now for w in windows
    )


def test_unknown_or_unbookable_service_is_refused(cal: dict[str, list[dict[str, object]]]) -> None:
    with pytest.raises(KeyError):
        open_windows(cal, "NO_SUCH_SERVICE", state_now("base_week_1"), days=7)
    with pytest.raises(ValueError):
        open_windows(cal, "SEWER_LINE_REPAIR", state_now("base_week_1"), days=7)


def test_cli_prints_the_week_per_service(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--state", "base_week_1"]) == 0
    out = capsys.readouterr().out
    assert "base_week_1" in out and "Mon 9 Nov" in out
    assert "DRAIN_CLEANING" in out and "CAMERA_INSPECTION" in out
    assert "SEWER_LINE_REPAIR" not in out  # quote only, never offered online
    assert "digest" in out
