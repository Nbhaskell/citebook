import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from citebook.pricing import NotQuotable, OutOfServiceArea, build_quote
from citebook.timeutil import BusinessClock

SEED = Path(__file__).resolve().parent.parent / "db" / "seed"


def load(name: str) -> Any:
    return json.loads((SEED / name).read_text(encoding="utf-8"))


BOOK = load("price_book.json")
SETTINGS = load("settings.json")
SERVICES = {s["code"]: s for s in load("services.json")}
CLOCK = BusinessClock.from_settings(SETTINGS)
ZIPS = SETTINGS["zip_areas"]
TZ = ZoneInfo("America/Los_Angeles")
CORE, OUTER = "00012", "00022"  # Millrace, Saltmarsh
IN_HOURS = datetime(2026, 11, 12, 10, 0, tzinfo=TZ)  # a Thursday visit window
SUNDAY = datetime(2026, 11, 15, 10, 0, tzinfo=TZ)


def quote(code: str, zip_code: str = CORE, slot: datetime | None = IN_HOURS) -> Any:
    return build_quote(SERVICES[code], zip_code, slot, BOOK, ZIPS, CLOCK)


def labels(q: Any) -> list[str]:
    return [label for label, _ in q.lines]


def test_fixed_price_in_the_core_area() -> None:
    q = quote("FAUCET_REPLACEMENT")
    assert q.lines == [
        ("Call-out fee", 8900),
        ("Faucet replacement", 16500),
        ("Call-out fee credited when you approve the work", -8900),
    ]
    assert q.total_minor == 16500
    assert q.is_estimate is False
    assert q.bookable_online is True
    assert q.price_book_version == BOOK["version"]


def test_starting_price_is_an_estimate() -> None:
    q = quote("DRAIN_CLEANING")
    assert q.total_minor == 14900
    assert q.is_estimate is True
    assert "final price confirmed on site" in q.estimate_note.lower()
    assert "Drain cleaning, one fixture (starting price)" in labels(q)


def test_hourly_service_quotes_the_minimum_labour_and_says_parts_are_extra() -> None:
    q = quote("GENERAL_REPAIR")
    assert ("Labour, minimum 1 hour", 11500) in q.lines
    assert q.total_minor == 11500
    assert q.is_estimate is True
    assert "parts" in q.estimate_note.lower()


def test_outer_area_adds_the_travel_fee() -> None:
    q = quote("FAUCET_REPLACEMENT", OUTER)
    assert ("Travel fee (Saltmarsh)", 4000) in q.lines
    assert q.total_minor == 16500 + 4000


def test_unknown_zip_code_is_out_of_service_area() -> None:
    with pytest.raises(OutOfServiceArea):
        quote("FAUCET_REPLACEMENT", "90210")


@pytest.mark.parametrize(
    ("code", "step"),
    [
        ("SEWER_LINE_REPAIR", "CAMERA_INSPECTION"),
        ("SLAB_LEAK_REPAIR", "LEAK_DETECTION"),
        ("REPIPING", None),
    ],
)
def test_quote_only_services_name_their_first_step(code: str, step: str | None) -> None:
    with pytest.raises(NotQuotable) as raised:
        quote(code)
    assert raised.value.first_step_code == step


def test_jobs_over_the_estimate_limit_are_quoted_but_not_bookable_online() -> None:
    q = quote("WATER_HEATER_40_GAL")
    assert q.total_minor == 145000
    assert q.bookable_online is False
    assert any("written estimate" in note for note in q.notes)


def test_outside_visit_hours_the_emergency_fee_replaces_the_call_out_and_is_not_credited() -> None:
    q = quote("FAUCET_REPLACEMENT", slot=SUNDAY)
    assert q.lines == [("Emergency call-out fee", 17500), ("Faucet replacement", 16500)]
    assert q.total_minor == 34000


def test_a_quote_without_a_slot_uses_the_standard_call_out() -> None:
    q = quote("FAUCET_REPLACEMENT", slot=None)
    assert ("Call-out fee", 8900) in q.lines


def test_the_decline_note_names_the_call_out_fee() -> None:
    assert any("$89.00" in note and "decline" in note for note in quote("TOILET_REPLACEMENT").notes)


KINDS = ["FAUCET_REPLACEMENT", "DRAIN_CLEANING", "GENERAL_REPAIR"]


@pytest.mark.parametrize("code", KINDS)
@pytest.mark.parametrize("zip_code", [CORE, OUTER])
@pytest.mark.parametrize("slot", [IN_HOURS, SUNDAY, None], ids=["in-hours", "sunday", "no-slot"])
def test_every_combination_adds_up_in_integer_cents(
    code: str, zip_code: str, slot: datetime | None
) -> None:
    q = quote(code, zip_code, slot)
    assert all(isinstance(amount, int) for _, amount in q.lines)
    assert q.total_minor == sum(amount for _, amount in q.lines)
    travel = 4000 if zip_code == OUTER else 0
    job = {"FAUCET_REPLACEMENT": 16500, "DRAIN_CLEANING": 14900, "GENERAL_REPAIR": 11500}[code]
    call_out = 17500 if slot == SUNDAY else 0  # the standard fee is credited back
    assert q.total_minor == job + travel + call_out
