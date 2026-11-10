"""Phase 3 acceptance: the booking, reschedule, cancellation and refund flows run end to end
through the tools and the gate, with no model.

Each flow is what the turn loop will do: tool calls go through the runner and the gate, the
customer's Confirm is the card's button (actions.confirm with the payload hash shown on the card),
and staff decisions come from the console (actions.decide). Typed words never confirm anything:
no tool can confirm, so a scripted "confirm" can only call tools, and those only create cards.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from citebook.actions import Rules, confirm, decide, payload_hash
from citebook.models import StaffDecision
from citebook.tools.registry import REGISTRY
from tests.conftest import booking, local, make_conversation, slot_status
from tests.tooling import CONTACT, NOW, SAM, call, decision, first_slot, ok, quote_for

LATE = local("2026-11-16T12:00")  # Sam's visit is Tuesday 17 November at 10:00 AM


def press_confirm(
    conn: sqlite3.Connection, rules: Rules, conversation: str, action_id: str, now: datetime
) -> str:
    """The widget's Confirm button: it sends the hash the card was rendered with."""
    card = conn.execute("SELECT payload_hash FROM actions WHERE id = ?", (action_id,)).fetchone()
    result = confirm(conn, rules, action_id, conversation, card["payload_hash"], now)
    return result.outcome


def action_row(conn: sqlite3.Connection, action_id: str) -> sqlite3.Row:
    row: sqlite3.Row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    return row


def outbox(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT kind FROM outbox ORDER BY rowid")]


def verify_sam(conn: sqlite3.Connection, now: datetime = NOW) -> str:
    conversation = make_conversation(conn, now=now)
    envelope = call(conn, conversation, "find_booking", now=now)
    assert decision(envelope) == "NEEDS_VERIFICATION"
    assert ok(call(conn, conversation, "verify_customer", SAM, now))["verified"] is True
    assert ok(call(conn, conversation, "find_booking", now=now))["booking_reference"] == "CB-7KQ4MX"
    return conversation


def test_new_booking_flow(db: sqlite3.Connection, rules: Rules) -> None:
    conversation = make_conversation(db)
    services = ok(call(db, conversation, "list_services"))
    assert "DRAIN_CLEANING" in {s["code"] for s in services["services"]}

    # Availability without a ZIP code is refused with a hint to ask for it.
    no_zip = call(
        db,
        conversation,
        "check_availability",
        {"service_code": "DRAIN_CLEANING", "date_from": "2026-11-12", "date_to": "2026-11-12"},
    )
    assert decision(no_zip) == "INVALID_ARGUMENTS" and "zip_code" in no_zip["say"]

    slot_id = first_slot(db, conversation, "DRAIN_CLEANING", "2026-11-12")
    quote = quote_for(db, conversation, slot_id, "DRAIN_CLEANING")
    hold = {"slot_id": slot_id, "quote_id": quote["quote_id"]}
    assert decision(call(db, conversation, "propose_hold", hold)) == "NEEDS_CONTACT"

    ok(call(db, conversation, "collect_contact", CONTACT))
    card = ok(call(db, conversation, "propose_hold", hold))
    assert slot_status(db, slot_id) == "open"  # nothing changes before the button

    assert press_confirm(
        db, rules, conversation, card["action_id"], NOW + timedelta(minutes=2)
    ) == ("pending_staff")
    action = action_row(db, card["action_id"])
    held = db.execute("SELECT * FROM bookings WHERE id = ?", (action["booking_id"],)).fetchone()
    assert held["status"] == "held" and slot_status(db, slot_id) == "held"
    assert held["quote_id"] == quote["quote_id"]

    decide(db, rules, card["action_id"], StaffDecision.APPROVE, None, NOW + timedelta(minutes=30))
    assert booking(db, held["reference"])["status"] == "confirmed"
    assert slot_status(db, slot_id) == "booked"
    assert action_row(db, card["action_id"])["status"] == "executed"
    assert outbox(db) == ["hold_received", "booking_confirmed"]


def test_reschedule_flow_within_policy(db: sqlite3.Connection, rules: Rules) -> None:
    conversation = verify_sam(db)
    old_slot = booking(db, "CB-7KQ4MX")["slot_id"]
    slot_id = first_slot(db, conversation, "DRAIN_CLEANING", "2026-11-19")
    quote = quote_for(db, conversation, slot_id, "DRAIN_CLEANING")
    args = {"booking_reference": "CB-7KQ4MX", "slot_id": slot_id, "quote_id": quote["quote_id"]}
    card = ok(call(db, conversation, "propose_reschedule", args))

    assert press_confirm(
        db, rules, conversation, card["action_id"], NOW + timedelta(minutes=1)
    ) == ("executed")
    moved = booking(db, "CB-7KQ4MX")
    assert (moved["slot_id"], moved["quote_id"], moved["reschedule_count"]) == (
        slot_id,
        quote["quote_id"],
        1,
    )
    assert slot_status(db, old_slot) == "open" and slot_status(db, slot_id) == "booked"
    assert outbox(db) == ["booking_rescheduled"]


def test_late_reschedule_goes_to_staff(db: sqlite3.Connection, rules: Rules) -> None:
    conversation = verify_sam(db, LATE)
    slot_id = first_slot(db, conversation, "DRAIN_CLEANING", "2026-11-19", now=LATE)
    quote = quote_for(db, conversation, slot_id, "DRAIN_CLEANING", now=LATE)
    args = {"booking_reference": "CB-7KQ4MX", "slot_id": slot_id, "quote_id": quote["quote_id"]}
    card = ok(call(db, conversation, "propose_reschedule", args, LATE))

    assert press_confirm(db, rules, conversation, card["action_id"], LATE) == "pending_staff"
    assert action_row(db, card["action_id"])["escalation_reason"] == "LATE_RESCHEDULE"
    assert booking(db, "CB-7KQ4MX")["reschedule_count"] == 0  # nothing moved yet

    decide(db, rules, card["action_id"], StaffDecision.APPROVE, None, LATE)
    assert booking(db, "CB-7KQ4MX")["slot_id"] == slot_id
    assert outbox(db) == ["request_received", "booking_rescheduled"]


def test_free_cancellation_flow(db: sqlite3.Connection, rules: Rules) -> None:
    conversation = verify_sam(db)
    slot_id = booking(db, "CB-7KQ4MX")["slot_id"]
    card = ok(call(db, conversation, "request_cancellation", {"booking_reference": "CB-7KQ4MX"}))
    assert booking(db, "CB-7KQ4MX")["status"] == "confirmed"
    assert press_confirm(db, rules, conversation, card["action_id"], NOW) == "executed"
    assert booking(db, "CB-7KQ4MX")["status"] == "cancelled"
    assert slot_status(db, slot_id) == "open"
    assert outbox(db) == ["booking_cancelled"]


def test_late_cancellation_goes_to_staff(db: sqlite3.Connection, rules: Rules) -> None:
    conversation = verify_sam(db, LATE)
    card = ok(
        call(
            db,
            conversation,
            "request_cancellation",
            {"booking_reference": "CB-7KQ4MX", "reason_text": "away"},
            LATE,
        )
    )
    assert press_confirm(db, rules, conversation, card["action_id"], LATE) == "pending_staff"
    assert action_row(db, card["action_id"])["escalation_reason"] == "LATE_CANCELLATION"
    assert booking(db, "CB-7KQ4MX")["status"] == "confirmed"
    decide(db, rules, card["action_id"], StaffDecision.REJECT, "Visit is tomorrow", LATE)
    assert booking(db, "CB-7KQ4MX")["status"] == "confirmed"


def test_a_held_booking_cancels_free_even_when_late(db: sqlite3.Connection, rules: Rules) -> None:
    conversation = make_conversation(db)
    ok(call(db, conversation, "collect_contact", CONTACT))
    slot_id = first_slot(db, conversation, "DRAIN_CLEANING", "2026-11-09")  # today
    quote = quote_for(db, conversation, slot_id, "DRAIN_CLEANING")
    card = ok(
        call(db, conversation, "propose_hold", {"slot_id": slot_id, "quote_id": quote["quote_id"]})
    )
    press_confirm(db, rules, conversation, card["action_id"], NOW)
    reference = db.execute(
        "SELECT reference FROM bookings WHERE id = ?",
        (action_row(db, card["action_id"])["booking_id"],),
    ).fetchone()[0]

    later = make_conversation(db)
    ok(call(db, later, "verify_customer", {"reference": reference, "phone_last4": "0150"}))
    cancel = ok(call(db, later, "request_cancellation", {"booking_reference": reference}))
    assert "No fee" in cancel["summary"]
    assert press_confirm(db, rules, later, cancel["action_id"], NOW) == "executed"
    assert booking(db, reference)["status"] == "cancelled"
    assert slot_status(db, slot_id) == "open"


def test_refund_flow_is_recorded_and_never_executed(db: sqlite3.Connection, rules: Rules) -> None:
    conversation = verify_sam(db)
    before = dict(booking(db, "CB-7KQ4MX"))
    card = ok(
        call(
            db,
            conversation,
            "request_refund",
            {"booking_reference": "CB-7KQ4MX", "reason_text": "The drain clogged again"},
        )
    )
    assert press_confirm(db, rules, conversation, card["action_id"], NOW) == "pending_staff"
    action = action_row(db, card["action_id"])
    assert action["escalation_reason"] == "REFUND"
    decide(db, rules, card["action_id"], StaffDecision.APPROVE, None, NOW)
    assert action_row(db, card["action_id"])["status"] == "executed"  # the decision is recorded
    assert dict(booking(db, "CB-7KQ4MX")) == before  # no booking change, no money
    assert outbox(db) == ["request_received"]


def test_a_customer_cannot_confirm_by_typing(db: sqlite3.Connection, rules: Rules) -> None:
    """Every tool a model could call after "yes, confirm" leaves the card unconfirmed."""
    conversation = verify_sam(db)
    card = ok(call(db, conversation, "request_cancellation", {"booking_reference": "CB-7KQ4MX"}))
    for name in REGISTRY:
        call(db, conversation, name, {"action_id": card["action_id"], "confirm": True})
    for spoof in ["confirm", "confirm_action", "press_confirm", "approve"]:
        assert decision(call(db, conversation, spoof, {"action_id": card["action_id"]})) == (
            "UNKNOWN_TOOL"
        )
    assert action_row(db, card["action_id"])["status"] in {"proposed", "withdrawn"}
    assert booking(db, "CB-7KQ4MX")["status"] == "confirmed"


def test_a_tampered_card_hash_confirms_nothing(db: sqlite3.Connection, rules: Rules) -> None:
    conversation = verify_sam(db)
    card = ok(call(db, conversation, "request_cancellation", {"booking_reference": "CB-7KQ4MX"}))
    payload = json.loads(action_row(db, card["action_id"])["payload"])
    forged = payload_hash({**payload, "booking_id": booking(db, "CB-3HTN8R")["id"]})
    result = confirm(db, rules, card["action_id"], conversation, forged, NOW)
    assert result.outcome == "HASH_MISMATCH"
    assert booking(db, "CB-7KQ4MX")["status"] == "confirmed"
