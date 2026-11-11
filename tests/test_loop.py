"""The turn loop with a scripted model: tools, limits, refusals, fallbacks, checks and cards."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any

import pytest

from citebook.actions import Rules, confirm
from citebook.loop import (
    CARD_NUDGE,
    FALLBACK,
    HANDOFF_COPY,
    HOLDING_COPY,
    ConversationEnded,
    LoopConfig,
    TurnResult,
    handle_message,
    is_person_request,
    is_typed_confirmation,
)
from citebook.models import Ablation, ActionType
from citebook.providers.base import Message, ModelTurn, ModelUnavailable, ToolCall
from citebook.providers.fake import FakeProvider, say, use
from citebook.tools.common import SAY_CARD
from citebook.verification import LIMIT_SAY
from tests.conftest import booking, make_conversation, slot_status
from tests.tooling import CONTACT, KB, NOW


def run(
    db: sqlite3.Connection,
    conversation: str,
    text: str,
    script: list[Any],
    config: LoopConfig | None = None,
    now: Any = NOW,
) -> tuple[TurnResult, FakeProvider]:
    fake = FakeProvider(list(script))
    result = handle_message(db, fake, KB, conversation, text, now, config)
    assert fake.script == [], "the loop did not use the whole script"
    return result, fake


def last_result(messages: list[Message]) -> dict[str, Any]:
    """The envelope of the newest tool result the model was given."""
    content = next(m["content"] for m in reversed(messages) if m["role"] == "tool")
    data: dict[str, Any] = json.loads(content.split("\n")[1])
    return data


def handoffs(db: sqlite3.Connection, conversation: str) -> list[tuple[str, str]]:
    return [
        (r["origin"], r["escalation_reason"])
        for r in db.execute(
            "SELECT origin, escalation_reason FROM actions WHERE conversation_id = ?"
            " AND action_type = 'handoff'",
            (conversation,),
        )
    ]


def roles(db: sqlite3.Connection, conversation: str) -> list[str]:
    return [
        r[0]
        for r in db.execute(
            "SELECT role FROM messages WHERE conversation_id = ? ORDER BY seq", (conversation,)
        )
    ]


# --- normal turns -----------------------------------------------------------------------------


def test_a_policy_question_uses_the_tool_and_passes_the_checks(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    result, fake = run(
        db,
        conversation,
        "How much notice do I need to cancel?",
        [
            use("get_policy", question="cancellation notice"),
            say("You can cancel free of charge with at least 24 hours' notice."),
        ],
    )
    assert [r.kind for r in result.replies] == ["text"]
    assert roles(db, conversation) == ["customer", "tool", "assistant"]
    assert len(fake.offered(0)) == 13
    system, state = fake.calls[0][0][0], fake.calls[0][0][1]
    assert system["role"] == "system" and "not a person" in system["content"]
    assert (
        state["content"].startswith("BEGIN STATE") and "Monday 9 November 2026" in state["content"]
    )
    tool_message = fake.calls[1][0][-1]
    assert tool_message["role"] == "tool" and tool_message["content"].startswith("BEGIN DATA")
    checks = db.execute("SELECT attempt, passed FROM reply_checks").fetchall()
    assert len(checks) == 8 and all(c["passed"] == 1 and c["attempt"] == 1 for c in checks)


def test_date_words_are_resolved_in_the_state_block(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    _, fake = run(
        db, conversation, "Can someone come Thursday?", [say("Which service do you need?")]
    )
    assert (
        "'Thursday' means Thursday 12 November 2026 (2026-11-12)." in fake.calls[0][0][1]["content"]
    )


def test_earlier_tool_results_stay_in_the_history(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    run(
        db,
        conversation,
        "What services do you offer?",
        [use("list_services"), say("We clear drains.")],
    )
    _, fake = run(db, conversation, "And faucets?", [say("Yes, we replace faucets.")])
    sent = fake.calls[0][0]
    assert [m["role"] for m in sent[2:]] == ["user", "assistant", "tool", "assistant", "user"]
    assert sent[3]["tool_calls"][0]["function"]["name"] == "list_services"


def test_a_sensitive_number_is_masked_before_storage_and_the_model(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    _, fake = run(
        db,
        conversation,
        "My card is 4111 1111 1111 1111",
        [say("Please don't share card numbers.")],
    )
    stored = db.execute(
        "SELECT content FROM messages WHERE role = 'customer' AND conversation_id = ?",
        (conversation,),
    ).fetchone()[0]
    assert "4111 1111 1111 1111" not in stored
    assert "4111 1111 1111 1111" not in json.dumps(fake.calls)
    flags = [r[0] for r in db.execute("SELECT flag FROM screen_flags")]
    assert flags == ["SENSITIVE_NUMBER"]


# --- the booking flow and cards ---------------------------------------------------------------


def _pick_slot(messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn:
    slot_id = last_result(messages)["data"]["slots"][0]["slot_id"]
    return use("quote_price", service_code="DRAIN_CLEANING", zip_code="00012", slot_id=slot_id)


def _hold(messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn:
    quote = next(
        json.loads(m["content"].split("\n")[1])["data"]
        for m in reversed(messages)
        if m["role"] == "tool" and '"quote_id"' in m["content"]
    )
    return use("propose_hold", slot_id=quote["slot_id"], quote_id=quote["quote_id"])


def _quote_text(messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn:
    data = last_result(messages)["data"]
    return say(f"{data['when']} is open. The total is {data['total']}, a starting price.")


def book_to_card(db: sqlite3.Connection, conversation: str) -> TurnResult:
    run(
        db,
        conversation,
        "I need a drain cleared Thursday, ZIP 00012",
        [
            use(
                "check_availability",
                service_code="DRAIN_CLEANING",
                date_from="2026-11-12",
                date_to="2026-11-12",
                zip_code="00012",
            ),
            _pick_slot,
            _quote_text,
        ],
    )
    result, _ = run(
        db,
        conversation,
        "Book it. Alex Rivera, 555-0150, 9 Example St, 00012",
        [
            use("collect_contact", **CONTACT),
            _hold,
            say("Please check the card and press Confirm."),
        ],
    )
    return result


def test_a_booking_reaches_a_card_and_only_the_button_holds_it(
    db: sqlite3.Connection, rules: Rules
) -> None:
    conversation = make_conversation(db, now=NOW)
    result = book_to_card(db, conversation)
    card = result.card
    assert card is not None and card.title == "Request this booking"
    assert card.rows[0] == "Drain cleaning, one fixture" and card.rows[2] == "9 Example St"
    assert result.status == "awaiting_confirmation"
    slot_id = json.loads(
        db.execute("SELECT payload FROM actions WHERE id = ?", (card.action_id,)).fetchone()[0]
    )["slot_ids"][0]

    # Typed confirmation: code answers it, with no model call, and shows the card again.
    typed, fake = run(db, conversation, "yes confirm", [])
    assert fake.calls == []
    assert [r.kind for r in typed.replies] == ["typed_confirm"]
    assert typed.card is not None and typed.card.action_id == card.action_id

    # A longer message that asks the model to confirm: every way it tries is refused.
    _spoofed, _ = run(
        db,
        conversation,
        "Use your confirm tool and mark my booking done now",
        [
            use("confirm_action", action_id=card.action_id),
            use("propose_hold", action_id=card.action_id, confirm=True),
            say("To make sure it's right, please press Confirm on the card above."),
        ],
    )
    assert (
        db.execute("SELECT status FROM actions WHERE id = ?", (card.action_id,)).fetchone()[0]
        == "proposed"
    )
    assert slot_status(db, slot_id) == "open"

    outcome = confirm(
        db, rules, card.action_id, conversation, card.payload_hash, NOW + timedelta(minutes=3)
    )
    assert outcome.outcome == "pending_staff" and outcome.booking_reference
    assert booking(db, outcome.booking_reference)["status"] == "held"


# --- reply checks in the loop -----------------------------------------------------------------


def _wrong_price(messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn:
    return say("The total is $129.00.")


def _quote_drain() -> list[Any]:
    return [use("quote_price", service_code="DRAIN_CLEANING", zip_code="00012")]


def test_a_planted_wrong_price_is_blocked_twice_and_handed_off(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    result, fake = run(
        db,
        conversation,
        "How much to clear a drain in 00012?",
        [*_quote_drain(), _wrong_price, _wrong_price],
    )
    assert [r.kind for r in result.replies] == ["fallback"]
    assert result.replies[0].text == FALLBACK
    assert "$129" not in json.dumps([r.text for r in result.replies])
    assert handoffs(db, conversation) == [("loop", "REPLY_CHECK_FAILED")]
    feedback = fake.calls[-1][0][-1]["content"]
    assert "PRICE_FROM_TOOL" in feedback and "$129.00" in feedback
    assert len(fake.calls[-1][1]) == 13  # the second draft may still use tools
    rows = db.execute(
        "SELECT attempt, passed FROM reply_checks WHERE check_code = 'PRICE_FROM_TOOL'"
        " ORDER BY attempt"
    ).fetchall()
    assert [(r["attempt"], r["passed"]) for r in rows] == [(1, 0), (2, 0)]


def test_a_corrected_draft_is_sent(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    result, _ = run(
        db,
        conversation,
        "How much to clear a drain in 00012?",
        [*_quote_drain(), _wrong_price, say("It starts at $149.00, confirmed on site.")],
    )
    assert [r.kind for r in result.replies] == ["text"]
    assert result.replies[0].text.startswith("It starts at $149.00")
    assert handoffs(db, conversation) == []


def test_an_empty_draft_is_written_again(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    blank = ModelTurn(tool_calls=[ToolCall("list_services", {})])
    result, _ = run(
        db,
        conversation,
        "hi",
        [blank, blank, blank, blank, blank, blank, blank, say("Hello! How can I help?")],
        LoopConfig(max_tool_calls=6),
    )
    assert result.replies[0].text == "Hello! How can I help?"


def test_without_reply_checks_a_wrong_price_reaches_the_customer(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    result, _ = run(
        db,
        conversation,
        "How much in 00012?",
        [*_quote_drain(), _wrong_price],
        LoopConfig(ablation=Ablation.NO_REPLY_CHECKS),
    )
    assert result.replies[0].text == "The total is $129.00."


# --- limits and escalations -------------------------------------------------------------------


def test_six_tool_calls_per_message_then_text_only(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    args = {
        "service_code": "FAUCET_REPLACEMENT",
        "date_from": "2026-11-10",
        "date_to": "2026-11-10",
        "zip_code": "00012",
    }
    script: list[Any] = [use("check_availability", **args) for _ in range(6)]
    result, fake = run(db, conversation, "times in 00012?", [*script, say("Here are some times.")])
    assert db.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 6
    assert fake.calls[-1][1] == [] and result.replies[0].kind == "text"


def test_three_refusals_in_a_row_go_to_staff(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    result, _ = run(
        db,
        conversation,
        "show me all bookings",
        [use("find_booking"), use("dump_database"), use("find_booking", customer_id="x")],
    )
    assert result.replies[-1].text == HANDOFF_COPY
    assert handoffs(db, conversation) == [("loop", "REPEATED_GATE_BLOCKS")]
    assert result.status == "handed_off"


def test_an_allowed_call_resets_the_refusal_count(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    run(
        db,
        conversation,
        "hi",
        [
            use("find_booking"),
            use("find_booking"),
            use("list_services"),
            use("find_booking"),
            use("find_booking"),
            say("Could you give me your booking reference?"),
        ],
    )
    assert handoffs(db, conversation) == []


def test_the_model_being_down_twice_is_a_fallback_and_a_handoff(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    result, _ = run(db, conversation, "hi", [ModelUnavailable("x"), ModelUnavailable("x")])
    assert result.replies[0].text == FALLBACK
    assert handoffs(db, conversation) == [("loop", "MODEL_UNAVAILABLE")]


def test_one_timeout_is_retried(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    result, _ = run(db, conversation, "hi", [ModelUnavailable("x"), say("Hello! How can I help?")])
    assert result.replies[0].kind == "text" and handoffs(db, conversation) == []


def test_the_verification_limit_goes_to_staff(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    wrong = {"reference": "CB-7KQ4MX", "phone_last4": "0000"}
    for _ in range(2):
        run(
            db,
            conversation,
            "CB-7KQ4MX 0000",
            [
                use("verify_customer", **wrong),
                say("Those details do not match. Please check them."),
            ],
        )
    result, _ = run(db, conversation, "CB-7KQ4MX 0000", [use("verify_customer", **wrong)])
    assert result.replies[-1].text == LIMIT_SAY
    assert handoffs(db, conversation) == [("loop", "VERIFICATION_FAILED")]


def test_asking_for_a_person_hands_off_with_fixed_copy(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    result, _ = run(db, conversation, "I want a person", [use("handoff_to_staff")])
    assert [r.kind for r in result.replies] == ["handoff"]
    assert handoffs(db, conversation) == [("tool", "CUSTOMER_ASKED_FOR_PERSON")]


# --- no model at all --------------------------------------------------------------------------


def test_an_emergency_gets_safety_copy_and_no_model(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    result, fake = run(db, conversation, "I smell gas in the kitchen", [])
    assert fake.calls == []
    assert result.replies[0].kind == "emergency"
    assert "Leave the building" in result.replies[0].text and "555-0172" in result.replies[0].text
    assert handoffs(db, conversation) == [("screening", "EMERGENCY")]


def test_a_handed_off_conversation_gets_the_holding_message(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    run(db, conversation, "a person please", [use("handoff_to_staff")])
    result, fake = run(db, conversation, "hello?", [])
    assert fake.calls == [] and result.replies[0].text == HOLDING_COPY


@pytest.mark.parametrize("status", ["closed", "abandoned"])
def test_an_ended_conversation_takes_no_messages(db: sqlite3.Connection, status: str) -> None:
    conversation = make_conversation(db, now=NOW)
    db.execute("UPDATE conversations SET status = ? WHERE id = ?", (status, conversation))
    with pytest.raises(ConversationEnded):
        handle_message(db, FakeProvider(), KB, conversation, "hi", NOW)


def test_an_idle_conversation_is_abandoned_before_the_turn(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)  # last activity 7:00 AM
    with pytest.raises(ConversationEnded):
        handle_message(db, FakeProvider(), KB, conversation, "hi", NOW + timedelta(hours=2))


# --- data marking -----------------------------------------------------------------------------


def test_booking_notes_reach_the_model_marked_as_data(db: sqlite3.Connection) -> None:
    conversation = make_conversation(
        db, now=NOW, verified_booking_id=booking(db, "CB-5NAE4P")["id"]
    )
    _, fake = run(
        db,
        conversation,
        "what's my booking?",
        [use("find_booking"), say("Your booking is on Wednesday 2 December.")],
    )
    content = fake.calls[1][0][-1]["content"]
    assert content.startswith("BEGIN DATA") and '"untrusted_fields": ["customer_notes"]' in content


def test_without_data_marking_results_are_plain(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    _, fake = run(
        db,
        conversation,
        "services?",
        [use("list_services"), say("We clear drains.")],
        LoopConfig(ablation=Ablation.NO_DATA_MARKING),
    )
    content = fake.calls[1][0][-1]["content"]
    assert not content.startswith("BEGIN DATA") and "untrusted_fields" not in content


def test_the_loop_writes_no_message_content_to_audit_events_or_logs(
    db: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    marker = "Zanzibarquux"
    conversation = make_conversation(db, now=NOW)
    caplog.set_level("INFO", logger="citebook")
    run(
        db,
        conversation,
        f"Hi, I'm {marker}, can you help?",
        [
            use("get_policy", question=f"{marker} hours"),
            say(f"Hello {marker}, we open at 7:30 AM."),
        ],
    )
    run(db, conversation, f"{marker} again", [ModelUnavailable("x"), ModelUnavailable("x")])
    audit_text = json.dumps([dict(r) for r in db.execute("SELECT * FROM audit_events")])
    assert marker not in audit_text and marker not in caplog.text
    assert "model_called" in audit_text and "reply_checked" in audit_text


def test_an_invented_zip_code_is_refused(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    _, fake = run(
        db,
        conversation,
        "My sink is blocked, can someone come Thursday?",
        [
            use(
                "check_availability",
                service_code="DRAIN_CLEANING",
                date_from="2026-11-12",
                date_to="2026-11-12",
                zip_code="00000",
            ),
            say("What is the ZIP code of the job?"),
        ],
    )
    refusal = json.loads(fake.calls[1][0][-1]["content"].split("\n")[1])
    assert refusal["gate_decision"] == "INVALID_ARGUMENTS"
    assert refusal["say"].startswith("Ask the customer for the ZIP code")


@pytest.mark.parametrize(
    ("tool", "extra"),
    [
        ("check_availability", {"date_from": "2026-11-12", "date_to": "2026-11-12"}),
        ("quote_price", {}),
    ],
)
def test_a_service_not_bookable_online_says_so_before_any_zip_code(
    db: sqlite3.Connection, tool: str, extra: dict[str, str]
) -> None:
    conversation = make_conversation(db, now=NOW)
    _, fake = run(
        db,
        conversation,
        "My sewer line is broken. Can I book the repair?",
        [
            use(tool, service_code="SEWER_LINE_REPAIR", zip_code="00000", **extra),
            say("That repair needs a camera inspection first."),
        ],
    )
    refusal = json.loads(fake.calls[1][0][-1]["content"].split("\n")[1])
    assert refusal["gate_decision"] == "POLICY_BLOCKED"
    assert "CAMERA_INSPECTION" in refusal["say"]


@pytest.mark.parametrize(
    ("text", "typed"),
    [
        ("yes", True),
        ("Yes, confirm it.", True),
        ("Yes, please book it.", True),
        ("ok go ahead", True),
        ("yes but can you make it 10am instead?", False),
        ("Yes, what time is that?", False),
        ("no thanks", False),
        ("please send me the prices for a toilet", False),
        ("Yes I would like that booking confirmed for the earlier time on Thursday", False),
    ],
)
def test_what_counts_as_a_typed_confirmation(text: str, typed: bool) -> None:
    assert is_typed_confirmation(text) is typed


def test_without_an_open_card_a_yes_goes_to_the_model(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    result, fake = run(db, conversation, "yes", [say("How can I help?")])
    assert len(fake.calls) == 1 and result.replies[0].kind == "text"


def test_the_second_draft_may_create_the_card_it_talked_about(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    run(
        db,
        conversation,
        "Drain cleaning Thursday please, ZIP 00012",
        [
            use(
                "check_availability",
                service_code="DRAIN_CLEANING",
                date_from="2026-11-12",
                date_to="2026-11-12",
                zip_code="00012",
            ),
            _pick_slot,
            _quote_text,
        ],
    )
    result, _ = run(
        db,
        conversation,
        "Alex Rivera, 555-0150, 9 Example St, 00012",
        [
            use("collect_contact", **CONTACT),
            say("Please press Confirm on the card below."),  # no card exists: the check fails
            _hold,
            say("Please check the details on the card and press Confirm to request this booking."),
        ],
    )
    assert result.card is not None and [r.kind for r in result.replies] == ["text"]
    passed = db.execute(
        "SELECT attempt, passed FROM reply_checks WHERE check_code = 'NO_FALSE_CONFIRMATION'"
        " ORDER BY attempt"
    ).fetchall()
    assert [(r["attempt"], r["passed"]) for r in passed][-2:] == [(1, 0), (2, 1)]


def test_internal_ids_and_bold_are_removed_from_replies() -> None:
    from citebook.loop import plain_text

    slot = "0386cedd-76d2-4840-8e43-77075a7a9076"
    text = f"1. **8:00 AM** (slot_id: {slot})\n2. 10:00 AM (`{slot}`)\nOr {slot}."
    assert plain_text(text) == "1. 8:00 AM\n2. 10:00 AM\nOr ."


@pytest.mark.parametrize(
    ("text", "person"),
    [
        ("Can I talk to a real person please?", True),
        ("I'd like to talk to a person, please.", True),
        ("Actually, I'd rather speak to someone in the office.", True),
        ("Please connect me to the office.", True),
        ("Can a person come Thursday?", False),
        ("Can someone come Thursday afternoon?", False),
        ("The person who came was late.", False),
        ("Can I speak to the plumber when he arrives?", False),
    ],
)
def test_person_requests_are_recognised(text: str, person: bool) -> None:
    assert is_person_request(text) is person


def test_a_request_for_a_person_hands_off_without_the_model(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    result, fake = run(db, conversation, "Can I talk to a real person please?", [])
    assert [r.text for r in result.replies] == [HANDOFF_COPY]
    assert fake.calls == []
    row = db.execute(
        "SELECT escalation_reason FROM actions WHERE conversation_id = ?"
        " AND action_type = 'handoff'",
        (conversation,),
    ).fetchone()
    assert row["escalation_reason"] == "CUSTOMER_ASKED_FOR_PERSON"


def test_asking_whether_to_proceed_gets_one_nudge_toward_the_card(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db, now=NOW)
    result, fake = run(
        db,
        conversation,
        "Please cancel my booking.",
        [say("Would you like to cancel this booking?"), say("Which booking is it?")],
    )
    assert [r.text for r in result.replies] == ["Which booking is it?"]
    assert fake.calls[1][0][-1] == {"role": "system", "content": CARD_NUDGE}


def test_a_card_turn_whose_drafts_fail_twice_keeps_the_card_with_its_fixed_sentence(
    db: sqlite3.Connection,
) -> None:
    conversation = make_conversation(db, now=NOW)
    run(
        db,
        conversation,
        "I need a drain cleared Thursday, ZIP 00012",
        [
            use(
                "check_availability",
                service_code="DRAIN_CLEANING",
                date_from="2026-11-12",
                date_to="2026-11-12",
                zip_code="00012",
            ),
            _pick_slot,
            _quote_text,
        ],
    )
    result, _ = run(
        db,
        conversation,
        "Book it. Alex Rivera, 555-0150, 9 Example St, 00012",
        [
            use("collect_contact", **CONTACT),
            _hold,
            say("Your booking is confirmed."),
            say("Done, it is booked and confirmed."),
        ],
    )
    assert result.card is not None
    assert [r.text for r in result.replies] == [SAY_CARD[ActionType.HOLD_CREATE]]
    handoffs = db.execute(
        "SELECT COUNT(*) FROM actions WHERE conversation_id = ? AND action_type = 'handoff'",
        (conversation,),
    ).fetchone()[0]
    assert handoffs == 0


@pytest.mark.parametrize(
    ("contact", "accepted"),
    [
        ({"name": "Alex Rivera", "phone": "555 0150", "address": "9 Example Street"}, True),
        ({"name": "Customer", "phone": "555-0142", "address": "123 Main Street"}, False),
        ({"name": "Alex Rivera", "phone": "555-0142", "address": "9 Example St"}, False),
        ({"name": "Alex Rivera", "phone": "555-0150", "address": "123 Main Street"}, False),
    ],
)
def test_contact_details_must_come_from_the_customer(
    db: sqlite3.Connection, contact: dict[str, str], accepted: bool
) -> None:
    conversation = make_conversation(db, now=NOW)
    _, fake = run(
        db,
        conversation,
        "Book it please. Alex Rivera, 555-0150, 9 Example St, Millrace, 00012.",
        [use("collect_contact", zip_code="00012", **contact), say("Thanks.")],
    )
    result = json.loads(fake.calls[1][0][-1]["content"].split("\n")[1])
    if accepted:
        assert result["gate_decision"] == "ALLOWED" if "gate_decision" in result else result["ok"]
    else:
        assert result["gate_decision"] == "INVALID_ARGUMENTS"
        assert result["say"].startswith("Ask the customer for their name, phone number")
