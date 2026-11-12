"""propose_reschedule: a card for moving the verified booking to a new arrival window.

Needs a new quote for the new slot (D-013). More than `max_reschedules` is POLICY_BLOCKED. A late
move is allowed and goes to staff after Confirm (D-005, D-048); the card says so.
"""

from __future__ import annotations

from pydantic import Field

from citebook.models import ActionType, ToolKind, ToolName
from citebook.timeutil import is_late, parse_utc
from citebook.tools.base import Block, ToolArgs, ToolContext, ToolOutput, ToolSpec, blocked, invalid
from citebook.tools.common import (
    SAY_NO_VERIFIED_BOOKING,
    SAY_SLOT_GONE,
    SAY_UNKNOWN_QUOTE,
    SAY_UNKNOWN_SLOT,
    arrival,
    check_booking_open,
    check_reference_is_verified,
    day_not_asked,
    make_card,
    money,
    normal_reference,
    quote,
    quote_problem,
    service,
    slot,
    total_label,
    verified_booking,
    visit_slots,
)


class Args(ToolArgs):
    booking_reference: str = Field(max_length=20, description="The verified booking's reference")
    slot_id: str = Field(max_length=64, description="New slot_id from check_availability")
    quote_id: str = Field(max_length=64, description="quote_id from quote_price for the new slot")


def check_ids(ctx: ToolContext, args: Args) -> Block | None:
    if slot(ctx.conn, args.slot_id) is None:
        return invalid(SAY_UNKNOWN_SLOT, "unknown_slot")
    if quote(ctx, args.quote_id) is None:
        return invalid(SAY_UNKNOWN_QUOTE, "unknown_quote")
    return None


def check_policy(ctx: ToolContext, args: Args) -> Block | None:
    problem = check_booking_open(ctx, args)
    if problem:
        return problem
    booking = verified_booking(ctx)
    if booking is None:
        return None  # the handler's own SQL refuses it
    if booking["reschedule_count"] >= ctx.settings["max_reschedules"]:
        return blocked(
            "This booking has already been moved the most times allowed in the chat. Offer "
            "request_exception or a person (handoff_to_staff).",
            "max_reschedules",
        )
    if args.slot_id == booking["slot_id"]:
        return blocked("That is the booking's current arrival window.", "same_slot")
    quote_row = quote(ctx, args.quote_id)
    assert quote_row is not None
    zip_code = ctx.conn.execute(
        "SELECT zip_code FROM customers WHERE id = ?", (booking["customer_id"],)
    ).fetchone()["zip_code"]
    problem = quote_problem(ctx, quote_row, args.slot_id, booking["service_code"], zip_code)
    if problem:
        return problem
    row = service(ctx.conn, booking["service_code"])
    first = slot(ctx.conn, args.slot_id)
    assert row is not None and first is not None
    if visit_slots(ctx.conn, first, row, ctx.now) is None:
        return blocked(SAY_SLOT_GONE, "slot_not_open")
    return day_not_asked(ctx, first)


def handle(ctx: ToolContext, args: Args) -> ToolOutput:
    booking = verified_booking(ctx)
    if booking is None or booking["reference"] != normal_reference(args.booking_reference):
        return ToolOutput(ok=False, error="NOT_FOUND", say=SAY_NO_VERIFIED_BOOKING)
    row = service(ctx.conn, booking["service_code"])
    new_quote = quote(ctx, args.quote_id)
    old_slot = slot(ctx.conn, booking["slot_id"])
    new_slot = slot(ctx.conn, args.slot_id)
    assert row is not None and new_quote is not None and old_slot and new_slot
    run = visit_slots(ctx.conn, new_slot, row, ctx.now)
    assert run is not None
    old_total = ctx.conn.execute(
        "SELECT total_minor FROM quotes WHERE id = ?", (booking["quote_id"],)
    ).fetchone()["total_minor"]
    total = f"Total {money(new_quote['total_minor'])}{total_label(row['pricing_kind'])}"
    if new_quote["total_minor"] != old_total:
        total += f", was {money(old_total)}"
    parts = [
        f"Move {booking['reference']}, {row['name']}",
        f"From {arrival(ctx.rules.clock, old_slot)}",
        f"To {arrival(ctx.rules.clock, new_slot)}",
        total,
    ]
    hours = ctx.settings["free_cancel_hours"]
    if is_late(ctx.now, parse_utc(old_slot["starts_at"]), hours):
        parts.append(
            f"Less than {hours} hours before the current visit: a team member will review the "
            "move and any late fee."
        )
    payload = {
        "booking_id": booking["id"],
        "new_slot_ids": [s["id"] for s in run],
        "quote_id": new_quote["id"],
        "total_minor": new_quote["total_minor"],
    }
    return make_card(
        ctx,
        ActionType.RESCHEDULE,
        payload,
        " · ".join(parts),
        expires_at=parse_utc(new_quote["expires_at"]),
        booking_id=booking["id"],
    )


SPEC = ToolSpec(
    name=ToolName.PROPOSE_RESCHEDULE,
    kind=ToolKind.PROPOSE,
    args=Args,
    description=(
        "Show the customer a confirmation card to move the verified booking to a new arrival "
        "window. Needs a quote_id from quote_price for the new slot_id."
    ),
    handler=handle,
    needs_verified=True,
    creates=ActionType.RESCHEDULE,
    rate_limit=5,
    check_ids=check_ids,
    check_owner=check_reference_is_verified,
    check_policy=check_policy,
)
