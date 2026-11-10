"""Notification drafts from fixed templates. Nothing is ever sent (rule 4).

Each `outbox_kind` has one template. Code fills it from the booking, its service, its slot and
its customer; the model never writes a draft. Staff read the drafts in the console and copy them
if they choose to send them by hand.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from citebook.models import ActionType, OutboxKind
from citebook.timeutil import BusinessClock, format_arrival, parse_utc, to_utc_text

TEMPLATES: dict[OutboxKind, str] = {
    OutboxKind.HOLD_RECEIVED: (
        "Hi {name}, we have your booking request {reference}: {service}, {window}. It is held, "
        "not confirmed yet. A team member will confirm it, usually within {typical_hours} "
        "business hours."
    ),
    OutboxKind.BOOKING_CONFIRMED: "Hi {name}, your booking {reference} is confirmed: {service}, "
    "{window}.",
    OutboxKind.HOLD_DECLINED: (
        "Hi {name}, we could not confirm your booking request {reference}: {service}, {window}. "
        "Reason from our team: {reason}"
    ),
    OutboxKind.HOLD_EXPIRED: (
        "Hi {name}, your booking request {reference} ({service}, {window}) was not confirmed in "
        "time, so the time is no longer held. Contact us to choose another time."
    ),
    OutboxKind.BOOKING_RESCHEDULED: "Hi {name}, your booking {reference} has moved: {service}, "
    "{window}.",
    OutboxKind.BOOKING_CANCELLED: "Hi {name}, your booking {reference} is cancelled: {service}, "
    "{window}.",
    OutboxKind.REQUEST_RECEIVED: (
        "Hi {name}, we have received your {request}{about}. A team member will review it and reply."
    ),
}

REQUEST_LABELS: dict[ActionType, str] = {
    ActionType.RESCHEDULE: "request to move your booking",
    ActionType.CANCELLATION: "cancellation request",
    ActionType.REFUND_REQUEST: "refund request",
    ActionType.POLICY_EXCEPTION: "request for an exception",
}


def booking_fields(
    conn: sqlite3.Connection, clock: BusinessClock, booking_id: str
) -> dict[str, str]:
    """Name, reference, service and arrival window of a booking, for templates and cards."""
    row = conn.execute(
        "SELECT b.reference, c.name AS customer, v.name AS service, s.starts_at, s.ends_at"
        " FROM bookings b JOIN customers c ON c.id = b.customer_id"
        " JOIN services v ON v.code = b.service_code JOIN slots s ON s.id = b.slot_id"
        " WHERE b.id = ?",
        (booking_id,),
    ).fetchone()
    start = clock.local(parse_utc(row["starts_at"]))
    end = clock.local(parse_utc(row["ends_at"]))
    return {
        "name": row["customer"],
        "reference": row["reference"],
        "service": row["service"],
        "window": format_arrival(start, end),
    }


def render(kind: OutboxKind, fields: dict[str, str]) -> str:
    return TEMPLATES[kind].format(**fields)


def draft(
    conn: sqlite3.Connection,
    clock: BusinessClock,
    kind: OutboxKind,
    now: datetime,
    *,
    booking_id: str | None = None,
    customer_id: str | None = None,
    **extra: str,
) -> str:
    """Write one draft and return its id. A request without a booking names the customer only."""
    if booking_id is not None:
        fields = booking_fields(conn, clock, booking_id)
    elif customer_id is not None:
        name = conn.execute("SELECT name FROM customers WHERE id = ?", (customer_id,)).fetchone()
        fields = {"name": name["name"]}
    else:
        raise ValueError("a draft needs a booking or a customer")
    fields.update(extra)
    if kind is OutboxKind.REQUEST_RECEIVED:
        fields.setdefault("about", f" for booking {fields['reference']}" if booking_id else "")
    body = render(kind, fields)
    draft_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO outbox (id, booking_id, kind, body, created_at) VALUES (?, ?, ?, ?, ?)",
        (draft_id, booking_id, kind.value, body, to_utc_text(now)),
    )
    return draft_id
