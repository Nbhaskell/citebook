"""propose_hold: a card for holding one arrival window, backed by a quote for that window.

The quote, the slot and the customer's ZIP code must agree. Nothing changes until the customer
presses Confirm; then the booking is held and waits for staff (schema section 2.3).
"""

from __future__ import annotations

from pydantic import Field

from citebook.models import ActionType, ToolKind, ToolName
from citebook.timeutil import parse_utc
from citebook.tools.base import Block, ToolArgs, ToolContext, ToolOutput, ToolSpec, blocked, invalid
from citebook.tools.check_availability import not_bookable
from citebook.tools.common import (
    SAY_SLOT_GONE,
    SAY_UNKNOWN_QUOTE,
    SAY_UNKNOWN_SLOT,
    arrival,
    make_card,
    money,
    quote,
    quote_problem,
    service,
    slot,
    total_label,
    visit_slots,
)


class Args(ToolArgs):
    slot_id: str = Field(max_length=64, description="slot_id from check_availability")
    quote_id: str = Field(max_length=64, description="quote_id from quote_price for this slot")


def check_ids(ctx: ToolContext, args: Args) -> Block | None:
    if slot(ctx.conn, args.slot_id) is None:
        return invalid(SAY_UNKNOWN_SLOT, "unknown_slot")
    if quote(ctx, args.quote_id) is None:
        return invalid(SAY_UNKNOWN_QUOTE, "unknown_quote")
    return None


def _customer(ctx: ToolContext) -> dict[str, str] | None:
    row = ctx.conn.execute(
        "SELECT id, address, zip_code FROM customers WHERE id = ?",
        (ctx.conversation["customer_id"],),
    ).fetchone()
    return dict(row) if row else None


def check_policy(ctx: ToolContext, args: Args) -> Block | None:
    quote_row = quote(ctx, args.quote_id)
    assert quote_row is not None
    customer = _customer(ctx)
    zip_code = customer["zip_code"] if customer else quote_row["zip_code"]
    problem = quote_problem(ctx, quote_row, args.slot_id, quote_row["service_code"], zip_code)
    if problem:
        return problem
    row = service(ctx.conn, quote_row["service_code"])
    assert row is not None
    if not row["bookable_online"]:
        return not_bookable(ctx, row)
    first = slot(ctx.conn, args.slot_id)
    assert first is not None
    if visit_slots(ctx.conn, first, row, ctx.now) is None:
        return blocked(SAY_SLOT_GONE, "slot_not_open")
    return None


def handle(ctx: ToolContext, args: Args) -> ToolOutput:
    customer = _customer(ctx)
    if customer is None:  # reachable only when an ablation switches the contact check off
        return ToolOutput(
            ok=False, error="NO_CONTACT", say="Collect contact details with collect_contact."
        )
    quote_row = quote(ctx, args.quote_id)
    first = slot(ctx.conn, args.slot_id)
    assert quote_row is not None and first is not None
    row = service(ctx.conn, quote_row["service_code"])
    assert row is not None
    run = visit_slots(ctx.conn, first, row, ctx.now)
    assert run is not None
    hours = ctx.settings["staff_confirm_typical_business_hours"]
    summary = " · ".join(
        [
            row["name"],
            arrival(ctx.rules.clock, first),
            customer["address"],
            f"Total {money(quote_row['total_minor'])}{total_label(row['pricing_kind'])}",
            f"Held until our team confirms, usually within {hours} business hours.",
        ]
    )
    payload = {
        "customer_id": customer["id"],
        "service_code": row["code"],
        "slot_ids": [s["id"] for s in run],
        "quote_id": quote_row["id"],
        "total_minor": quote_row["total_minor"],
    }
    return make_card(
        ctx,
        ActionType.HOLD_CREATE,
        payload,
        summary,
        expires_at=parse_utc(quote_row["expires_at"]),
    )


SPEC = ToolSpec(
    name=ToolName.PROPOSE_HOLD,
    kind=ToolKind.PROPOSE,
    args=Args,
    description=(
        "Show the customer a confirmation card to hold an arrival window. Needs contact details "
        "(collect_contact) and a quote_id from quote_price for the same slot_id. Nothing is "
        "booked until the customer presses Confirm, and staff then confirm the hold."
    ),
    handler=handle,
    needs_contact=True,
    creates=ActionType.HOLD_CREATE,
    rate_limit=5,
    check_ids=check_ids,
    check_policy=check_policy,
)
