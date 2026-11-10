"""The copied universe is unchanged, and Citebook's extension never contradicts facts.json."""

import hashlib
import itertools
import json
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tools.build_data import UNIVERSE, load_extension, load_facts

FACTS = load_facts()
EXT = load_extension()
SERVICE_TYPES = {s["service_type"] for s in FACTS["services_offered"]}
REFERENCE = re.compile(r"^CB-[2-9A-HJKMNP-Z]{6}$")


def by_code() -> dict[str, dict[str, Any]]:
    return {s["code"]: s for s in EXT["services"]}


def test_universe_files_match_the_citedesk_manifest() -> None:
    manifest = json.loads((UNIVERSE / "manifest.json").read_text(encoding="utf-8"))
    facts_hash = hashlib.sha256((UNIVERSE / "facts.json").read_bytes()).hexdigest()
    assert facts_hash == manifest["facts_sha256"]
    for name, expected in manifest["docs"].items():
        assert hashlib.sha256((UNIVERSE / "docs" / name).read_bytes()).hexdigest() == expected


def test_extension_is_marked_synthetic_and_names_its_source() -> None:
    assert EXT["synthetic"] is True and FACTS["synthetic"] is True
    assert EXT["source"] == {"project": "citedesk", "commit": "d64b8a0", "path": "universe/"}


def test_business_time_zone_is_valid() -> None:
    ZoneInfo(EXT["business_tz"])


def test_every_neighbourhood_has_a_zip_code_in_the_right_zone() -> None:
    area = FACTS["service_area"]
    expected = {name: "core" for name in area["core"]}
    expected |= {name: "outer" for name in area["outer"]["areas"]}
    zips = {k: v for k, v in EXT["zip_areas"].items() if not k.startswith("_")}
    assert {v["area"] for v in zips.values()} == set(expected)
    for code, info in zips.items():
        assert re.fullmatch(r"000\d\d", code), (
            "invented ZIP codes stay in the unassigned 000xx range"
        )
        assert info["zone"] == expected[info["area"]]


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


@pytest.mark.parametrize("day", ["monday_to_friday", "saturday"])
def test_visit_windows_fit_the_published_visit_hours(day: str) -> None:
    hours = FACTS["hours"]["scheduled_visits"]
    length = hours["arrival_windows_hours"] * 60
    starts = [_minutes(t) for t in EXT["visit_windows"][day]]
    assert starts[0] == _minutes(hours[day]["first_arrival"])
    assert starts[-1] + length == _minutes(hours[day]["last_arrival"])
    assert all(b - a >= length for a, b in itertools.pairwise(starts)), "no overlaps"


def test_priced_services_match_the_price_list() -> None:
    jobs = {j["job"]: j for j in FACTS["pricing"]["flat_price_jobs"]}
    for service in EXT["services"]:
        if "facts_job" not in service:
            continue
        job = jobs[service["facts_job"]]
        assert service["skill"] == job["service_type"], service["code"]
        starting = job.get("price_note") == "starting price"
        assert (service["pricing_kind"] == "starting_price") == starting, service["code"]
        assert service["pricing_kind"] in {"fixed", "starting_price"}


def test_every_published_job_has_a_service() -> None:
    priced = {s.get("facts_job") for s in EXT["services"]}
    quoted = {s.get("facts_quote_only") for s in EXT["services"]}
    assert {j["job"] for j in FACTS["pricing"]["flat_price_jobs"]} <= priced
    assert set(FACTS["pricing"]["quote_only"]) <= quoted


def test_quote_only_services_point_to_a_bookable_first_step() -> None:
    services = by_code()
    for service in EXT["services"]:
        assert service["skill"] in SERVICE_TYPES, service["code"]
        if service["pricing_kind"] == "quote_only":
            step = service.get("first_step_code")
            if step is not None:
                assert services[step]["pricing_kind"] in {"fixed", "starting_price"}
        else:
            assert "first_step_code" not in service


def test_technicians_are_the_published_technicians() -> None:
    staff = {s["name"]: s["role"] for s in FACTS["staff"]}
    for tech in EXT["technicians"]:
        assert "technician" in staff[tech["name"]].lower()
        assert set(tech["skills"]) <= SERVICE_TYPES
    assert {s["skill"] for s in EXT["services"]} <= {
        k for t in EXT["technicians"] for k in t["skills"]
    }


def test_customers_use_invented_contact_details() -> None:
    for customer in EXT["customers"]:
        assert re.fullmatch(r"555-01([0-5]\d|6\d)", customer["phone"]), customer["key"]
        assert customer["phone"] not in {
            FACTS["business"]["phone"],
            FACTS["business"]["emergency_phone"],
        }
        assert customer["email"].endswith("@example.com")
        assert customer["zip_code"] in EXT["zip_areas"]
        assert EXT["zip_areas"][customer["zip_code"]]["area"] in customer["address"]


def _closed() -> set[date]:
    return {date.fromisoformat(d["date"]) for d in FACTS["hours"]["closed_dates"]}


def test_bookings_fit_the_calendar_and_the_rules() -> None:
    states = EXT["seed_states"]
    start = date.fromisoformat(states["calendar_start"])
    end = start + timedelta(weeks=states["calendar_weeks"])
    tz = ZoneInfo(EXT["business_tz"])
    latest_now = max(datetime.fromisoformat(s["now"]) for s in states["states"].values())
    customers = {c["key"] for c in EXT["customers"]}
    techs = {t["key"]: t for t in EXT["technicians"]}
    services = by_code()
    references = [b["reference"] for b in EXT["bookings"]]
    assert len(set(references)) == len(references)
    for booking in EXT["bookings"]:
        assert REFERENCE.match(booking["reference"]), booking["reference"]
        assert booking["customer"] in customers
        assert booking["status"] == "confirmed"
        service = services[booking["service"]]
        assert service["skill"] in techs[booking["technician"]]["skills"]
        day = date.fromisoformat(booking["date"])
        assert start <= day < end and day not in _closed() and day.weekday() != 6
        windows = EXT["visit_windows"]["saturday" if day.weekday() == 5 else "monday_to_friday"]
        assert booking["window"] in windows
        if service["windows_needed"] == 2:
            assert windows.index(booking["window"]) + 1 < len(windows)
        starts = datetime.combine(day, datetime.strptime(booking["window"], "%H:%M").time(), tz)
        assert starts > latest_now.replace(tzinfo=tz), (
            "seeded bookings are in the future in every state"
        )
        assert 0 <= booking["reschedule_count"] <= EXT["citebook_policies"]["max_reschedules"]
