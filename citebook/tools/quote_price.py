"""quote_price: a price computed in code from the price book (TRD section 6, citebook/pricing.py).

The quote is stored with this conversation and expires after `quote_ttl_minutes`. A quote needs a
slot_id before it can back a hold or a reschedule.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

from pydantic import Field

from citebook.models import PricingKind, ToolKind, ToolName
from citebook.money import format_money
from citebook.pricing import build_quote
from citebook.timeutil import format_clock, parse_utc, to_utc_text
from citebook.tools.base import Block, ToolArgs, ToolContext, ToolOutput, ToolSpec, blocked, invalid
from citebook.tools.common import (
    SAY_OUT_OF_AREA,
    SAY_SLOT_GONE,
    SAY_UNKNOWN_SERVICE,
    SAY_UNKNOWN_SLOT,
    arrival,
    price_book,
    service,
    slot,
    visit_slots,
    zip_problem,
)


class Args(ToolArgs):
    service_code: str = Field(max_length=40)
    zip_code: str = Field(pattern=r"^\d{5}$", description="5-digit ZIP code of the service address")
    slot_id: str | None = Field(
        default=None, max_length=64, description="slot_id from check_availability, if chosen"
    )


def check_ids(ctx: ToolContext, args: Args) -> Block | None:
    if service(ctx.conn, args.service_code) is None:
        return invalid(SAY_UNKNOWN_SERVICE, "unknown_service")
    if block := zip_problem(ctx, args.zip_code):
        return block
    if args.slot_id is not None and slot(ctx.conn, args.slot_id) is None:
        return invalid(SAY_UNKNOWN_SLOT, "unknown_slot")
    return None


def check_policy(ctx: ToolContext, args: Args) -> Block | None:
    row = service(ctx.conn, args.service_code)
    assert row is not None
    if row["pricing_kind"] == PricingKind.QUOTE_ONLY:
        say = "This job is priced by a written quote, so it cannot be priced or booked online. "
        first = row["first_step_code"]
        if first:
            name = ctx.conn.execute("SELECT name FROM services WHERE code = ?", (first,)).fetchone()
            say += (
                f"Offer the bookable first step {first} ({name['name']}) or a person "
                "(handoff_to_staff)."
            )
        else:
            say += "Offer a person (handoff_to_staff)."
        return blocked(say, "quote_only")
    if args.zip_code not in ctx.settings["zip_areas"]:
        return blocked(SAY_OUT_OF_AREA, "out_of_area")
    if args.slot_id is not None:
        first_slot = slot(ctx.conn, args.slot_id)
        assert first_slot is not None
        if visit_slots(ctx.conn, first_slot, row, ctx.now) is None:
            return blocked(SAY_SLOT_GONE, "slot_not_open")
    return None


def handle(ctx: ToolContext, args: Args) -> ToolOutput:
    row = service(ctx.conn, args.service_code)
    assert row is not None
    slot_row = slot(ctx.conn, args.slot_id) if args.slot_id else None
    start = parse_utc(slot_row["starts_at"]) if slot_row else None
    built = build_quote(
        dict(row), args.zip_code, start, price_book(), ctx.settings["zip_areas"], ctx.rules.clock
    )
    expires = ctx.now + timedelta(minutes=ctx.settings["quote_ttl_minutes"])
    quote_id = str(uuid.uuid4())
    ctx.conn.execute(
        "INSERT INTO quotes (id, conversation_id, service_code, slot_id, zip_code, lines,"
        " total_minor, is_estimate, estimate_note, price_book_version, created_at, expires_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            quote_id,
            ctx.conversation_id,
            built.service_code,
            args.slot_id,
            built.zip_code,
            json.dumps([{"label": label, "amount_minor": amount} for label, amount in built.lines]),
            built.total_minor,
            int(built.is_estimate),
            built.estimate_note or None,
            built.price_book_version,
            to_utc_text(ctx.now),
            to_utc_text(expires),
        ),
    )
    notes = [n for n in [built.estimate_note, *built.notes] if n]
    notes.append(f"Price valid until {format_clock(ctx.rules.clock.local(expires))}.")
    data: dict[str, object] = {
        "quote_id": quote_id,
        "service": row["name"],
        "total": format_money(built.total_minor),
        "lines": [[label, format_money(amount)] for label, amount in built.lines],
        "is_estimate": built.is_estimate,
        "note": " ".join(notes),
        "bookable_online": built.bookable_online,
    }
    if slot_row is not None:
        data["slot_id"] = slot_row["id"]
        data["when"] = arrival(ctx.rules.clock, slot_row)
    else:
        data["next"] = "To book, choose a slot_id from check_availability and quote again with it."
    return ToolOutput(data=data)


SPEC = ToolSpec(
    name=ToolName.QUOTE_PRICE,
    kind=ToolKind.READ,
    args=Args,
    description=(
        "Price a service at a ZIP code, optionally for one arrival window (slot_id). Code "
        "computes every line and the total; repeat them exactly. Returns a quote_id that a "
        "booking needs; it must include the chosen slot_id."
    ),
    handler=handle,
    rate_limit=10,
    check_ids=check_ids,
    check_policy=check_policy,
)
