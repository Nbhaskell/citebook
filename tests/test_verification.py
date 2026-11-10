import sqlite3

import pytest

from citebook.verification import FAILED_SAY, LIMIT_SAY, verify
from tests.conftest import booking, make_conversation
from tests.test_actions import NOW


def conv(conn: sqlite3.Connection, conversation_id: str) -> sqlite3.Row:
    row: sqlite3.Row = conn.execute(
        "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    return row


def test_matching_details_verify_one_booking(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db)
    result = verify(db, conversation, "CB-7KQ4MX", "0142", 3, NOW)
    assert result.ok is True
    row = conv(db, conversation)
    target = booking(db, "CB-7KQ4MX")
    assert row["verification_state"] == "verified"
    assert row["verified_booking_id"] == target["id"]
    assert row["customer_id"] == target["customer_id"]


@pytest.mark.parametrize(("reference", "digits"), [(" cb-7kq4mx ", "0142"), ("CB-7KQ4MX", " 0142")])
def test_spaces_and_case_do_not_matter(db: sqlite3.Connection, reference: str, digits: str) -> None:
    assert verify(db, make_conversation(db), reference, digits, 3, NOW).ok is True


@pytest.mark.parametrize(
    ("reference", "digits"),
    [("CB-7KQ4MX", "0143"), ("CB-ZZZZZZ", "0142"), ("not a reference", "0142"), ("CB-7KQ4MX", "")],
)
def test_every_failure_says_the_same_thing(
    db: sqlite3.Connection, reference: str, digits: str
) -> None:
    conversation = make_conversation(db)
    result = verify(db, conversation, reference, digits, 3, NOW)
    assert (result.ok, result.say, result.escalate) == (False, FAILED_SAY, False)
    assert conv(db, conversation)["verification_state"] == "anonymous"
    assert conv(db, conversation)["verification_failures"] == 1


def test_the_limit_escalates_and_then_refuses_even_correct_details(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db)
    assert verify(db, conversation, "CB-7KQ4MX", "1111", 3, NOW).escalate is False
    assert verify(db, conversation, "CB-7KQ4MX", "2222", 3, NOW).escalate is False
    third = verify(db, conversation, "CB-7KQ4MX", "3333", 3, NOW)
    assert (third.ok, third.escalate, third.say) == (False, True, LIMIT_SAY)
    after = verify(db, conversation, "CB-7KQ4MX", "0142", 3, NOW)
    assert after.ok is False and after.escalate is True
    assert conv(db, conversation)["verification_state"] == "anonymous"


def test_a_cancelled_booking_cannot_be_verified(db: sqlite3.Connection) -> None:
    db.execute("UPDATE bookings SET status = 'cancelled' WHERE reference = 'CB-7KQ4MX'")
    result = verify(db, make_conversation(db), "CB-7KQ4MX", "0142", 3, NOW)
    assert (result.ok, result.say) == (False, FAILED_SAY)


def test_audit_events_hold_no_reference_or_digits(db: sqlite3.Connection) -> None:
    conversation = make_conversation(db)
    verify(db, conversation, "CB-7KQ4MX", "9999", 3, NOW)
    verify(db, conversation, "CB-7KQ4MX", "0142", 3, NOW)
    text = " ".join(r[0] for r in db.execute("SELECT payload FROM audit_events"))
    assert "7KQ4MX" not in text and "0142" not in text and "9999" not in text
