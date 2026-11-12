"""What each tool returns once the gate allows it."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import timedelta

import pytest

from citebook.kb import FakeKB
from citebook.loop import add_message
from citebook.timeutil import EN_DASH, parse_utc
from tests.conftest import TZ, booking, local, make_conversation, new_customer
from tests.tooling import CONTACT, NOW, SAM, call, decision, first_slot, ok, quote_for

DASH = EN_DASH


def verified(conn: sqlite3.Connection, reference: str = "CB-7KQ4MX") -> str:
    return make_conversation(conn, verified_booking_id=booking(conn, reference)["id"])


def availability(conn: sqlite3.Connection, conversation: str, **change: str) -> dict[str, object]:
    args = {
        "service_code": "FAUCET_REPLACEMENT",
        "date_from": "2026-11-10",
        "date_to": "2026-11-12",
        "zip_code": "00012",
        **change,
    }
    return ok(call(conn, conversation, "check_availability", args))


# --- list_services ----------------------------------------------------------------------------


def test_list_services(db: sqlite3.Connection) -> None:
    data = ok(call(db, make_conversation(db), "list_services"))
    by_code = {s["code"]: s for s in data["services"]}
    assert len(by_code) == 19
    assert by_code["DRAIN_CLEANING"]["price"] == "from $149.00"
    assert by_code["FAUCET_REPLACEMENT"]["price"] == "$165.00"
    assert by_code["GENERAL_REPAIR"]["price"] == "$115.00 per hour"
    assert by_code["SEWER_LINE_REPAIR"]["book_first"]["code"] == "CAMERA_INSPECTION"
    assert by_code["SEWER_LINE_REPAIR"]["bookable_online"] is False


# --- check_availability -----------------------------------------------------------------------


def test_availability_returns_at_most_six_future_windows_once_each(db: sqlite3.Connection) -> None:
    data = availability(db, make_conversation(db))
    slots = data["slots"]
    assert isinstance(slots, list) and 1 <= len(slots) <= 6
    starts = [
        db.execute("SELECT starts_at FROM slots WHERE id = ?", (s["slot_id"],)).fetchone()[0]
        for s in slots
    ]
    assert starts == sorted(set(starts))
    assert all(parse_utc(s) > NOW for s in starts)
    assert re.fullmatch(
        rf"Tue 10 Nov, arrival \d+:00(?: [AP]M)?{DASH}\d+:00 [AP]M", slots[0]["when"]
    )


def test_availability_lists_the_days_the_customer_named(db: sqlite3.Connection) -> None:
    # S-013: the range started on Monday, so 6 earlier windows hid Friday 13 November.
    conversation = make_conversation(db)
    add_message(db, conversation, "customer", "Friday 13 November, ZIP 00012.", NOW)
    data = availability(
        db,
        conversation,
        service_code="DRAIN_CLEANING",
        date_from="2026-11-09",
        date_to="2026-11-14",
    )
    whens = [s["when"] for s in data["slots"]]  # type: ignore[attr-defined]
    assert len(whens) == 3 and all(w.startswith("Fri 13 Nov") for w in whens)
    assert data["note"] == "Only windows on the days the customer named are listed."


def test_availability_says_when_a_named_day_has_no_windows(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db)
    add_message(db, conversation, "customer", "Sunday 15 November? ZIP 00012.", NOW)
    data = availability(
        db,
        conversation,
        service_code="DRAIN_CLEANING",
        date_from="2026-11-09",
        date_to="2026-11-15",
    )
    whens = [s["when"] for s in data["slots"]]  # type: ignore[attr-defined]
    assert len(whens) == 6 and whens[0].startswith("Mon 9 Nov")
    assert data["note"] == (
        "No open windows on the days the customer named (Sunday 15 November). These are the "
        "earliest other windows in the range."
    )


def test_availability_says_when_the_named_day_is_outside_the_range(db: sqlite3.Connection) -> None:
    # S-015: the model searched without the customer's day and said that day had no windows.
    conversation = make_conversation(db)
    add_message(db, conversation, "customer", "Friday 20 November? ZIP 00012.", NOW)
    data = availability(
        db,
        conversation,
        service_code="DRAIN_CLEANING",
        date_from="2026-11-09",
        date_to="2026-11-12",
    )
    assert len(data["slots"]) == 6  # type: ignore[arg-type]
    assert data["note"] == (
        "The customer asked for Friday 20 November, which is outside this range. "
        "Call check_availability for that day before you say anything about it."
    )


def test_availability_without_named_days_is_the_earliest_windows(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db)
    add_message(db, conversation, "customer", "Whenever is soonest, ZIP 00012.", NOW)
    data = availability(
        db,
        conversation,
        service_code="DRAIN_CLEANING",
        date_from="2026-11-09",
        date_to="2026-11-14",
    )
    whens = [s["when"] for s in data["slots"]]  # type: ignore[attr-defined]
    assert len(whens) == 6 and whens[0].startswith("Mon 9 Nov") and "note" not in data


def test_afternoon_means_windows_starting_at_noon_or_later(db: sqlite3.Connection) -> None:
    data = availability(db, make_conversation(db), part_of_day="afternoon")
    for s in data["slots"]:  # type: ignore[attr-defined]
        start = db.execute("SELECT starts_at FROM slots WHERE id = ?", (s["slot_id"],)).fetchone()[
            0
        ]
        assert parse_utc(start).astimezone(TZ).hour >= 12


def test_only_technicians_with_the_skill_and_two_touching_windows(db: sqlite3.Connection) -> None:
    data = availability(db, make_conversation(db), service_code="HOME_INSPECTION")
    for s in data["slots"]:  # type: ignore[attr-defined]
        row = db.execute(
            "SELECT s.*, t.skills FROM slots s JOIN technicians t ON t.id = s.technician_id"
            " WHERE s.id = ?",
            (s["slot_id"],),
        ).fetchone()
        assert "inspection" in json.loads(row["skills"])
        nxt = db.execute(
            "SELECT status FROM slots WHERE technician_id = ? AND starts_at = ?",
            (row["technician_id"], row["ends_at"]),
        ).fetchone()
        assert nxt["status"] == "open"


def test_a_closed_date_has_no_windows(db: sqlite3.Connection) -> None:
    data = availability(db, make_conversation(db), date_from="2026-11-26", date_to="2026-11-26")
    assert data["slots"] == [] and "other dates" in str(data["note"])


def test_windows_already_started_are_not_offered(db: sqlite3.Connection) -> None:
    late = local("2026-11-10T13:00")
    data = ok(
        call(
            db,
            make_conversation(db),
            "check_availability",
            {
                "service_code": "FAUCET_REPLACEMENT",
                "date_from": "2026-11-10",
                "date_to": "2026-11-10",
                "zip_code": "00012",
            },
            late,
        )
    )
    for s in data["slots"]:
        start = db.execute("SELECT starts_at FROM slots WHERE id = ?", (s["slot_id"],)).fetchone()[
            0
        ]
        assert parse_utc(start) > late


# --- quote_price ------------------------------------------------------------------------------


def test_a_starting_price_quote_with_the_call_out_credit(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db)
    slot_id = first_slot(db, conversation, "DRAIN_CLEANING")
    data = quote_for(db, conversation, slot_id, "DRAIN_CLEANING")
    assert data["total"] == "$149.00"
    assert data["lines"] == [
        ["Call-out fee", "$89.00"],
        ["Drain cleaning, one fixture (starting price)", "$149.00"],
        ["Call-out fee credited when you approve the work", "-$89.00"],
    ]
    assert data["is_estimate"] is True
    assert data["note"].endswith("Price valid until 8:30 AM.")
    assert "$89.00 call-out fee applies" in data["note"]
    stored = db.execute("SELECT * FROM quotes WHERE id = ?", (data["quote_id"],)).fetchone()
    assert stored["conversation_id"] == conversation and stored["slot_id"] == slot_id
    assert stored["total_minor"] == 14900
    assert parse_utc(stored["expires_at"]) == NOW + timedelta(minutes=30)


def test_an_outer_zip_code_adds_the_travel_fee(db: sqlite3.Connection) -> None:
    data = ok(
        call(
            db,
            make_conversation(db),
            "quote_price",
            {"service_code": "FAUCET_REPLACEMENT", "zip_code": "00022"},
        )
    )
    assert ["Travel fee (Saltmarsh)", "$40.00"] in data["lines"]
    assert data["total"] == "$205.00"
    assert "slot_id" in data["next"]


def test_an_hourly_quote_is_an_estimate(db: sqlite3.Connection) -> None:
    data = ok(
        call(
            db,
            make_conversation(db),
            "quote_price",
            {"service_code": "GENERAL_REPAIR", "zip_code": "00012"},
        )
    )
    assert data["is_estimate"] is True and "Parts are extra" in data["note"]


# --- get_policy -------------------------------------------------------------------------------


def test_policy_passages_are_marked_untrusted(db: sqlite3.Connection) -> None:
    envelope = call(db, make_conversation(db), "get_policy", {"question": "late cancellation fee"})
    assert envelope["untrusted_fields"] == ["passages"]
    passages = envelope["data"]["passages"]
    assert 1 <= len(passages) <= 4 and all("#" in p["source_id"] for p in passages)


def test_nothing_found_says_so(db: sqlite3.Connection) -> None:
    data = ok(call(db, make_conversation(db), "get_policy", {"question": "Do you repair cars?"}))
    assert data["found"] is False and "NOT_IN_KNOWLEDGE_BASE" in data["note"]


def test_a_knowledge_base_outage_is_reported(db: sqlite3.Connection) -> None:
    down = FakeKB([])
    down.fail = True
    data = ok(call(db, make_conversation(db), "get_policy", {"question": "hours"}, kb=down))
    assert data["found"] is False and data["unavailable"] is True


# --- collect_contact --------------------------------------------------------------------------


def test_collect_contact_creates_one_customer_for_the_conversation(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db)
    data = ok(call(db, conversation, "collect_contact", {**CONTACT, "phone": "(555) 0150"}))
    assert data == {"saved": True, "area": "Millrace", "zone": "core"}
    row = db.execute("SELECT * FROM conversations WHERE id = ?", (conversation,)).fetchone()
    assert row["verification_state"] == "contact_collected"
    customer = db.execute("SELECT * FROM customers WHERE id = ?", (row["customer_id"],)).fetchone()
    assert customer["phone"] == "555-0150"
    count = db.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    ok(call(db, conversation, "collect_contact", {**CONTACT, "address": "10 Example St"}))
    assert db.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == count
    assert (
        db.execute("SELECT address FROM customers WHERE id = ?", (row["customer_id"],)).fetchone()[
            0
        ]
        == "10 Example St"
    )


def test_a_known_phone_number_never_links_to_the_existing_customer(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db)
    ok(call(db, conversation, "collect_contact", {**CONTACT, "phone": "555-0142"}))
    row = db.execute(
        "SELECT customer_id FROM conversations WHERE id = ?", (conversation,)
    ).fetchone()
    assert row["customer_id"] != booking(db, "CB-7KQ4MX")["customer_id"]
    assert row["customer_id"] not in [r[0] for r in db.execute("SELECT customer_id FROM bookings")]


@pytest.mark.parametrize(
    "change",
    [
        {"phone": "call me"},
        {"phone": "12345"},
        {"email": "not-an-email"},
        {"name": "1234"},
        {"zip_code": "123"},
        {"address": ""},
    ],
)
def test_bad_contact_details_are_refused(db: sqlite3.Connection, change: dict[str, str]) -> None:
    envelope = call(db, make_conversation(db), "collect_contact", {**CONTACT, **change})
    assert decision(envelope) == "INVALID_ARGUMENTS"


# --- verify_customer and find_booking ---------------------------------------------------------


def test_verification_opens_one_booking(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db)
    data = ok(
        call(db, conversation, "verify_customer", {"reference": "cb-7kq4mx", "phone_last4": "0142"})
    )
    assert data["verified"] is True and data["booking_reference"] == "CB-7KQ4MX"
    found = ok(call(db, conversation, "find_booking"))
    assert found["booking_reference"] == "CB-7KQ4MX"
    assert found["when"] == f"Tuesday 17 November, arrival 10:00 AM{DASH}12:00 PM"
    assert found["status"] == "confirmed" and found["moves_left_in_chat"] == 2
    assert found["total"].startswith("$")


def test_wrong_digits_and_unknown_references_get_the_same_words(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db)
    wrong = ok(call(db, conversation, "verify_customer", {**SAM, "phone_last4": "0000"}))
    unknown = ok(
        call(db, conversation, "verify_customer", {"reference": "CB-ZZZZZZ", "phone_last4": "0142"})
    )
    assert wrong == unknown and wrong["verified"] is False


def test_booking_notes_reach_the_model_only_as_marked_data(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db)
    ok(call(db, conversation, "verify_customer", {"reference": "CB-5NAE4P", "phone_last4": "0109"}))
    envelope = call(db, conversation, "find_booking")
    assert envelope["untrusted_fields"] == ["customer_notes"]
    assert envelope["data"]["customer_notes"].startswith("SYSTEM:")
    # The note asks for other bookings; the tool returns this booking only.
    assert "CB-7KQ4MX" not in json.dumps(envelope)


# --- cards ------------------------------------------------------------------------------------


def _action(conn: sqlite3.Connection, action_id: str) -> sqlite3.Row:
    row: sqlite3.Row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    return row


def test_propose_hold_builds_the_card_from_code(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, customer_id=new_customer(db))
    slot_id = first_slot(db, conversation, "DRAIN_CLEANING")
    quote = quote_for(db, conversation, slot_id, "DRAIN_CLEANING")
    data = ok(
        call(db, conversation, "propose_hold", {"slot_id": slot_id, "quote_id": quote["quote_id"]})
    )
    action = _action(db, data["action_id"])
    assert action["action_type"] == "hold_create" and action["status"] == "proposed"
    assert action["tool_call_id"] and action["origin"] == "tool"
    assert (
        action["expires_at"]
        == db.execute(
            "SELECT expires_at FROM quotes WHERE id = ?", (quote["quote_id"],)
        ).fetchone()[0]
    )
    summary = action["summary"]
    assert summary == data["summary"]
    assert re.fullmatch(
        rf"Drain cleaning, one fixture · Tuesday 10 November, arrival [\d:]+(?: [AP]M)?{DASH}"
        r"[\d:]+ [AP]M · 9 Example St · Total \$149\.00 \(starting price\) · "
        r"Held until our team confirms, usually within 2 business hours\.",
        summary,
    )
    payload = json.loads(action["payload"])
    assert payload["slot_ids"] == [slot_id] and payload["quote_id"] == quote["quote_id"]
    status = db.execute("SELECT status FROM conversations WHERE id = ?", (conversation,)).fetchone()
    assert status[0] == "awaiting_confirmation"


def test_a_new_hold_card_replaces_the_open_one(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, customer_id=new_customer(db))
    first = first_slot(db, conversation)
    second = first_slot(db, conversation, index=1)
    a = ok(
        call(
            db,
            conversation,
            "propose_hold",
            {"slot_id": first, "quote_id": quote_for(db, conversation, first)["quote_id"]},
        )
    )
    b = ok(
        call(
            db,
            conversation,
            "propose_hold",
            {"slot_id": second, "quote_id": quote_for(db, conversation, second)["quote_id"]},
        )
    )
    assert _action(db, a["action_id"])["status"] == "withdrawn"
    assert _action(db, b["action_id"])["status"] == "proposed"


def test_a_reschedule_card_shows_both_times_and_a_changed_total(db: sqlite3.Connection) -> None:
    conversation = verified(db)
    db.execute(
        "UPDATE quotes SET total_minor = 20000 WHERE id = ?",
        (booking(db, "CB-7KQ4MX")["quote_id"],),
    )
    slot_id = first_slot(db, conversation, "DRAIN_CLEANING", "2026-11-19")
    quote = quote_for(db, conversation, slot_id, "DRAIN_CLEANING")
    args = {"booking_reference": "CB-7KQ4MX", "slot_id": slot_id, "quote_id": quote["quote_id"]}
    data = ok(call(db, conversation, "propose_reschedule", args))
    summary = data["summary"]
    assert summary.startswith(
        "Move CB-7KQ4MX, Drain cleaning, one fixture · From Tuesday 17 November"
    )
    assert " · To Thursday 19 November, arrival " in summary
    assert "Total $149.00 (starting price), was $200.00" in summary
    assert "late fee" not in summary


def _customer_says(conn: sqlite3.Connection, conversation: str, *texts: str) -> None:
    for text in texts:
        add_message(conn, conversation, "customer", text, NOW)


@pytest.mark.parametrize(
    ("texts", "allowed"),
    [
        (["Move CB-7KQ4MX to Thursday 19 November please."], True),
        (["Friday 20 November?", f"Thu 19 Nov, arrival 8:00{DASH}10:00 AM"], True),  # a chip
        (["Move it to next week, any day."], True),
        (["Move it.", "The earliest one."], True),  # no day named: nothing to compare
        # S-015, S-017: the customer named one day and the card showed another.
        (["Move CB-7KQ4MX to Friday 20 November please."], False),
        (["Friday 20 November?", "The earliest afternoon one."], False),
    ],
)
def test_a_reschedule_card_must_be_on_a_day_the_customer_named(
    db: sqlite3.Connection, texts: list[str], allowed: bool
) -> None:
    conversation = verified(db)
    slot_id = first_slot(db, conversation, "DRAIN_CLEANING", "2026-11-19")  # Thursday 19 Nov
    quote = quote_for(db, conversation, slot_id, "DRAIN_CLEANING")
    _customer_says(db, conversation, *texts)
    args = {"booking_reference": "CB-7KQ4MX", "slot_id": slot_id, "quote_id": quote["quote_id"]}
    envelope = call(db, conversation, "propose_reschedule", args)
    if allowed:
        ok(envelope)
    else:
        assert decision(envelope) == "POLICY_BLOCKED"
        assert envelope["say"].startswith(
            "That arrival window is on Thursday 19 November, not a day the customer asked for."
        )


@pytest.mark.parametrize(
    ("text", "allowed"),
    [("Tuesday 10 November, please.", True), ("Wednesday 11 November, please.", False)],
)
def test_a_hold_card_must_be_on_a_day_the_customer_named(
    db: sqlite3.Connection, text: str, allowed: bool
) -> None:
    conversation = make_conversation(db, customer_id=new_customer(db))
    slot_id = first_slot(db, conversation, "DRAIN_CLEANING")  # Tuesday 10 November
    quote = quote_for(db, conversation, slot_id, "DRAIN_CLEANING")
    _customer_says(db, conversation, text)
    args = {"slot_id": slot_id, "quote_id": quote["quote_id"]}
    envelope = call(db, conversation, "propose_hold", args)
    if allowed:
        ok(envelope)
    else:
        assert decision(envelope) == "POLICY_BLOCKED"


def test_a_late_reschedule_card_says_a_person_reviews_it(db: sqlite3.Connection) -> None:
    now = local("2026-11-16T12:00")  # the visit is Tuesday 17 November at 10:00 AM
    conversation = make_conversation(
        db, verified_booking_id=booking(db, "CB-7KQ4MX")["id"], now=now
    )
    slot_id = first_slot(db, conversation, "DRAIN_CLEANING", "2026-11-19", now=now)
    quote = quote_for(db, conversation, slot_id, "DRAIN_CLEANING", now=now)
    args = {"booking_reference": "CB-7KQ4MX", "slot_id": slot_id, "quote_id": quote["quote_id"]}
    data = ok(call(db, conversation, "propose_reschedule", args, now))
    assert "Less than 24 hours before the current visit" in data["summary"]


def test_cancellation_cards_free_and_late(db: sqlite3.Connection) -> None:
    data = ok(call(db, verified(db), "request_cancellation", {"booking_reference": "CB-7KQ4MX"}))
    assert data["summary"].endswith("No fee: cancelled as soon as you confirm.")
    now = local("2026-11-16T12:00")
    conversation = make_conversation(
        db, verified_booking_id=booking(db, "CB-7KQ4MX")["id"], now=now
    )
    data = ok(
        call(db, conversation, "request_cancellation", {"booking_reference": "CB-7KQ4MX"}, now)
    )
    assert "a team member will review the cancellation and any late fee" in data["summary"]


def test_refund_and_exception_cards_promise_nothing(db: sqlite3.Connection) -> None:
    conversation = verified(db)
    data = ok(
        call(
            db,
            conversation,
            "request_refund",
            {"booking_reference": "CB-7KQ4MX", "reason_text": "late"},
        )
    )
    assert "The chat cannot give a refund." in data["summary"]
    conversation = verified(db)
    data = ok(call(db, conversation, "request_exception", {"details": "move it a third time"}))
    assert "Nothing changes until they reply." in data["summary"]
    action = _action(db, data["action_id"])
    assert action["booking_id"] == booking(db, "CB-7KQ4MX")["id"]


def test_handoff_needs_no_confirm(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db)
    data = ok(call(db, conversation, "handoff_to_staff", {"reason": "CUSTOMER_ASKED_FOR_PERSON"}))
    action = _action(db, data["action_id"])
    assert (action["status"], action["origin"], action["escalation_reason"]) == (
        "pending_staff",
        "tool",
        "CUSTOMER_ASKED_FOR_PERSON",
    )
    assert action["tool_call_id"]
    status = db.execute("SELECT status FROM conversations WHERE id = ?", (conversation,)).fetchone()
    assert status[0] == "handed_off"


def test_the_model_cannot_claim_an_emergency_or_loop_reason(db: sqlite3.Connection) -> None:
    envelope = call(db, make_conversation(db), "handoff_to_staff", {"reason": "EMERGENCY"})
    assert decision(envelope) == "INVALID_ARGUMENTS"


# --- handoff_to_staff: reasons code can check (D-085) -------------------------------------------


def _handoff(conn: sqlite3.Connection, conversation: str, reason: str) -> dict[str, object]:
    return call(conn, conversation, "handoff_to_staff", {"reason": reason})


@pytest.mark.parametrize(
    ("reason", "allowed"),
    [
        ("OUT_OF_SERVICE_AREA", False),
        ("POLICY_EXCEPTION", False),
        ("NOT_IN_KNOWLEDGE_BASE", False),
        ("CUSTOMER_ASKED_FOR_PERSON", True),
        ("SUSPICIOUS_INPUT", True),
    ],
)
def test_a_handoff_waits_while_verification_can_still_succeed(
    db: sqlite3.Connection, reason: str, allowed: bool
) -> None:
    # S-110: one wrong pair of digits, then a handoff as OUT_OF_SERVICE_AREA.
    conversation = make_conversation(db)
    add_message(db, conversation, "customer", "Cancel CB-6RJZ3K please, phone ends 0000.", NOW)
    db.execute("UPDATE conversations SET verification_failures = 1 WHERE id = ?", (conversation,))
    envelope = _handoff(db, conversation, reason)
    if allowed:
        ok(envelope)
    else:
        assert decision(envelope) == "POLICY_BLOCKED"
        assert str(envelope["say"]).startswith("Do not hand off yet: the customer can try again.")


def test_a_policy_exception_with_a_verified_booking_is_a_card(db: sqlite3.Connection) -> None:
    # S-112: the third move was refused, and the model handed off instead of a card.
    conversation = verified(db)
    add_message(db, conversation, "customer", "Please ask the team to make an exception.", NOW)
    envelope = _handoff(db, conversation, "POLICY_EXCEPTION")
    assert decision(envelope) == "POLICY_BLOCKED"
    assert str(envelope["say"]).startswith("Use request_exception")
    anonymous = make_conversation(db)
    add_message(db, anonymous, "customer", "Can you make an exception for me?", NOW)
    ok(_handoff(db, anonymous, "POLICY_EXCEPTION"))


@pytest.mark.parametrize(
    ("text", "allowed"),
    [
        ("I'm at ZIP 99999, can you come?", True),
        ("Cancel CB-6RJZ3K please, phone ends 0000.", False),
        ("I'm in 00012.", False),  # a served ZIP code
    ],
)
def test_out_of_service_area_needs_a_zip_code_that_is_not_served(
    db: sqlite3.Connection, text: str, allowed: bool
) -> None:
    conversation = make_conversation(db)
    add_message(db, conversation, "customer", text, NOW)
    envelope = _handoff(db, conversation, "OUT_OF_SERVICE_AREA")
    if allowed:
        ok(envelope)
    else:
        assert decision(envelope) == "POLICY_BLOCKED"
        assert str(envelope["say"]).startswith("The customer has not given a ZIP code")
