"""Actions: proposal, customer confirmation, staff decision and execution (schema section 4).

Nothing here runs on the model's word. A proposal waits for the customer's Confirm, which must
carry the action's payload hash. Execution runs in one transaction; every slot change checks the
slot's version, so two confirmations for the same slot cannot both succeed (D-047). Refunds and
exceptions are only recorded: approving one changes no booking and moves no money.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from citebook.db import transaction
from citebook.events import audit
from citebook.models import (
    ActionOrigin,
    ActionStatus,
    ActionType,
    Actor,
    BookingStatus,
    ConversationStatus,
    EscalationReason,
    OutboxKind,
    SlotStatus,
    StaffDecision,
)
from citebook.outbox import REQUEST_LABELS, draft
from citebook.timeutil import BusinessClock, is_late, parse_utc, to_utc_text

S = ActionStatus
ACTION_TRANSITIONS: dict[ActionStatus | None, set[ActionStatus]] = {
    None: {S.PROPOSED, S.PENDING_STAFF},
    S.PROPOSED: {S.CUSTOMER_CONFIRMED, S.WITHDRAWN, S.EXPIRED},
    S.CUSTOMER_CONFIRMED: {S.EXECUTED, S.PENDING_STAFF, S.FAILED},
    S.PENDING_STAFF: {S.APPROVED, S.REJECTED, S.EXPIRED, S.EXECUTED},
    S.APPROVED: {S.EXECUTED, S.FAILED},
}
B = BookingStatus
BOOKING_TRANSITIONS: dict[BookingStatus, set[BookingStatus]] = {
    B.HELD: {B.CONFIRMED, B.EXPIRED, B.CANCELLED},
    B.CONFIRMED: {B.CANCELLED, B.COMPLETED, B.NO_SHOW},
}
SLOT_TRANSITIONS: dict[SlotStatus, set[SlotStatus]] = {
    SlotStatus.OPEN: {SlotStatus.HELD, SlotStatus.BOOKED},
    SlotStatus.HELD: {SlotStatus.BOOKED, SlotStatus.OPEN},
    SlotStatus.BOOKED: {SlotStatus.OPEN},
    SlotStatus.BLOCKED: set(),
}
REFERENCE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # no 0/O, 1/I/L


class TransitionError(Exception):
    """A status change the state machine does not allow."""


class SlotTaken(Exception):
    """A slot changed between the proposal and the execution."""


class DecisionError(Exception):
    """A staff decision that cannot apply to this action."""


@dataclass(frozen=True)
class Rules:
    clock: BusinessClock
    free_cancel_hours: int
    hold_ttl_business_hours: int
    staff_confirm_typical_business_hours: int = 2

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> Rules:
        return cls(
            clock=BusinessClock.from_settings(settings),
            free_cancel_hours=settings["free_cancel_hours"],
            hold_ttl_business_hours=settings["hold_ttl_business_hours"],
            staff_confirm_typical_business_hours=settings["staff_confirm_typical_business_hours"],
        )


@dataclass(frozen=True)
class ConfirmResult:
    """`outcome` is the action's status afterwards, or a refusal code when nothing changed."""

    outcome: str
    action_id: str
    booking_reference: str | None = None


def canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def check_transition(table: dict[Any, set[Any]], current: Any, target: Any) -> None:
    if target not in table.get(current, set()):
        raise TransitionError(f"{current} -> {target} is not allowed")


# --- low-level moves --------------------------------------------------------------------------


def set_action_status(
    conn: sqlite3.Connection,
    action_id: str,
    current: ActionStatus,
    target: ActionStatus,
    now: datetime,
    **columns: Any,
) -> None:
    check_transition(ACTION_TRANSITIONS, current, target)
    sets = ", ".join(f"{name} = :{name}" for name in columns)
    sql = f"UPDATE actions SET status = :target, updated_at = :now{', ' + sets if sets else ''}"
    cur = conn.execute(
        sql + " WHERE id = :id AND status = :current",
        {
            "target": target.value,
            "now": to_utc_text(now),
            "id": action_id,
            "current": current.value,
            **columns,
        },
    )
    if cur.rowcount != 1:
        raise TransitionError(f"action {action_id} is no longer {current}")


def _move_slot(conn: sqlite3.Connection, slot_id: str, target: SlotStatus) -> None:
    row = conn.execute("SELECT status, version FROM slots WHERE id = ?", (slot_id,)).fetchone()
    if row is None:
        raise SlotTaken(slot_id)
    current = SlotStatus(row["status"])
    if target not in SLOT_TRANSITIONS[current]:
        raise SlotTaken(slot_id)
    cur = conn.execute(
        "UPDATE slots SET status = ?, version = version + 1 WHERE id = ? AND version = ?",
        (target.value, slot_id, row["version"]),
    )
    if cur.rowcount != 1:
        raise SlotTaken(slot_id)


def _move_booking(
    conn: sqlite3.Connection, booking_id: str, target: BookingStatus, now: datetime
) -> None:
    row = conn.execute("SELECT status FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    check_transition(BOOKING_TRANSITIONS, BookingStatus(row["status"]), target)
    conn.execute(
        "UPDATE bookings SET status = ?, updated_at = ? WHERE id = ?",
        (target.value, to_utc_text(now), booking_id),
    )


def visit_slot_ids(conn: sqlite3.Connection, first_slot_id: str, windows_needed: int) -> list[str]:
    """The slot a visit starts in, plus the next windows of the same technician it also uses."""
    ids = [first_slot_id]
    row = conn.execute("SELECT * FROM slots WHERE id = ?", (first_slot_id,)).fetchone()
    for _ in range(windows_needed - 1):
        row = conn.execute(
            "SELECT * FROM slots WHERE technician_id = ? AND starts_at = ?",
            (row["technician_id"], row["ends_at"]),
        ).fetchone()
        if row is None:
            raise SlotTaken(first_slot_id)
        ids.append(row["id"])
    return ids


def _set_conversation(
    conn: sqlite3.Connection, conversation_id: str, status: ConversationStatus, now: datetime
) -> None:
    closed = to_utc_text(now) if status is ConversationStatus.CLOSED else None
    conn.execute(
        "UPDATE conversations SET status = ?, last_activity_at = ?,"
        " closed_at = COALESCE(?, closed_at) WHERE id = ?",
        (status.value, to_utc_text(now), closed, conversation_id),
    )


def settle_conversation(conn: sqlite3.Connection, conversation_id: str, now: datetime) -> None:
    """Back to `active` when no proposal is open, unless staff or the clock ended it."""
    status = conn.execute(
        "SELECT status FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()["status"]
    if status != ConversationStatus.AWAITING_CONFIRMATION:
        return
    open_ = conn.execute(
        "SELECT 1 FROM actions WHERE conversation_id = ? AND status = 'proposed'",
        (conversation_id,),
    ).fetchone()
    if not open_:
        _set_conversation(conn, conversation_id, ConversationStatus.ACTIVE, now)


def _withdraw_open(conn: sqlite3.Connection, conversation_id: str, now: datetime) -> None:
    for row in conn.execute(
        "SELECT id FROM actions WHERE conversation_id = ? AND status = 'proposed'",
        (conversation_id,),
    ).fetchall():
        set_action_status(conn, row["id"], S.PROPOSED, S.WITHDRAWN, now)
        audit(
            conn,
            Actor.SYSTEM,
            "action_withdrawn",
            conversation_id=conversation_id,
            action_id=row["id"],
        )


def new_reference(conn: sqlite3.Connection) -> str:
    while True:
        ref = "CB-" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))
        if not conn.execute("SELECT 1 FROM bookings WHERE reference = ?", (ref,)).fetchone():
            return ref


def _action(conn: sqlite3.Connection, action_id: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM actions WHERE id = ?", (action_id,)
    ).fetchone()
    return row


# --- creating actions -------------------------------------------------------------------------


def propose(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    tool_call_id: str,
    action_type: ActionType,
    payload: dict[str, Any],
    summary: str,
    expires_at: datetime,
    now: datetime,
    booking_id: str | None = None,
) -> str:
    """Create a proposal; any earlier open proposal in the conversation is withdrawn."""
    if action_type is ActionType.HANDOFF:
        raise ValueError("handoffs are created with create_handoff")
    action_id = str(uuid.uuid4())
    with transaction(conn):
        _withdraw_open(conn, conversation_id, now)
        stamp = to_utc_text(now)
        conn.execute(
            "INSERT INTO actions (id, conversation_id, origin, tool_call_id, action_type, status,"
            " booking_id, payload, summary, payload_hash, expires_at, created_at, updated_at)"
            " VALUES (?, ?, 'tool', ?, ?, 'proposed', ?, ?, ?, ?, ?, ?, ?)",
            (
                action_id,
                conversation_id,
                tool_call_id,
                action_type.value,
                booking_id,
                canonical(payload),
                summary,
                payload_hash(payload),
                to_utc_text(expires_at),
                stamp,
                stamp,
            ),
        )
        _set_conversation(conn, conversation_id, ConversationStatus.AWAITING_CONFIRMATION, now)
        audit(
            conn,
            Actor.ASSISTANT,
            "action_proposed",
            conversation_id=conversation_id,
            action_id=action_id,
            action_type=action_type.value,
        )
    return action_id


def withdraw(conn: sqlite3.Connection, action_id: str, conversation_id: str, now: datetime) -> bool:
    """The customer pressed Cancel on the card. False if the card was no longer open."""
    with transaction(conn):
        row = _action(conn, action_id)
        if row is None or row["conversation_id"] != conversation_id or row["status"] != "proposed":
            return False
        set_action_status(conn, action_id, S.PROPOSED, S.WITHDRAWN, now)
        audit(
            conn,
            Actor.CUSTOMER,
            "action_withdrawn",
            conversation_id=conversation_id,
            action_id=action_id,
        )
        settle_conversation(conn, conversation_id, now)
    return True


def create_handoff(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    origin: ActionOrigin,
    reason: EscalationReason,
    now: datetime,
    tool_call_id: str | None = None,
) -> str:
    """Hand the conversation to staff: open cards are withdrawn and the bot stops (D-009)."""
    if (origin is ActionOrigin.TOOL) != (tool_call_id is not None):
        raise ValueError("only a tool handoff has a tool call")
    action_id = str(uuid.uuid4())
    payload = {"reason": reason.value}
    with transaction(conn):
        _withdraw_open(conn, conversation_id, now)
        stamp = to_utc_text(now)
        conn.execute(
            "INSERT INTO actions (id, conversation_id, origin, tool_call_id, action_type, status,"
            " payload, summary, payload_hash, escalation_reason, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 'handoff', 'pending_staff', ?, ?, ?, ?, ?, ?)",
            (
                action_id,
                conversation_id,
                origin.value,
                tool_call_id,
                canonical(payload),
                "Conversation handed to the team",
                payload_hash(payload),
                reason.value,
                stamp,
                stamp,
            ),
        )
        _set_conversation(conn, conversation_id, ConversationStatus.HANDED_OFF, now)
        audit(
            conn,
            Actor.SYSTEM,
            "handoff_created",
            conversation_id=conversation_id,
            action_id=action_id,
            reason=reason.value,
            origin=origin.value,
        )
    return action_id


# --- customer confirmation --------------------------------------------------------------------


def confirm(
    conn: sqlite3.Connection,
    rules: Rules,
    action_id: str,
    conversation_id: str,
    presented_hash: str,
    now: datetime,
) -> ConfirmResult:
    """The customer pressed Confirm. Refusals change nothing (except marking an expired card)."""
    with transaction(conn):
        row = _action(conn, action_id)
        if row is None or row["conversation_id"] != conversation_id:
            return ConfirmResult("NOT_FOUND", action_id)
        if row["status"] != S.PROPOSED:
            return ConfirmResult("NOT_PROPOSED", action_id)
        if row["expires_at"] and parse_utc(row["expires_at"]) <= now:
            set_action_status(conn, action_id, S.PROPOSED, S.EXPIRED, now)
            settle_conversation(conn, conversation_id, now)
            return ConfirmResult("EXPIRED", action_id)
        if not hmac.compare_digest(row["payload_hash"], presented_hash):
            return ConfirmResult("HASH_MISMATCH", action_id)

        set_action_status(
            conn,
            action_id,
            S.PROPOSED,
            S.CUSTOMER_CONFIRMED,
            now,
            customer_confirmed_at=to_utc_text(now),
        )
        audit(
            conn,
            Actor.CUSTOMER,
            "action_confirmed",
            conversation_id=conversation_id,
            action_id=action_id,
        )
        result = _route(conn, rules, row, now)
        settle_conversation(conn, conversation_id, now)
    return result


def _visit_start(conn: sqlite3.Connection, booking_id: str) -> datetime:
    row = conn.execute(
        "SELECT s.starts_at FROM bookings b JOIN slots s ON s.id = b.slot_id WHERE b.id = ?",
        (booking_id,),
    ).fetchone()
    return parse_utc(row["starts_at"])


def _to_staff(
    conn: sqlite3.Connection,
    rules: Rules,
    row: sqlite3.Row,
    current: ActionStatus,
    reason: EscalationReason,
    now: datetime,
) -> ConfirmResult:
    set_action_status(
        conn, row["id"], current, S.PENDING_STAFF, now, escalation_reason=reason.value
    )
    customer = conn.execute(
        "SELECT customer_id FROM conversations WHERE id = ?", (row["conversation_id"],)
    ).fetchone()["customer_id"]
    draft(
        conn,
        rules.clock,
        OutboxKind.REQUEST_RECEIVED,
        now,
        booking_id=row["booking_id"],
        customer_id=customer,
        request=REQUEST_LABELS[ActionType(row["action_type"])],
    )
    return ConfirmResult(S.PENDING_STAFF.value, row["id"])


def _route(
    conn: sqlite3.Connection, rules: Rules, row: sqlite3.Row, now: datetime
) -> ConfirmResult:
    kind = ActionType(row["action_type"])
    payload = json.loads(row["payload"])
    confirmed = S.CUSTOMER_CONFIRMED
    if kind is ActionType.HOLD_CREATE:
        return _attempt(conn, row, confirmed, now, lambda: _execute_hold(conn, rules, row, now))
    if kind is ActionType.REFUND_REQUEST:
        return _to_staff(conn, rules, row, confirmed, EscalationReason.REFUND, now)
    if kind is ActionType.POLICY_EXCEPTION:
        return _to_staff(conn, rules, row, confirmed, EscalationReason.POLICY_EXCEPTION, now)
    booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (row["booking_id"],)).fetchone()
    late = is_late(now, _visit_start(conn, booking["id"]), rules.free_cancel_hours)
    if kind is ActionType.RESCHEDULE:
        if late:
            return _to_staff(conn, rules, row, confirmed, EscalationReason.LATE_RESCHEDULE, now)
        return _attempt(
            conn,
            row,
            confirmed,
            now,
            lambda: _execute_reschedule(conn, rules, booking, payload, now),
        )
    if kind is ActionType.CANCELLATION:
        if late and booking["status"] != BookingStatus.HELD:
            return _to_staff(conn, rules, row, confirmed, EscalationReason.LATE_CANCELLATION, now)
        return _attempt(
            conn, row, confirmed, now, lambda: _execute_cancel(conn, rules, booking, now)
        )
    raise ValueError(f"no routing for {kind}")


def _attempt(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    current: ActionStatus,
    now: datetime,
    execute: Any,
) -> ConfirmResult:
    """Run an execution inside a savepoint: all of it happens, or none of it and `failed`."""
    conn.execute("SAVEPOINT execute_action")
    try:
        result: ConfirmResult = execute()
    except SlotTaken:
        conn.execute("ROLLBACK TO execute_action")
        conn.execute("RELEASE execute_action")
        set_action_status(conn, row["id"], current, S.FAILED, now, failure_detail="slot_taken")
        audit(
            conn,
            Actor.SYSTEM,
            "action_failed",
            conversation_id=row["conversation_id"],
            action_id=row["id"],
            failure="slot_taken",
        )
        return ConfirmResult(S.FAILED.value, row["id"])
    conn.execute("RELEASE execute_action")
    return result


def _service(conn: sqlite3.Connection, code: str) -> sqlite3.Row:
    row: sqlite3.Row = conn.execute("SELECT * FROM services WHERE code = ?", (code,)).fetchone()
    return row


def _execute_hold(
    conn: sqlite3.Connection, rules: Rules, row: sqlite3.Row, now: datetime
) -> ConfirmResult:
    payload = json.loads(row["payload"])
    customer = conn.execute(
        "SELECT customer_id FROM conversations WHERE id = ?", (row["conversation_id"],)
    ).fetchone()["customer_id"]
    if customer is None or customer != payload["customer_id"]:
        raise TransitionError("the hold's customer is not this conversation's customer")
    for slot_id in payload["slot_ids"]:
        _move_slot(conn, slot_id, SlotStatus.HELD)
    booking_id = str(uuid.uuid4())
    reference = new_reference(conn)
    expires = rules.clock.add_office_hours(now, rules.hold_ttl_business_hours)
    stamp = to_utc_text(now)
    conn.execute(
        "INSERT INTO bookings (id, reference, customer_id, service_code, slot_id, quote_id,"
        " status, reschedule_count, hold_expires_at, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 'held', 0, ?, ?, ?)",
        (
            booking_id,
            reference,
            customer,
            payload["service_code"],
            payload["slot_ids"][0],
            payload["quote_id"],
            to_utc_text(expires),
            stamp,
            stamp,
        ),
    )
    set_action_status(
        conn, row["id"], S.CUSTOMER_CONFIRMED, S.PENDING_STAFF, now, booking_id=booking_id
    )
    draft(
        conn,
        rules.clock,
        OutboxKind.HOLD_RECEIVED,
        now,
        booking_id=booking_id,
        typical_hours=str(rules.staff_confirm_typical_business_hours),
    )
    audit(
        conn,
        Actor.SYSTEM,
        "hold_created",
        conversation_id=row["conversation_id"],
        action_id=row["id"],
        booking_id=booking_id,
    )
    return ConfirmResult(S.PENDING_STAFF.value, row["id"], reference)


def _execute_reschedule(
    conn: sqlite3.Connection,
    rules: Rules,
    booking: sqlite3.Row,
    payload: dict[str, Any],
    now: datetime,
) -> ConfirmResult:
    windows = _service(conn, booking["service_code"])["windows_needed"]
    old = visit_slot_ids(conn, booking["slot_id"], windows)
    target = SlotStatus.HELD if booking["status"] == BookingStatus.HELD else SlotStatus.BOOKED
    for slot_id in payload["new_slot_ids"]:
        _move_slot(conn, slot_id, target)
    for slot_id in old:
        _move_slot(conn, slot_id, SlotStatus.OPEN)
    conn.execute(
        "UPDATE bookings SET slot_id = ?, quote_id = ?, reschedule_count = reschedule_count + 1,"
        " updated_at = ? WHERE id = ?",
        (payload["new_slot_ids"][0], payload["quote_id"], to_utc_text(now), booking["id"]),
    )
    return _executed(conn, rules, booking["id"], OutboxKind.BOOKING_RESCHEDULED, now)


def _execute_cancel(
    conn: sqlite3.Connection, rules: Rules, booking: sqlite3.Row, now: datetime
) -> ConfirmResult:
    windows = _service(conn, booking["service_code"])["windows_needed"]
    for slot_id in visit_slot_ids(conn, booking["slot_id"], windows):
        _move_slot(conn, slot_id, SlotStatus.OPEN)
    _move_booking(conn, booking["id"], BookingStatus.CANCELLED, now)
    return _executed(conn, rules, booking["id"], OutboxKind.BOOKING_CANCELLED, now)


def _executed(
    conn: sqlite3.Connection, rules: Rules, booking_id: str, kind: OutboxKind, now: datetime
) -> ConfirmResult:
    """Finish the action that is executing for this booking and draft the notification."""
    row = conn.execute(
        "SELECT id, status, conversation_id FROM actions WHERE booking_id = ?"
        " AND status IN ('customer_confirmed', 'approved')",
        (booking_id,),
    ).fetchone()
    set_action_status(
        conn, row["id"], ActionStatus(row["status"]), S.EXECUTED, now, executed_at=to_utc_text(now)
    )
    reference = conn.execute(
        "SELECT reference FROM bookings WHERE id = ?", (booking_id,)
    ).fetchone()["reference"]
    draft(conn, rules.clock, kind, now, booking_id=booking_id)
    audit(
        conn,
        Actor.SYSTEM,
        "action_executed",
        conversation_id=row["conversation_id"],
        action_id=row["id"],
        booking_id=booking_id,
    )
    return ConfirmResult(S.EXECUTED.value, row["id"], reference)


# --- staff ------------------------------------------------------------------------------------


def decide(
    conn: sqlite3.Connection,
    rules: Rules,
    action_id: str,
    decision: StaffDecision,
    reason: str | None,
    now: datetime,
    review_seconds: int | None = None,
) -> ConfirmResult:
    """Staff approve or reject a pending action. Approving a refund only records the decision."""
    if decision not in {StaffDecision.APPROVE, StaffDecision.REJECT}:
        raise DecisionError("use take_over or close_conversation for those decisions")
    if decision is StaffDecision.REJECT and not (reason and reason.strip()):
        raise DecisionError("a rejection needs a reason")
    with transaction(conn):
        row = _action(conn, action_id)
        if row is None or row["status"] != S.PENDING_STAFF or row["action_type"] == "handoff":
            raise DecisionError("only a pending action that is not a handoff can be decided")
        conn.execute(
            "INSERT INTO staff_decisions (id, action_id, conversation_id, decision, reason,"
            " decided_at, review_seconds) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                action_id,
                row["conversation_id"],
                decision.value,
                reason,
                to_utc_text(now),
                review_seconds,
            ),
        )
        audit(
            conn,
            Actor.STAFF,
            "staff_decided",
            conversation_id=row["conversation_id"],
            action_id=action_id,
            decision=decision.value,
        )
        kind = ActionType(row["action_type"])
        if decision is StaffDecision.REJECT:
            set_action_status(conn, action_id, S.PENDING_STAFF, S.REJECTED, now)
            if kind is ActionType.HOLD_CREATE:
                release_hold(
                    conn,
                    rules,
                    row["booking_id"],
                    BookingStatus.CANCELLED,
                    OutboxKind.HOLD_DECLINED,
                    now,
                    reason=reason or "",
                )
            return ConfirmResult(S.REJECTED.value, action_id)

        set_action_status(conn, action_id, S.PENDING_STAFF, S.APPROVED, now)
        if kind in {ActionType.REFUND_REQUEST, ActionType.POLICY_EXCEPTION}:
            set_action_status(
                conn, action_id, S.APPROVED, S.EXECUTED, now, executed_at=to_utc_text(now)
            )
            return ConfirmResult(S.EXECUTED.value, action_id)
        booking = conn.execute(
            "SELECT * FROM bookings WHERE id = ?", (row["booking_id"],)
        ).fetchone()
        if kind is ActionType.HOLD_CREATE:
            return _attempt(
                conn, row, S.APPROVED, now, lambda: _confirm_hold(conn, rules, booking, now)
            )
        if kind is ActionType.RESCHEDULE:
            payload = json.loads(row["payload"])
            return _attempt(
                conn,
                row,
                S.APPROVED,
                now,
                lambda: _execute_reschedule(conn, rules, booking, payload, now),
            )
        return _attempt(
            conn, row, S.APPROVED, now, lambda: _execute_cancel(conn, rules, booking, now)
        )


def _confirm_hold(
    conn: sqlite3.Connection, rules: Rules, booking: sqlite3.Row, now: datetime
) -> ConfirmResult:
    if booking["status"] != BookingStatus.HELD:
        raise SlotTaken(booking["slot_id"])
    windows = _service(conn, booking["service_code"])["windows_needed"]
    for slot_id in visit_slot_ids(conn, booking["slot_id"], windows):
        _move_slot(conn, slot_id, SlotStatus.BOOKED)
    _move_booking(conn, booking["id"], BookingStatus.CONFIRMED, now)
    conn.execute("UPDATE bookings SET hold_expires_at = NULL WHERE id = ?", (booking["id"],))
    return _executed(conn, rules, booking["id"], OutboxKind.BOOKING_CONFIRMED, now)


def release_hold(
    conn: sqlite3.Connection,
    rules: Rules,
    booking_id: str,
    target: BookingStatus,
    kind: OutboxKind,
    now: datetime,
    reason: str = "",
) -> None:
    booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    windows = _service(conn, booking["service_code"])["windows_needed"]
    # Drafted first: the template names the arrival window the hold had.
    extra = {"reason": reason} if kind is OutboxKind.HOLD_DECLINED else {}
    draft(conn, rules.clock, kind, now, booking_id=booking_id, **extra)
    for slot_id in visit_slot_ids(conn, booking["slot_id"], windows):
        _move_slot(conn, slot_id, SlotStatus.OPEN)
    _move_booking(conn, booking_id, target, now)


def take_over(conn: sqlite3.Connection, conversation_id: str, now: datetime) -> None:
    _staff_control(conn, conversation_id, StaffDecision.TAKE_OVER, now)


def close_conversation(conn: sqlite3.Connection, conversation_id: str, now: datetime) -> None:
    _staff_control(conn, conversation_id, StaffDecision.CLOSE, now)


def _staff_control(
    conn: sqlite3.Connection, conversation_id: str, decision: StaffDecision, now: datetime
) -> None:
    with transaction(conn):
        _withdraw_open(conn, conversation_id, now)
        for row in conn.execute(
            "SELECT id FROM actions WHERE conversation_id = ? AND action_type = 'handoff'"
            " AND status = 'pending_staff'",
            (conversation_id,),
        ).fetchall():
            set_action_status(
                conn, row["id"], S.PENDING_STAFF, S.EXECUTED, now, executed_at=to_utc_text(now)
            )
        conn.execute(
            "INSERT INTO staff_decisions (id, conversation_id, decision, decided_at)"
            " VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), conversation_id, decision.value, to_utc_text(now)),
        )
        status = (
            ConversationStatus.CLOSED
            if decision is StaffDecision.CLOSE
            else ConversationStatus.HANDED_OFF
        )
        _set_conversation(conn, conversation_id, status, now)
        audit(
            conn,
            Actor.STAFF,
            "staff_decided",
            conversation_id=conversation_id,
            decision=decision.value,
        )
