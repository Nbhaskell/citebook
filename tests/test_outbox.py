"""Outbox drafts come from fixed templates, filled by code, and are never sent."""

from __future__ import annotations

import re
import sqlite3
import string
from datetime import timedelta

import pytest

from citebook.actions import Rules, confirm, decide, payload_hash
from citebook.expiry import expire_due
from citebook.models import OutboxKind, StaffDecision
from citebook.outbox import TEMPLATES, render
from tests.conftest import local
from tests.test_actions import NOW, propose_hold

LATER = NOW + timedelta(minutes=5)


def bodies(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["kind"]: r["body"] for r in conn.execute("SELECT kind, body FROM outbox")}


def test_every_outbox_kind_has_one_template() -> None:
    assert set(TEMPLATES) == set(OutboxKind)


@pytest.mark.parametrize("kind", list(OutboxKind))
def test_templates_fill_completely(kind: OutboxKind) -> None:
    names = {f for _, f, _, _ in string.Formatter().parse(TEMPLATES[kind]) if f}
    body = render(kind, {name: f"<{name}>" for name in names})
    assert "{" not in body and "}" not in body
    assert names <= {
        "name",
        "reference",
        "service",
        "window",
        "typical_hours",
        "reason",
        "request",
        "about",
    }


def test_customer_text_in_a_field_is_not_read_as_a_template() -> None:
    body = render(
        OutboxKind.HOLD_DECLINED,
        {
            "name": "{reference}",
            "reference": "CB-7KQ4MX",
            "service": "Drain cleaning",
            "window": "Monday",
            "reason": "{name}",
        },
    )
    assert body.startswith("Hi {reference},") and body.endswith("Reason from our team: {name}")


def test_a_hold_draft_says_held_not_confirmed(db: sqlite3.Connection, rules: Rules) -> None:
    conversation, action_id, payload, _ = propose_hold(db)
    result = confirm(db, rules, action_id, conversation, payload_hash(payload), LATER)
    body = bodies(db)["hold_received"]
    assert result.booking_reference in body
    assert "held, not confirmed yet" in body
    assert "usually within 2 business hours" in body
    assert body.startswith("Hi Alex Rivera,")
    assert re.search(r"Faucet replacement, \w+day \d+ November, arrival ", body)


def test_approval_rejection_and_expiry_drafts(db: sqlite3.Connection, rules: Rules) -> None:
    conversation, action_id, payload, _ = propose_hold(db)
    result = confirm(db, rules, action_id, conversation, payload_hash(payload), LATER)
    decide(db, rules, action_id, StaffDecision.APPROVE, None, LATER)
    assert (
        f"your booking {result.booking_reference} is confirmed" in bodies(db)["booking_confirmed"]
    )

    conversation, action_id, payload, _ = propose_hold(db, after="2026-11-10T16:00:00Z")
    confirm(db, rules, action_id, conversation, payload_hash(payload), LATER)
    decide(db, rules, action_id, StaffDecision.REJECT, "No technician that day", LATER)
    assert bodies(db)["hold_declined"].endswith("Reason from our team: No technician that day")

    conversation, action_id, payload, _ = propose_hold(db, after="2026-11-11T16:00:00Z")
    confirm(db, rules, action_id, conversation, payload_hash(payload), LATER)
    expire_due(db, rules, local("2026-11-10T08:00"), idle_minutes=100_000)
    assert "was not confirmed in time" in bodies(db)["hold_expired"]


def test_the_outbox_module_sends_nothing() -> None:
    from pathlib import Path

    source = Path("citebook/outbox.py").read_text(encoding="utf-8")
    for word in ("smtplib", "socket", "urllib", "http", "requests", "httpx"):
        assert word not in source
