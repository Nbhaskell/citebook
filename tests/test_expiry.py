import sqlite3
from datetime import timedelta

from citebook.actions import Rules, confirm, decide, payload_hash
from citebook.expiry import expire_due
from citebook.models import StaffDecision
from citebook.timeutil import parse_utc
from tests.conftest import booking, local, slot_status
from tests.test_actions import LATER, NOW, propose_hold


def action_status(conn: sqlite3.Connection, action_id: str) -> str:
    status: str = conn.execute("SELECT status FROM actions WHERE id = ?", (action_id,)).fetchone()[
        0
    ]
    return status


def conv_status(conn: sqlite3.Connection, conversation_id: str) -> str:
    status: str = conn.execute(
        "SELECT status FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()[0]
    return status


def test_an_unanswered_card_expires(db: sqlite3.Connection, rules: Rules) -> None:
    conversation, action_id, _, _ = propose_hold(db)
    db.execute("UPDATE conversations SET last_activity_at = ?", ("2026-11-09T16:25:00Z",))
    assert expire_due(db, rules, NOW + timedelta(minutes=29)).proposals == 0
    counts = expire_due(db, rules, NOW + timedelta(minutes=30))
    assert counts.proposals == 1
    assert action_status(db, action_id) == "expired"
    assert conv_status(db, conversation) == "active"


def test_a_hold_nobody_confirms_expires_and_frees_its_slot(
    db: sqlite3.Connection, rules: Rules
) -> None:
    conversation, action_id, payload, slots = propose_hold(db)
    held = confirm(db, rules, action_id, conversation, payload_hash(payload), LATER)
    row = booking(db, held.booking_reference or "")
    deadline = parse_utc(row["hold_expires_at"])
    assert expire_due(db, rules, deadline - timedelta(seconds=1)).holds == 0
    counts = expire_due(db, rules, deadline)
    assert counts.holds == 1
    assert booking(db, row["reference"])["status"] == "expired"
    assert action_status(db, action_id) == "expired"
    assert slot_status(db, slots[0]["id"]) == "open"
    kinds = [r[0] for r in db.execute("SELECT kind FROM outbox ORDER BY rowid")]
    assert kinds == ["hold_received", "hold_expired"]


def test_a_confirmed_hold_never_expires(db: sqlite3.Connection, rules: Rules) -> None:
    conversation, action_id, payload, slots = propose_hold(db)
    held = confirm(db, rules, action_id, conversation, payload_hash(payload), LATER)
    decide(db, rules, action_id, StaffDecision.APPROVE, None, LATER)
    assert expire_due(db, rules, local("2026-12-31T12:00")).holds == 0
    assert booking(db, held.booking_reference or "")["status"] == "confirmed"
    assert slot_status(db, slots[0]["id"]) == "booked"


def test_an_idle_conversation_is_abandoned_and_its_card_expires(
    db: sqlite3.Connection, rules: Rules
) -> None:
    conversation, action_id, _, _ = propose_hold(db)
    db.execute("UPDATE actions SET expires_at = '2026-11-10T00:00:00Z' WHERE id = ?", (action_id,))
    assert expire_due(db, rules, NOW + timedelta(minutes=59)).conversations == 0
    counts = expire_due(db, rules, NOW + timedelta(minutes=60))
    assert counts.conversations == 1
    assert conv_status(db, conversation) == "abandoned"
    assert action_status(db, action_id) == "expired"


def test_handed_off_conversations_wait_for_staff(db: sqlite3.Connection, rules: Rules) -> None:
    conversation, _, _, _ = propose_hold(db)
    db.execute("UPDATE conversations SET status = 'handed_off' WHERE id = ?", (conversation,))
    assert expire_due(db, rules, NOW + timedelta(days=3)).conversations == 0
    assert conv_status(db, conversation) == "handed_off"


def test_running_twice_changes_nothing_more(db: sqlite3.Connection, rules: Rules) -> None:
    conversation, action_id, payload, _ = propose_hold(db)
    confirm(db, rules, action_id, conversation, payload_hash(payload), LATER)
    late = local("2026-12-31T12:00")
    first = expire_due(db, rules, late)
    assert first.holds == 1
    second = expire_due(db, rules, late)
    assert (second.proposals, second.holds, second.conversations) == (0, 0, 0)
