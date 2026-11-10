"""Build the deterministic four-week calendar seed and print a week's open windows.

Run from the repository root:
  python -m tools.seed_calendar --state base_week_1          open windows per service, that week
  python -m tools.seed_calendar --state base_week_2 --seed 7 same, with another blocked-slot seed

A slot is one 2-hour arrival window for one technician (docs/DECISIONS.md D-004). Windows come
from settings.visit_hours, skip Sundays and closed dates, and are stored in UTC. The seeded
bookings in universe/citebook_extension.json mark their slots `booked`; a seeded random share of
the other slots is `blocked` (technician busy elsewhere). The same seed always gives the same
calendar. Nothing is written to disk; Phase 2 loads the result into the database.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import random
import sys
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from tools.build_data import (
    SEED_DIR,
    load_extension,
    stable_uuid,
)

Calendar = dict[str, list[dict[str, Any]]]


def _seed_json(name: str) -> Any:
    return json.loads((SEED_DIR / name).read_text(encoding="utf-8"))


SETTINGS: dict[str, Any] = _seed_json("settings.json")
BUSINESS_TZ = ZoneInfo(SETTINGS["business_tz"])
UTC = ZoneInfo("UTC")


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def state_now(state: str) -> datetime:
    """The clock a seed state starts at, as an aware datetime."""
    states = load_extension()["seed_states"]["states"]
    return datetime.fromisoformat(states[state]["now"]).replace(tzinfo=BUSINESS_TZ)


def _day_windows(day: date) -> list[str]:
    if day.weekday() == 6 or day.isoformat() in SETTINGS["closed_dates"]:
        return []
    key = "saturday" if day.weekday() == 5 else "monday_to_friday"
    windows: list[str] = SETTINGS["visit_hours"][key]
    return windows


def generate_calendar(seed: int | None = None) -> Calendar:
    ext = load_extension()
    states = ext["seed_states"]
    seed = states["calendar_seed"] if seed is None else seed
    share = Decimal(states["blocked_share"])
    start = date.fromisoformat(states["calendar_start"])
    length = timedelta(minutes=SETTINGS["arrival_window_minutes"])
    technicians = _seed_json("technicians.json")

    slots: list[dict[str, Any]] = []
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for offset in range(states["calendar_weeks"] * 7):
        day = start + timedelta(days=offset)
        for window in _day_windows(day):
            begins = datetime.combine(day, time.fromisoformat(window), BUSINESS_TZ)
            for tech in technicians:
                starts_at = _utc_text(begins)
                slot = {
                    "id": stable_uuid("slot", f"{tech['key']}:{starts_at}"),
                    "technician_id": tech["id"],
                    "technician_key": tech["key"],
                    "starts_at": starts_at,
                    "ends_at": _utc_text(begins + length),
                    "status": "open",
                    "version": 0,
                }
                slots.append(slot)
                index[(tech["key"], day.isoformat(), window)] = slot
    slots.sort(key=lambda s: (s["starts_at"], s["technician_key"]))

    rng = random.Random(seed)
    for slot in slots:  # one draw per slot, in a fixed order, so bookings never shift the draws
        if Decimal(str(rng.random())) < share:
            slot["status"] = "blocked"

    services = {s["code"]: s for s in _seed_json("services.json")}
    customers = {c["key"]: c for c in _seed_json("customers.json")}
    bookings: list[dict[str, Any]] = []
    for b in ext["bookings"]:
        windows = _day_windows(date.fromisoformat(b["date"]))
        first = windows.index(b["window"])
        needed = services[b["service"]]["windows_needed"]
        held = [index[(b["technician"], b["date"], w)] for w in windows[first : first + needed]]
        if len(held) != needed:
            raise ValueError(f"{b['reference']} does not fit the day's windows")
        for slot in held:
            slot["status"] = "booked"
        bookings.append(
            {
                "id": stable_uuid("booking", b["reference"]),
                "reference": b["reference"],
                "customer_id": customers[b["customer"]]["id"],
                "service_code": b["service"],
                "slot_id": held[0]["id"],
                "slot_ids": [s["id"] for s in held],
                "status": b["status"],
                "reschedule_count": b["reschedule_count"],
                "customer_notes": b.get("customer_notes"),
            }
        )
    return {"slots": slots, "bookings": bookings}


def calendar_digest(calendar: Calendar) -> str:
    text = json.dumps(calendar, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def open_windows(
    calendar: Calendar, service_code: str, now: datetime, days: int
) -> list[dict[str, Any]]:
    """Open windows for a bookable service from `now` for `days` days, grouped by start time."""
    services = {s["code"]: s for s in _seed_json("services.json")}
    service = services[service_code]
    if not service["bookable_online"]:
        raise ValueError(f"{service_code} is not bookable online")
    techs = {t["key"]: t for t in _seed_json("technicians.json")}
    able = {k for k, t in techs.items() if service["skill"] in t["skills"]}
    until = now + timedelta(days=days)
    by_tech: dict[str, list[dict[str, Any]]] = {}
    for slot in calendar["slots"]:
        by_tech.setdefault(slot["technician_key"], []).append(slot)

    grouped: dict[str, dict[str, Any]] = {}
    needed = service["windows_needed"]
    for key in sorted(able):
        row = by_tech.get(key, [])
        for i, slot in enumerate(row):
            begins = _parse_utc(slot["starts_at"])
            if not now < begins < until:
                continue
            run = row[i : i + needed]
            same_day = {_parse_utc(s["starts_at"]).astimezone(BUSINESS_TZ).date() for s in run}
            touching = all(a["ends_at"] == b["starts_at"] for a, b in itertools.pairwise(run))
            if len(run) < needed or len(same_day) != 1 or not touching:
                continue
            if any(s["status"] != "open" for s in run):
                continue
            entry = grouped.setdefault(
                slot["starts_at"],
                {"starts_at": slot["starts_at"], "ends_at": run[-1]["ends_at"], "technicians": []},
            )
            entry["technicians"].append(techs[key]["name"])
    return [grouped[k] for k in sorted(grouped)]


def _label(window: dict[str, Any]) -> str:
    begins = _parse_utc(window["starts_at"]).astimezone(BUSINESS_TZ)
    ends = _parse_utc(window["ends_at"]).astimezone(BUSINESS_TZ)
    names = ", ".join(n.split()[-1] for n in window["technicians"])
    return f"{begins:%a} {begins.day} {begins:%b} {begins:%H:%M}-{ends:%H:%M} ({names})"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print a week's open windows per service.")
    parser.add_argument("--state", default="base_week_1", help="seed state, e.g. base_week_2")
    parser.add_argument("--seed", type=int, default=None, help="blocked-slot seed")
    args = parser.parse_args(argv)

    calendar = generate_calendar(args.seed)
    now = state_now(args.state)
    print(f"Citebook calendar seed, state {args.state} (now {now:%a} {now.day} {now:%b %H:%M})")
    print(f"digest {calendar_digest(calendar)}")
    for service in _seed_json("services.json"):
        if not service["bookable_online"]:
            continue
        windows = open_windows(calendar, service["code"], now, days=7)
        print(f"\n{service['code']} ({len(windows)} open)")
        for window in windows:
            print(f"  {_label(window)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
