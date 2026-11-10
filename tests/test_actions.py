"""The action state machine, booking and slot changes, and execution (schema section 4)."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from citebook.actions import (
    ACTION_TRANSITIONS,
    BOOKING_TRANSITIONS,
    SLOT_TRANSITIONS,
    ConfirmResult,
    DecisionError,
    Rules,
    TransitionError,
    check_transition,
    close_conversation,
    confirm,
    create_handoff,
    decide,
    payload_hash,
    propose,
    withdraw,
)
from citebook.db import connect
from citebook.models import (
    ActionOrigin,
    ActionStatus,
    ActionType,
    BookingStatus,
    EscalationReason,
    SlotStatus,
    StaffDecision,
)
from citebook.timeutil import parse_utc, to_utc_text
from tests.conftest import (
    add_quote,
    add_tool_call,
    booking,
    local,
    make_conversation,
    new_customer,
    open_slots,
    seeded,
    slot_status,
)

NOW = local("2026-11-09T08:00")  # Monday, base_week_1
LATER = NOW + timedelta(minutes=5)


def action(conn: sqlite3.Connection, action_id: str) -> sqlite3.Row:
    row: sqlite3.Row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    return row


def conv_status(conn: sqlite3.Connection, conversation_id: str) -> str:
    status: str = conn.execute(
        "SELECT status FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()[0]
    return status


def outbox_kinds(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT kind FROM outbox ORDER BY created_at, rowid")]


def propose_hold(
    conn: sqlite3.Connection,
    service: str = "FAUCET_REPLACEMENT",
    skill: str = "fixture_installation",
    windows: int = 1,
    customer_id: str | None = None,
    after: str = "2026-11-09T16:00:00Z",
) -> tuple[str, str, dict[str, Any], list[sqlite3.Row]]:
    customer_id = customer_id or new_customer(conn)
    conversation = make_conversation(conn, customer_id=customer_id)
    slots = open_slots(conn, skill, after=after, count=windows)
    quote_id = add_quote(conn, conversation, service, slots[0]["id"])
    payload = {
        "service_code": service,
        "slot_ids": [s["id"] for s in slots],
        "quote_id": quote_id,
        "customer_id": customer_id,
    }
    action_id = propose(
        conn,
        conversation_id=conversation,
        tool_call_id=add_tool_call(conn, conversation, "propose_hold"),
        action_type=ActionType.HOLD_CREATE,
        payload=payload,
        summary="Faucet replacement",
        expires_at=NOW + timedelta(minutes=30),
        now=NOW,
    )
    return conversation, action_id, payload, slots


# --- the tables themselves --------------------------------------------------------------------


def test_every_documented_action_transition_is_allowed_and_nothing_else() -> None:
    S = ActionStatus
    documented = {
        (None, S.PROPOSED),
        (None, S.PENDING_STAFF),
        (S.PROPOSED, S.CUSTOMER_CONFIRMED),
        (S.PROPOSED, S.WITHDRAWN),
        (S.PROPOSED, S.EXPIRED),
        (S.CUSTOMER_CONFIRMED, S.EXECUTED),
        (S.CUSTOMER_CONFIRMED, S.PENDING_STAFF),
        (S.CUSTOMER_CONFIRMED, S.FAILED),
        (S.PENDING_STAFF, S.APPROVED),
        (S.PENDING_STAFF, S.REJECTED),
        (S.PENDING_STAFF, S.EXPIRED),
        (S.PENDING_STAFF, S.EXECUTED),
        (S.APPROVED, S.EXECUTED),
        (S.APPROVED, S.FAILED),
    }
    allowed = {(a, b) for a, targets in ACTION_TRANSITIONS.items() for b in targets}
    assert allowed == documented
    for current in [None, *S]:
        for target in S:
            if (current, target) not in documented:
                with pytest.raises(TransitionError):
                    check_transition(ACTION_TRANSITIONS, current, target)


def test_final_states_have_no_way_out() -> None:
    for final in [
        ActionStatus.EXECUTED,
        ActionStatus.REJECTED,
        ActionStatus.WITHDRAWN,
        ActionStatus.EXPIRED,
        ActionStatus.FAILED,
    ]:
        assert final not in ACTION_TRANSITIONS
    for done in [BookingStatus.CANCELLED, BookingStatus.EXPIRED, BookingStatus.COMPLETED]:
        assert done not in BOOKING_TRANSITIONS
    assert SLOT_TRANSITIONS[SlotStatus.BLOCKED] == set()


# --- holds ------------------------------------------------------------------------------------


def test_a_proposal_waits_for_the_button(db: sqlite3.Connection) -> None:
    conversation, action_id, payload, slots = propose_hold(db)
    assert action(db, action_id)["status"] == "proposed"
    assert action(db, action_id)["payload_hash"] == payload_hash(payload)
    assert conv_status(db, conversation) == "awaiting_confirmation"
    assert slot_status(db, slots[0]["id"]) == "open"
    assert db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 8  # only the seed


def test_confirming_a_hold_holds_the_slot_and_queues_it_for_staff(
    db: sqlite3.Connection, rules: Rules
) -> None:
    conversation, action_id, payload, slots = propose_hold(db)
    result = confirm(db, rules, action_id, conversation, payload_hash(payload), LATER)
    assert result.outcome == "pending_staff"
    assert result.booking_reference and result.booking_reference.startswith("CB-")
    held = booking(db, result.booking_reference)
    assert held["status"] == "held"
    assert slot_status(db, slots[0]["id"]) == "held"
    # 8:05 on Monday + 4 office hours
    assert held["hold_expires_at"] == to_utc_text(local("2026-11-09T12:05"))
    assert action(db, action_id)["booking_id"] == held["id"]
    assert outbox_kinds(db) == ["hold_received"]
    assert conv_status(db, conversation) == "active"


def test_a_two_window_service_holds_both_windows(db: sqlite3.Connection, rules: Rules) -> None:
    conversation, action_id, payload, slots = propose_hold(
        db, service="HOME_INSPECTION", skill="inspection", windows=2
    )
    confirm(db, rules, action_id, conversation, payload_hash(payload), LATER)
    assert [slot_status(db, s["id"]) for s in slots] == ["held", "held"]


@pytest.mark.parametrize(
    ("change", "outcome"),
    [("hash", "HASH_MISMATCH"), ("conversation", "NOT_FOUND"), ("time", "EXPIRED")],
)
def test_refused_confirmations_change_nothing(
    db: sqlite3.Connection, rules: Rules, change: str, outcome: str
) -> None:
    conversation, action_id, payload, slots = propose_hold(db)
    presented, conv, when = payload_hash(payload), conversation, LATER
    if change == "hash":
        presented = payload_hash({**payload, "quote_id": "other"})
    elif change == "conversation":
        conv = make_conversation(db)
    else:
        when = NOW + timedelta(minutes=31)
    assert confirm(db, rules, action_id, conv, presented, when).outcome == outcome
    assert slot_status(db, slots[0]["id"]) == "open"
    assert db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 8
    expected = "expired" if change == "time" else "proposed"
    assert action(db, action_id)["status"] == expected


def test_a_card_confirms_only_once(db: sqlite3.Connection, rules: Rules) -> None:
    conversation, action_id, payload, _ = propose_hold(db)
    confirm(db, rules, action_id, conversation, payload_hash(payload), LATER)
    again = confirm(db, rules, action_id, conversation, payload_hash(payload), LATER)
    assert again.outcome == "NOT_PROPOSED"
    assert db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 9


def test_a_new_proposal_withdraws_the_open_one(db: sqlite3.Connection) -> None:
    customer = new_customer(db)
    conversation = make_conversation(db, customer_id=customer)
    first_slot, second_slot = open_slots(db, "fixture_installation", count=1)[0], None
    ids = []
    for slot in [
        first_slot,
        open_slots(db, "fixture_installation", after=first_slot["starts_at"])[0],
    ]:
        second_slot = slot
        payload = {
            "service_code": "FAUCET_REPLACEMENT",
            "slot_ids": [slot["id"]],
            "quote_id": add_quote(db, conversation, "FAUCET_REPLACEMENT", slot["id"]),
            "customer_id": customer,
        }
        ids.append(
            propose(
                db,
                conversation_id=conversation,
                tool_call_id=add_tool_call(db, conversation, "propose_hold"),
                action_type=ActionType.HOLD_CREATE,
                payload=payload,
                summary="s",
                expires_at=NOW + timedelta(minutes=30),
                now=NOW,
            )
        )
    assert second_slot is not None
    assert [action(db, i)["status"] for i in ids] == ["withdrawn", "proposed"]


def test_cancel_on_the_card_withdraws_it(db: sqlite3.Connection) -> None:
    conversation, action_id, _, _ = propose_hold(db)
    assert withdraw(db, action_id, conversation, LATER) is True
    assert action(db, action_id)["status"] == "withdrawn"
    assert conv_status(db, conversation) == "active"
    assert withdraw(db, action_id, conversation, LATER) is False


def test_a_hold_fails_cleanly_when_the_slot_was_taken(db: sqlite3.Connection, rules: Rules) -> None:
    conversation, action_id, payload, slots = propose_hold(db)
    db.execute(
        "UPDATE slots SET status = 'blocked', version = version + 1 WHERE id = ?", (slots[0]["id"],)
    )
    result = confirm(db, rules, action_id, conversation, payload_hash(payload), LATER)
    assert result.outcome == "failed"
    assert action(db, action_id)["failure_detail"] == "slot_taken"
    assert db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 8
    assert outbox_kinds(db) == []


def test_a_hold_must_belong_to_the_conversations_customer(
    db: sqlite3.Connection, rules: Rules
) -> None:
    conversation, action_id, payload, _ = propose_hold(db)
    db.execute(
        "UPDATE conversations SET customer_id = ? WHERE id = ?", (new_customer(db), conversation)
    )
    with pytest.raises(TransitionError):
        confirm(db, rules, action_id, conversation, payload_hash(payload), LATER)


def _held(db: sqlite3.Connection, rules: Rules) -> tuple[str, ConfirmResult, list[sqlite3.Row]]:
    conversation, action_id, payload, slots = propose_hold(db)
    return (
        action_id,
        confirm(db, rules, action_id, conversation, payload_hash(payload), LATER),
        slots,
    )


def test_staff_approval_confirms_the_hold(db: sqlite3.Connection, rules: Rules) -> None:
    action_id, held, slots = _held(db, rules)
    result = decide(db, rules, action_id, StaffDecision.APPROVE, None, LATER)
    assert result.outcome == "executed"
    assert booking(db, held.booking_reference or "")["status"] == "confirmed"
    assert booking(db, held.booking_reference or "")["hold_expires_at"] is None
    assert slot_status(db, slots[0]["id"]) == "booked"
    assert outbox_kinds(db) == ["hold_received", "booking_confirmed"]


def test_staff_rejection_needs_a_reason_and_frees_the_slot(
    db: sqlite3.Connection, rules: Rules
) -> None:
    action_id, held, slots = _held(db, rules)
    with pytest.raises(DecisionError):
        decide(db, rules, action_id, StaffDecision.REJECT, "  ", LATER)
    decide(db, rules, action_id, StaffDecision.REJECT, "Technician unavailable", LATER)
    assert action(db, action_id)["status"] == "rejected"
    assert booking(db, held.booking_reference or "")["status"] == "cancelled"
    assert slot_status(db, slots[0]["id"]) == "open"
    assert outbox_kinds(db)[-1] == "hold_declined"


def test_only_pending_actions_can_be_decided(db: sqlite3.Connection, rules: Rules) -> None:
    _, action_id, _, _ = propose_hold(db)
    with pytest.raises(DecisionError):
        decide(db, rules, action_id, StaffDecision.APPROVE, None, LATER)


# --- existing bookings: reschedule and cancel -------------------------------------------------


def _propose_on_booking(
    db: sqlite3.Connection, reference: str, kind: ActionType, payload: dict[str, Any], now: Any
) -> tuple[str, str]:
    row = booking(db, reference)
    conversation = make_conversation(db, verified_booking_id=row["id"])
    action_id = propose(
        db,
        conversation_id=conversation,
        tool_call_id=add_tool_call(db, conversation, "x"),
        action_type=kind,
        payload=payload,
        summary="s",
        expires_at=now + timedelta(minutes=30),
        now=now,
        booking_id=row["id"],
    )
    return conversation, action_id


def _reschedule(db: sqlite3.Connection, now: Any) -> tuple[str, str, dict[str, Any], sqlite3.Row]:
    old = booking(db, "CB-7KQ4MX")  # drain cleaning, Tuesday 17 November 10:00
    new_slot = open_slots(db, "drain_cleaning", after="2026-11-18T16:00:00Z")[0]
    payload = {
        "booking_id": old["id"],
        "new_slot_ids": [new_slot["id"]],
        "quote_id": add_quote(db, None, "DRAIN_CLEANING", new_slot["id"]),
    }
    conversation, action_id = _propose_on_booking(
        db, "CB-7KQ4MX", ActionType.RESCHEDULE, payload, now
    )
    return conversation, action_id, payload, new_slot


def test_a_reschedule_with_notice_executes(db: sqlite3.Connection, rules: Rules) -> None:
    old = booking(db, "CB-7KQ4MX")
    conversation, action_id, payload, new_slot = _reschedule(db, NOW)
    result = confirm(db, rules, action_id, conversation, payload_hash(payload), LATER)
    assert result.outcome == "executed"
    moved = booking(db, "CB-7KQ4MX")
    assert moved["slot_id"] == new_slot["id"] and moved["quote_id"] == payload["quote_id"]
    assert moved["reschedule_count"] == old["reschedule_count"] + 1
    assert slot_status(db, old["slot_id"]) == "open"
    assert slot_status(db, new_slot["id"]) == "booked"
    assert outbox_kinds(db) == ["booking_rescheduled"]


def test_a_late_reschedule_goes_to_staff_then_executes_on_approval(
    db: sqlite3.Connection, rules: Rules
) -> None:
    late = local("2026-11-16T12:00")  # 22 hours before the visit
    conversation, action_id, payload, new_slot = _reschedule(db, late)
    result = confirm(db, rules, action_id, conversation, payload_hash(payload), late)
    assert result.outcome == "pending_staff"
    assert action(db, action_id)["escalation_reason"] == EscalationReason.LATE_RESCHEDULE
    assert booking(db, "CB-7KQ4MX")["slot_id"] != new_slot["id"]
    decide(db, rules, action_id, StaffDecision.APPROVE, None, late)
    assert booking(db, "CB-7KQ4MX")["slot_id"] == new_slot["id"]


def test_a_cancellation_with_notice_executes(db: sqlite3.Connection, rules: Rules) -> None:
    row = booking(db, "CB-8YTF5G")  # home inspection: two windows
    conversation, action_id = _propose_on_booking(
        db, "CB-8YTF5G", ActionType.CANCELLATION, {"booking_id": row["id"]}, NOW
    )
    result = confirm(
        db, rules, action_id, conversation, payload_hash({"booking_id": row["id"]}), LATER
    )
    assert result.outcome == "executed"
    assert booking(db, "CB-8YTF5G")["status"] == "cancelled"
    statuses = db.execute(
        "SELECT status FROM slots WHERE technician_id = (SELECT technician_id FROM slots"
        " WHERE id = ?) AND starts_at >= ? AND starts_at < ?",
        (row["slot_id"], "2026-11-24T16:00:00Z", "2026-11-24T20:00:00Z"),
    ).fetchall()
    assert [s[0] for s in statuses] == ["open", "open"]


def test_a_late_cancellation_goes_to_staff_and_changes_nothing_yet(
    db: sqlite3.Connection, rules: Rules
) -> None:
    late = local("2026-11-16T12:00")
    row = booking(db, "CB-7KQ4MX")
    conversation, action_id = _propose_on_booking(
        db, "CB-7KQ4MX", ActionType.CANCELLATION, {"booking_id": row["id"]}, late
    )
    result = confirm(
        db, rules, action_id, conversation, payload_hash({"booking_id": row["id"]}), late
    )
    assert result.outcome == "pending_staff"
    assert action(db, action_id)["escalation_reason"] == EscalationReason.LATE_CANCELLATION
    assert booking(db, "CB-7KQ4MX")["status"] == "confirmed"
    assert outbox_kinds(db) == ["request_received"]


def test_cancelling_a_held_booking_is_always_free(db: sqlite3.Connection, rules: Rules) -> None:
    _, held, slots = _held(db, rules)
    row = booking(db, held.booking_reference or "")
    visit = parse_utc(
        db.execute("SELECT starts_at FROM slots WHERE id = ?", (row["slot_id"],)).fetchone()[0]
    )
    late = visit - timedelta(hours=2)
    conversation, action_id = _propose_on_booking(
        db, row["reference"], ActionType.CANCELLATION, {"booking_id": row["id"]}, late
    )
    result = confirm(
        db, rules, action_id, conversation, payload_hash({"booking_id": row["id"]}), late
    )
    assert result.outcome == "executed"
    assert slot_status(db, slots[0]["id"]) == "open"


def test_a_refund_is_only_ever_recorded(db: sqlite3.Connection, rules: Rules) -> None:
    row = booking(db, "CB-4MVX7A")
    payload = {"booking_id": row["id"], "reason_text": "Not happy"}
    conversation, action_id = _propose_on_booking(
        db, "CB-4MVX7A", ActionType.REFUND_REQUEST, payload, NOW
    )
    assert (
        confirm(db, rules, action_id, conversation, payload_hash(payload), LATER).outcome
        == "pending_staff"
    )
    assert action(db, action_id)["escalation_reason"] == "REFUND"
    assert decide(db, rules, action_id, StaffDecision.APPROVE, None, LATER).outcome == "executed"
    after = booking(db, "CB-4MVX7A")
    assert after["status"] == row["status"] and after["slot_id"] == row["slot_id"]


# --- handoffs ---------------------------------------------------------------------------------


def test_a_handoff_withdraws_open_cards_and_closes_with_staff(db: sqlite3.Connection) -> None:
    conversation, card, _, _ = propose_hold(db)
    handoff = create_handoff(
        db,
        conversation_id=conversation,
        origin=ActionOrigin.SCREENING,
        reason=EscalationReason.EMERGENCY,
        now=LATER,
    )
    assert action(db, card)["status"] == "withdrawn"
    assert action(db, handoff)["status"] == "pending_staff"
    assert action(db, handoff)["tool_call_id"] is None
    assert conv_status(db, conversation) == "handed_off"
    close_conversation(db, conversation, LATER)
    assert action(db, handoff)["status"] == "executed"
    assert conv_status(db, conversation) == "closed"


def test_only_a_tool_handoff_has_a_tool_call(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db)
    with pytest.raises(ValueError):
        create_handoff(
            db,
            conversation_id=conversation,
            origin=ActionOrigin.LOOP,
            reason=EscalationReason.MODEL_UNAVAILABLE,
            now=NOW,
            tool_call_id="x",
        )


# --- double booking ---------------------------------------------------------------------------


def test_two_customers_confirming_the_same_slot_at_once_get_exactly_one_hold(
    tmp_path: Path, rules: Rules
) -> None:
    path = str(tmp_path / "race.db")
    setup = seeded(path)
    slot = open_slots(setup, "fixture_installation", after="2026-11-09T16:00:00Z")[0]
    cards = []
    for _ in range(2):
        customer = new_customer(setup)
        conversation = make_conversation(setup, customer_id=customer)
        payload = {
            "service_code": "FAUCET_REPLACEMENT",
            "slot_ids": [slot["id"]],
            "quote_id": add_quote(setup, conversation, "FAUCET_REPLACEMENT", slot["id"]),
            "customer_id": customer,
        }
        action_id = propose(
            setup,
            conversation_id=conversation,
            tool_call_id=add_tool_call(setup, conversation, "propose_hold"),
            action_type=ActionType.HOLD_CREATE,
            payload=payload,
            summary="s",
            expires_at=NOW + timedelta(minutes=30),
            now=NOW,
        )
        cards.append((conversation, action_id, payload_hash(payload)))
    setup.close()

    barrier = threading.Barrier(2)
    results: list[str] = []

    def press(conversation: str, action_id: str, hash_: str) -> None:
        conn = connect(path)
        barrier.wait()
        results.append(confirm(conn, rules, action_id, conversation, hash_, LATER).outcome)
        conn.close()

    threads = [threading.Thread(target=press, args=card) for card in cards]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == ["failed", "pending_staff"]
    check = connect(path)
    assert (
        check.execute("SELECT status FROM slots WHERE id = ?", (slot["id"],)).fetchone()[0]
        == "held"
    )
    assert (
        check.execute("SELECT COUNT(*) FROM bookings WHERE slot_id = ?", (slot["id"],)).fetchone()[
            0
        ]
        == 1
    )
    events = [json.loads(r[0]) for r in check.execute("SELECT payload FROM audit_events")]
    assert any(e.get("failure") == "slot_taken" for e in events)
    check.close()
