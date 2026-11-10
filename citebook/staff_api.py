"""Staff routes (TRD section 11): queue, conversation detail, decisions, take-over, close, staff
messages, calendar and outbox.

v1 has no staff login: the API listens on 127.0.0.1 only, like the sister projects, and must not
be exposed without one (TRD section 13). Staff decisions run the same state machine as everything
else (citebook/actions.py): approving a refund records the decision and moves no money.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from citebook.actions import (
    DecisionError,
    Rules,
    TransitionError,
    close_conversation,
    decide,
    take_over,
)
from citebook.api_common import ERRORS, ApiError, Db, Now
from citebook.db import transaction
from citebook.events import audit
from citebook.expiry import expire_due
from citebook.loop import add_message
from citebook.models import Actor, StaffDecision
from citebook.money import format_money
from citebook.timeutil import format_arrival, format_window, parse_utc, to_utc_text
from citebook.tools.common import load_settings

router = APIRouter(prefix="/api/staff", tags=["staff"])
CALENDAR_MAX_DAYS = 14


# --- shapes -----------------------------------------------------------------------------------


class QueueItem(BaseModel):
    action_id: str
    conversation_id: str
    action_type: str
    status: str
    reason: str | None
    origin: str
    summary: str
    customer_name: str | None
    booking_reference: str | None
    visit: str | None
    total: str | None
    created_at: str
    updated_at: str
    hold_expires_at: str | None


class Queue(BaseModel):
    needs_decision: list[QueueItem]
    handoffs: list[QueueItem]
    expired_holds: list[QueueItem]
    recent: list[QueueItem]


class CheckOut(BaseModel):
    attempt: int
    code: str
    passed: bool
    detail: dict[str, Any]


class ToolEvent(BaseModel):
    name: str
    arguments: Any
    ok: bool
    gate_decision: str
    say: str | None
    result: dict[str, Any]


class MessageOut(BaseModel):
    seq: int
    role: str
    text: str
    at: str
    flags: list[str] = []
    checks: list[CheckOut] = []
    tool: ToolEvent | None = None


class ActionOut(BaseModel):
    action_id: str
    action_type: str
    status: str
    origin: str
    reason: str | None
    summary: str
    booking_reference: str | None
    created_at: str
    updated_at: str
    failure_detail: str | None


class CustomerOut(BaseModel):
    name: str
    phone: str
    email: str | None
    address: str
    zip_code: str


class ConversationDetail(BaseModel):
    conversation_id: str
    status: str
    channel: str
    verification_state: str
    verified_booking_reference: str | None
    customer: CustomerOut | None
    started_at: str
    last_activity_at: str
    messages: list[MessageOut]
    actions: list[ActionOut]


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=500)
    review_seconds: int | None = Field(default=None, ge=0)


class DecisionOut(BaseModel):
    outcome: str
    action_status: str


class ConversationState(BaseModel):
    status: str


class StaffMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class CalendarSlot(BaseModel):
    slot_id: str
    technician_id: str
    starts_at: str
    ends_at: str
    label: str
    status: str
    booking_reference: str | None
    service: str | None
    hold_expires_at: str | None


class TechnicianOut(BaseModel):
    technician_id: str
    name: str


class Calendar(BaseModel):
    first_day: str
    days: int
    technicians: list[TechnicianOut]
    slots: list[CalendarSlot]


class DraftOut(BaseModel):
    draft_id: str
    kind: str
    body: str
    booking_reference: str | None
    created_at: str


# --- helpers ----------------------------------------------------------------------------------


def _rules(conn: sqlite3.Connection) -> Rules:
    return Rules.from_settings(load_settings(conn))


_ITEM_SQL = """
SELECT a.*, b.reference, b.hold_expires_at, c.name AS customer_name, s.starts_at, s.ends_at,
       q.total_minor
FROM actions a
JOIN conversations v ON v.id = a.conversation_id
LEFT JOIN bookings b ON b.id = a.booking_id
LEFT JOIN customers c ON c.id = COALESCE(b.customer_id, v.customer_id)
LEFT JOIN slots s ON s.id = b.slot_id
LEFT JOIN quotes q ON q.id = b.quote_id
"""


def _item(rules: Rules, row: sqlite3.Row) -> QueueItem:
    visit = None
    if row["starts_at"]:
        visit = format_arrival(
            rules.clock.local(parse_utc(row["starts_at"])),
            rules.clock.local(parse_utc(row["ends_at"])),
        )
    payload = json.loads(row["payload"])
    total_minor = payload.get("total_minor", row["total_minor"])
    return QueueItem(
        action_id=row["id"],
        conversation_id=row["conversation_id"],
        action_type=row["action_type"],
        status=row["status"],
        reason=row["escalation_reason"],
        origin=row["origin"],
        summary=row["summary"],
        customer_name=row["customer_name"],
        booking_reference=row["reference"],
        visit=visit,
        total=format_money(total_minor) if isinstance(total_minor, int) else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        hold_expires_at=row["hold_expires_at"] if row["status"] == "pending_staff" else None,
    )


def _conversation(conn: sqlite3.Connection, conversation_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "No such conversation.")
    return row


# --- routes -----------------------------------------------------------------------------------


@router.get("/queue", responses=ERRORS)
def queue(conn: Db, now: Now) -> Queue:
    """Pending decisions and handoffs oldest first; expired holds and recent decisions newest
    first (UI brief section 4)."""
    rules = _rules(conn)
    expire_due(conn, rules, now)
    since = to_utc_text(now - timedelta(days=2))

    def rows(where: str, order: str, args: tuple[str, ...] = ()) -> list[QueueItem]:
        found = conn.execute(f"{_ITEM_SQL} WHERE {where} ORDER BY {order}", args).fetchall()
        return [_item(rules, r) for r in found]

    return Queue(
        needs_decision=rows(
            "a.status = 'pending_staff' AND a.action_type <> 'handoff'", "a.updated_at, a.id"
        ),
        handoffs=rows(
            "a.status = 'pending_staff' AND a.action_type = 'handoff'", "a.created_at, a.id"
        ),
        expired_holds=rows(
            "a.status = 'expired' AND a.action_type = 'hold_create' AND a.booking_id IS NOT NULL"
            " AND a.updated_at >= ?",
            "a.updated_at DESC",
            (since,),
        ),
        recent=rows(
            "a.status IN ('approved', 'rejected', 'executed', 'failed') AND a.updated_at >= ?",
            "a.updated_at DESC LIMIT 25",
            (since,),
        ),
    )


@router.get("/conversations/{conversation_id}", responses=ERRORS)
def conversation_detail(conversation_id: str, conn: Db) -> ConversationDetail:
    conv = _conversation(conn, conversation_id)
    customer = None
    if conv["customer_id"]:
        c = conn.execute("SELECT * FROM customers WHERE id = ?", (conv["customer_id"],)).fetchone()
        customer = CustomerOut(
            name=c["name"],
            phone=c["phone"],
            email=c["email"],
            address=c["address"],
            zip_code=c["zip_code"],
        )
    verified = None
    if conv["verified_booking_id"]:
        verified = conn.execute(
            "SELECT reference FROM bookings WHERE id = ?", (conv["verified_booking_id"],)
        ).fetchone()["reference"]

    flags: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT f.message_id, f.flag FROM screen_flags f JOIN messages m ON m.id = f.message_id"
        " WHERE m.conversation_id = ?",
        (conversation_id,),
    ):
        flags.setdefault(row["message_id"], []).append(row["flag"])
    checks: dict[str, list[CheckOut]] = {}
    for row in conn.execute(
        "SELECT r.* FROM reply_checks r JOIN messages m ON m.id = r.message_id"
        " WHERE m.conversation_id = ? ORDER BY r.attempt, r.check_code",
        (conversation_id,),
    ):
        checks.setdefault(row["message_id"], []).append(
            CheckOut(
                attempt=row["attempt"],
                code=row["check_code"],
                passed=bool(row["passed"]),
                detail=json.loads(row["detail"]),
            )
        )

    messages = []
    for row in conn.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq", (conversation_id,)
    ):
        tool = None
        text = row["content"]
        if row["role"] == "tool":
            record = json.loads(row["content"])
            result = record["result"]
            tool = ToolEvent(
                name=record["name"],
                arguments=record["arguments"],
                ok=bool(result.get("ok")),
                gate_decision=result.get("gate_decision", "ALLOWED"),
                say=result.get("say"),
                result=result,
            )
            text = record["name"]
        messages.append(
            MessageOut(
                seq=row["seq"],
                role=row["role"],
                text=text,
                at=row["created_at"],
                flags=flags.get(row["id"], []),
                checks=checks.get(row["id"], []),
                tool=tool,
            )
        )

    actions = [
        ActionOut(
            action_id=r["id"],
            action_type=r["action_type"],
            status=r["status"],
            origin=r["origin"],
            reason=r["escalation_reason"],
            summary=r["summary"],
            booking_reference=r["reference"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            failure_detail=r["failure_detail"],
        )
        for r in conn.execute(
            "SELECT a.*, b.reference FROM actions a LEFT JOIN bookings b ON b.id = a.booking_id"
            " WHERE a.conversation_id = ? ORDER BY a.created_at, a.rowid",
            (conversation_id,),
        )
    ]
    return ConversationDetail(
        conversation_id=conv["id"],
        status=conv["status"],
        channel=conv["channel"],
        verification_state=conv["verification_state"],
        verified_booking_reference=verified,
        customer=customer,
        started_at=conv["started_at"],
        last_activity_at=conv["last_activity_at"],
        messages=messages,
        actions=actions,
    )


@router.post("/actions/{action_id}/decide", responses=ERRORS)
def staff_decide(action_id: str, body: DecisionIn, conn: Db, now: Now) -> DecisionOut:
    rules = _rules(conn)
    expire_due(conn, rules, now)
    if conn.execute("SELECT 1 FROM actions WHERE id = ?", (action_id,)).fetchone() is None:
        raise ApiError(404, "NOT_FOUND", "No such action.")
    try:
        result = decide(
            conn,
            rules,
            action_id,
            StaffDecision(body.decision),
            body.reason,
            now,
            review_seconds=body.review_seconds,
        )
    except DecisionError as error:
        raise ApiError(409, "CANNOT_DECIDE", str(error)) from error
    except TransitionError as error:
        raise ApiError(409, "CANNOT_DECIDE", "This action changed meanwhile.") from error
    status = conn.execute("SELECT status FROM actions WHERE id = ?", (action_id,)).fetchone()[0]
    return DecisionOut(outcome=result.outcome, action_status=status)


@router.post("/conversations/{conversation_id}/take-over", responses=ERRORS)
def staff_take_over(conversation_id: str, conn: Db, now: Now) -> ConversationState:
    conv = _conversation(conn, conversation_id)
    if conv["status"] in {"closed", "abandoned"}:
        raise ApiError(409, "CONVERSATION_ENDED", "This conversation has ended.")
    take_over(conn, conversation_id, now)
    return ConversationState(status=_conversation(conn, conversation_id)["status"])


@router.post("/conversations/{conversation_id}/close", responses=ERRORS)
def staff_close(conversation_id: str, conn: Db, now: Now) -> ConversationState:
    conv = _conversation(conn, conversation_id)
    if conv["status"] == "closed":
        raise ApiError(409, "CONVERSATION_ENDED", "This conversation is already closed.")
    close_conversation(conn, conversation_id, now)
    return ConversationState(status="closed")


@router.post("/conversations/{conversation_id}/messages", responses=ERRORS)
def staff_message(
    conversation_id: str, body: StaffMessageIn, conn: Db, now: Now
) -> ConversationState:
    """Staff write to the customer in the widget. Only after taking over: the bot is paused."""
    conv = _conversation(conn, conversation_id)
    if conv["status"] != "handed_off":
        raise ApiError(409, "TAKE_OVER_FIRST", "Take over the conversation before you write.")
    add_message(conn, conversation_id, "staff", body.text, now)
    with transaction(conn):
        audit(conn, Actor.STAFF, "staff_message", conversation_id=conversation_id)
    return ConversationState(status=conv["status"])


@router.get("/calendar", responses=ERRORS)
def calendar(
    conn: Db,
    now: Now,
    first_day: Annotated[date | None, Query(alias="from")] = None,
    days: Annotated[int, Query(ge=1, le=CALENDAR_MAX_DAYS)] = 7,
) -> Calendar:
    rules = _rules(conn)
    expire_due(conn, rules, now)
    start_day = first_day or rules.clock.local(now).date()
    start = datetime.combine(start_day, time(0), rules.clock.tz)
    end = datetime.combine(start_day + timedelta(days=days), time(0), rules.clock.tz)
    technicians = [
        TechnicianOut(technician_id=r["id"], name=r["name"])
        for r in conn.execute("SELECT id, name FROM technicians WHERE active = 1 ORDER BY name")
    ]
    booked: dict[str, sqlite3.Row] = {}
    for row in conn.execute(
        "SELECT b.*, v.name AS service, v.windows_needed FROM bookings b"
        " JOIN services v ON v.code = b.service_code WHERE b.status IN ('held', 'confirmed')"
    ):
        slot_row = conn.execute("SELECT * FROM slots WHERE id = ?", (row["slot_id"],)).fetchone()
        booked[slot_row["id"]] = row
        for _ in range(row["windows_needed"] - 1):  # the second window of a 2-window visit
            slot_row = conn.execute(
                "SELECT * FROM slots WHERE technician_id = ? AND starts_at = ?",
                (slot_row["technician_id"], slot_row["ends_at"]),
            ).fetchone()
            if slot_row is None:
                break
            booked[slot_row["id"]] = row
    slots = []
    for row in conn.execute(
        "SELECT * FROM slots WHERE starts_at >= ? AND starts_at < ? ORDER BY starts_at",
        (to_utc_text(start), to_utc_text(end)),
    ):
        booking = booked.get(row["id"])
        slots.append(
            CalendarSlot(
                slot_id=row["id"],
                technician_id=row["technician_id"],
                starts_at=row["starts_at"],
                ends_at=row["ends_at"],
                label=format_window(
                    rules.clock.local(parse_utc(row["starts_at"])),
                    rules.clock.local(parse_utc(row["ends_at"])),
                ),
                status=row["status"],
                booking_reference=booking["reference"] if booking else None,
                service=booking["service"] if booking else None,
                hold_expires_at=booking["hold_expires_at"] if booking else None,
            )
        )
    return Calendar(
        first_day=start_day.isoformat(), days=days, technicians=technicians, slots=slots
    )


@router.get("/outbox", responses=ERRORS)
def outbox(conn: Db) -> list[DraftOut]:
    """Drafted notifications, newest first. Nothing is ever sent."""
    return [
        DraftOut(
            draft_id=r["id"],
            kind=r["kind"],
            body=r["body"],
            booking_reference=r["reference"],
            created_at=r["created_at"],
        )
        for r in conn.execute(
            "SELECT o.*, b.reference FROM outbox o LEFT JOIN bookings b ON b.id = o.booking_id"
            " ORDER BY o.created_at DESC, o.rowid DESC LIMIT 200"
        )
    ]
