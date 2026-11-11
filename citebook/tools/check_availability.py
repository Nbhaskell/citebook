"""check_availability: open arrival windows for a service, date range and ZIP code (TRD section 6).

Only technicians whose skills include the service's skill are searched. A 2-window service needs
two touching open windows of the same technician. Windows that start at the same time are shown
once, because the customer chooses a time, not a technician (D-056).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from typing import Literal

from pydantic import Field, model_validator

from citebook.models import ToolKind, ToolName
from citebook.timeutil import format_slot_short, parse_utc, to_utc_text
from citebook.tools.base import Block, ToolArgs, ToolContext, ToolOutput, ToolSpec, blocked, invalid
from citebook.tools.common import (
    SAY_OUT_OF_AREA,
    SAY_UNKNOWN_SERVICE,
    service,
    visit_slots,
    zip_problem,
)

MAX_RESULTS = 6
MAX_DAYS = 14
NOON = time(12, 0)


class Args(ToolArgs):
    service_code: str = Field(max_length=40)
    date_from: date = Field(description="First day, ISO date as given in the state summary")
    date_to: date = Field(description="Last day, ISO date; at most 14 days after date_from")
    part_of_day: Literal["morning", "afternoon", "any"] = "any"
    zip_code: str = Field(pattern=r"^\d{5}$", description="5-digit ZIP code of the service address")

    @model_validator(mode="after")
    def range_ok(self) -> Args:
        if self.date_to < self.date_from:
            raise ValueError("date_to is before date_from")
        if (self.date_to - self.date_from).days >= MAX_DAYS:
            raise ValueError(f"the range is longer than {MAX_DAYS} days")
        return self


def check_ids(ctx: ToolContext, args: Args) -> Block | None:
    row = service(ctx.conn, args.service_code)
    if row is None:
        return invalid(SAY_UNKNOWN_SERVICE, "unknown_service")
    # A service that cannot be booked online gets that answer whatever the ZIP code: asking the
    # customer for a ZIP code first only delays the answer (D-070).
    if row["bookable_online"] and (block := zip_problem(ctx, args.zip_code)):
        return block
    return None


def check_policy(ctx: ToolContext, args: Args) -> Block | None:
    row = service(ctx.conn, args.service_code)
    if row is not None and not row["bookable_online"]:
        return not_bookable(ctx, row)
    if args.zip_code not in ctx.settings["zip_areas"]:
        return blocked(SAY_OUT_OF_AREA, "out_of_area")
    return None


def not_bookable(ctx: ToolContext, row: sqlite3.Row) -> Block:
    first = row["first_step_code"]
    if first:
        name = ctx.conn.execute("SELECT name FROM services WHERE code = ?", (first,)).fetchone()
        return blocked(
            f"This service cannot be booked online. Offer the bookable first step "
            f"{first} ({name['name']}) or a person. Call handoff_to_staff only if the "
            "customer then asks for a person.",
            "not_bookable_online",
        )
    return blocked(
        "This service cannot be booked online: a team member arranges it. Offer a person; call "
        "handoff_to_staff only if the customer then asks for one.",
        "not_bookable_online",
    )


def handle(ctx: ToolContext, args: Args) -> ToolOutput:
    clock = ctx.rules.clock
    row = service(ctx.conn, args.service_code)
    assert row is not None  # checked by the gate
    start = datetime.combine(args.date_from, time(0), clock.tz)
    end = datetime.combine(args.date_to + timedelta(days=1), time(0), clock.tz)
    candidates = ctx.conn.execute(
        "SELECT s.* FROM slots s JOIN technicians t ON t.id = s.technician_id"
        " WHERE s.status = 'open' AND t.active = 1 AND s.starts_at >= ? AND s.starts_at < ?"
        " AND s.starts_at > ? ORDER BY s.starts_at, t.name",
        (to_utc_text(start), to_utc_text(end), to_utc_text(ctx.now)),
    ).fetchall()
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for first in candidates:
        if first["starts_at"] in seen:
            continue
        local = clock.local(parse_utc(first["starts_at"]))
        if args.part_of_day == "morning" and local.time() >= NOON:
            continue
        if args.part_of_day == "afternoon" and local.time() < NOON:
            continue
        run = visit_slots(ctx.conn, first, row, ctx.now)
        if run is None:
            continue
        seen.add(first["starts_at"])
        finish = clock.local(parse_utc(first["ends_at"]))
        found.append({"slot_id": first["id"], "when": format_slot_short(local, finish)})
        if len(found) == MAX_RESULTS:
            break
    data: dict[str, object] = {
        "service_code": row["code"],
        "date_from": args.date_from.isoformat(),
        "date_to": args.date_to.isoformat(),
        "slots": found,
    }
    if not found:
        data["note"] = "No open arrival windows in that range. Offer to look at other dates."
    return ToolOutput(data=data)


SPEC = ToolSpec(
    name=ToolName.CHECK_AVAILABILITY,
    kind=ToolKind.READ,
    args=Args,
    description=(
        "Find open 2-hour arrival windows for a service between two dates (ISO dates exactly "
        "as the state summary gives them) at a ZIP code. Returns at most 6 windows with slot_ids."
    ),
    handler=handle,
    rate_limit=10,
    check_ids=check_ids,
    check_policy=check_policy,
)
