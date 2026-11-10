"""Shared reads and checks for the tools: settings, services, slots, quotes, bookings."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

from citebook.actions import propose
from citebook.models import ActionType, PricingKind
from citebook.money import format_money
from citebook.timeutil import BusinessClock, format_arrival, parse_utc
from citebook.tools.base import Block, ToolContext, ToolOutput, blocked, invalid

SEED_DIR = Path(__file__).resolve().parent.parent.parent / "db" / "seed"
PRICE_BOOK_PATH = SEED_DIR / "price_book.json"

SAY_UNKNOWN_SERVICE = "Unknown service_code. Call list_services and use one of its codes."
SAY_UNKNOWN_SLOT = "Unknown slot_id. Use a slot_id from check_availability."
SAY_UNKNOWN_QUOTE = "Unknown quote_id. Call quote_price for this slot and use its quote_id."
SAY_OUT_OF_AREA = (
    "That ZIP code is outside our service area. Tell the customer, and offer a person "
    "(handoff_to_staff with reason OUT_OF_SERVICE_AREA)."
)
SAY_SLOT_GONE = "That arrival window is not available. Call check_availability again."
SAY_QUOTE_EXPIRED = "The price has expired. Call quote_price again for the same slot."
SAY_NO_VERIFIED_BOOKING = (
    "No verified booking. Ask for the booking reference and the last 4 digits of the phone "
    "number, and call verify_customer."
)
SAY_NOT_VERIFIED_BOOKING = (
    "That reference is not the verified booking. To work on another booking, ask for its "
    "reference and the last 4 digits of the phone number, and call verify_customer."
)


def load_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM settings")
    }


@cache
def price_book() -> dict[str, Any]:
    book: dict[str, Any] = json.loads(PRICE_BOOK_PATH.read_text(encoding="utf-8"))
    return book


@cache
def safety() -> dict[str, Any]:
    """The published safety advice and emergency line (from facts.json via build_data)."""
    data: dict[str, Any] = json.loads((SEED_DIR / "safety.json").read_text(encoding="utf-8"))
    return data


def service(conn: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM services WHERE code = ? AND active = 1", (code,)
    ).fetchone()
    return row


def slot(conn: sqlite3.Connection, slot_id: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM slots WHERE id = ?", (slot_id,)
    ).fetchone()
    return row


def quote(ctx: ToolContext, quote_id: str) -> sqlite3.Row | None:
    """A quote only if this conversation created it."""
    row: sqlite3.Row | None = ctx.conn.execute(
        "SELECT * FROM quotes WHERE id = ? AND conversation_id = ?",
        (quote_id, ctx.conversation_id),
    ).fetchone()
    return row


def technician_can(conn: sqlite3.Connection, technician_id: str, skill: str) -> bool:
    row = conn.execute(
        "SELECT skills FROM technicians WHERE id = ? AND active = 1", (technician_id,)
    ).fetchone()
    return row is not None and skill in json.loads(row["skills"])


def visit_slots(
    conn: sqlite3.Connection, first: sqlite3.Row, service_row: sqlite3.Row, now: datetime
) -> list[sqlite3.Row] | None:
    """The open windows a visit starting in `first` needs, or None if it cannot start there."""
    if first["status"] != "open" or parse_utc(first["starts_at"]) <= now:
        return None
    if not technician_can(conn, first["technician_id"], service_row["skill"]):
        return None
    run = [first]
    while len(run) < service_row["windows_needed"]:
        nxt = conn.execute(
            "SELECT * FROM slots WHERE technician_id = ? AND starts_at = ? AND status = 'open'",
            (first["technician_id"], run[-1]["ends_at"]),
        ).fetchone()
        if nxt is None:
            return None
        run.append(nxt)
    return run


def arrival(clock: BusinessClock, slot_row: sqlite3.Row) -> str:
    return format_arrival(
        clock.local(parse_utc(slot_row["starts_at"])), clock.local(parse_utc(slot_row["ends_at"]))
    )


def total_label(pricing_kind: str) -> str:
    return {
        PricingKind.STARTING_PRICE: " (starting price)",
        PricingKind.HOURLY: " (estimate: labour for the minimum time, parts extra)",
    }.get(PricingKind(pricing_kind), "")


def money(minor: int) -> str:
    return format_money(minor)


def verified_booking(ctx: ToolContext) -> sqlite3.Row | None:
    """The second ownership layer: only the verified booking of the verified customer."""
    row: sqlite3.Row | None = ctx.conn.execute(
        "SELECT * FROM bookings WHERE id = :booking AND customer_id = :customer",
        {
            "booking": ctx.conversation["verified_booking_id"],
            "customer": ctx.conversation["customer_id"],
        },
    ).fetchone()
    return row


def normal_reference(reference: str) -> str:
    return reference.strip().upper()


SAY_ZIP_NOT_GIVEN = "Ask the customer for the ZIP code of the job. Never guess a ZIP code."


def zip_problem(ctx: ToolContext, zip_code: str) -> Block | None:
    """A ZIP code must come from the customer: their saved details or their own messages.

    The model has been seen to invent ZIP codes and then tell the customer the area is not
    served. A conversation with no customer message (tools driven directly) has nothing to
    compare with, so the check applies once the customer has written (D-063)."""
    texts = [
        r["content"]
        for r in ctx.conn.execute(
            "SELECT content FROM messages WHERE conversation_id = ? AND role = 'customer'",
            (ctx.conversation_id,),
        )
    ]
    if not texts:
        return None
    saved = ctx.conn.execute(
        "SELECT zip_code FROM customers WHERE id = ?", (ctx.conversation["customer_id"],)
    ).fetchone()
    if saved is not None and saved["zip_code"] == zip_code:
        return None
    if any(re.search(rf"(?<!\d){re.escape(zip_code)}(?!\d)", text) for text in texts):
        return None
    return invalid(SAY_ZIP_NOT_GIVEN, "zip_not_from_customer")


def check_reference_is_verified(ctx: ToolContext, args: Any) -> Block | None:
    """Gate ownership check: the reference must be the one booking this conversation verified."""
    booking_id = ctx.conversation["verified_booking_id"]
    if not booking_id:
        return None  # NEEDS_VERIFICATION (check 5) refuses it with the better hint
    row = ctx.conn.execute("SELECT reference FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if row is None or row["reference"] != normal_reference(args.booking_reference):
        return invalid(SAY_NOT_VERIFIED_BOOKING, "not_verified_booking")
    return None


def make_card(
    ctx: ToolContext,
    action_type: ActionType,
    payload: dict[str, Any],
    summary: str,
    expires_at: datetime,
    booking_id: str | None = None,
) -> ToolOutput:
    """Create the proposal behind a confirmation card. Any open card is withdrawn first."""
    assert ctx.tool_call_id is not None
    action_id = propose(
        ctx.conn,
        conversation_id=ctx.conversation_id,
        tool_call_id=ctx.tool_call_id,
        action_type=action_type,
        payload=payload,
        summary=summary,
        expires_at=expires_at,
        now=ctx.now,
        booking_id=booking_id,
    )
    return ToolOutput(
        data={
            "action_id": action_id,
            "card": "shown",
            "summary": summary,
            "next": "The customer sees a card with Confirm and Cancel buttons. Nothing has "
            "happened yet. Tell the customer say_to_customer.",
            "say_to_customer": SAY_CARD[action_type],
        },
        untrusted_fields=["summary"],
    )


SAY_CARD = {
    ActionType.HOLD_CREATE: "Please check the details on the card and press Confirm to request "
    "this booking. Our team then confirms it.",
    ActionType.RESCHEDULE: "Please check the new time on the card and press Confirm to move your "
    "booking.",
    ActionType.CANCELLATION: "Please check the card and press Confirm to cancel your booking.",
    ActionType.REFUND_REQUEST: "Please press Confirm on the card to send your refund request to "
    "our team. A person will review it.",
    ActionType.POLICY_EXCEPTION: "Please press Confirm on the card to send your request to our "
    "team. A person will review it.",
}


def card_expiry(ctx: ToolContext) -> datetime:
    """Requests have no quote, so their card lasts as long as a quote would (D-056)."""
    return ctx.now + timedelta(minutes=ctx.settings["quote_ttl_minutes"])


def check_booking_open(ctx: ToolContext, _args: Any) -> Block | None:
    row = verified_booking(ctx)
    if row is not None and row["status"] not in {"held", "confirmed"}:
        return blocked(
            "This booking is no longer active, so it cannot be changed in the chat. "
            "Offer a person (handoff_to_staff).",
            "booking_not_active",
        )
    return None


def quote_problem(
    ctx: ToolContext, quote_row: sqlite3.Row, slot_id: str, service_code: str, zip_code: str
) -> Block | None:
    """A quote can back a hold or reschedule only for the same service, slot and ZIP code."""
    if parse_utc(quote_row["expires_at"]) <= ctx.now:
        return blocked(SAY_QUOTE_EXPIRED, "quote_expired")
    if quote_row["slot_id"] != slot_id:
        return blocked(
            "That quote is for a different arrival window. Call quote_price with this slot_id.",
            "quote_other_slot",
        )
    if quote_row["service_code"] != service_code:
        return blocked(
            "That quote is for a different service. Call quote_price for this service.",
            "quote_other_service",
        )
    if quote_row["zip_code"] != zip_code:
        return blocked(
            "That quote is for a different ZIP code than the service address. Call quote_price "
            "with the ZIP code of the service address.",
            "quote_other_zip",
        )
    return None
