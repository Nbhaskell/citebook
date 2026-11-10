"""Shared fixtures: a migrated, seeded in-memory database and helpers to set up conversations."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from citebook.actions import Rules
from citebook.db import connect, migrate
from tools.build_data import SEED_DIR
from tools.seed_db import load_seed

TZ = ZoneInfo("America/Los_Angeles")
SETTINGS: dict[str, Any] = json.loads((SEED_DIR / "settings.json").read_text(encoding="utf-8"))


def local(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=TZ)


def seeded(path: str = ":memory:") -> sqlite3.Connection:
    conn = connect(path)
    migrate(conn)
    load_seed(conn)
    return conn


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = seeded()
    yield conn
    conn.close()


@pytest.fixture
def rules() -> Rules:
    return Rules.from_settings(SETTINGS)


def make_conversation(
    conn: sqlite3.Connection,
    *,
    customer_id: str | None = None,
    verified_booking_id: str | None = None,
    now: datetime | None = None,
) -> str:
    """A conversation as the gate would leave it: anonymous, with contact, or verified."""
    conversation_id = str(uuid.uuid4())
    state = (
        "verified" if verified_booking_id else ("contact_collected" if customer_id else "anonymous")
    )
    if verified_booking_id and customer_id is None:
        customer_id = conn.execute(
            "SELECT customer_id FROM bookings WHERE id = ?", (verified_booking_id,)
        ).fetchone()["customer_id"]
    stamp = (
        "2026-11-09T15:00:00Z"
        if now is None
        else now.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    conn.execute(
        "INSERT INTO conversations (id, channel, status, verification_state, customer_id,"
        " verified_booking_id, started_at, last_activity_at)"
        " VALUES (?, 'simulator', 'active', ?, ?, ?, ?, ?)",
        (conversation_id, state, customer_id, verified_booking_id, stamp, stamp),
    )
    return conversation_id


def new_customer(conn: sqlite3.Connection, zip_code: str = "00012") -> str:
    customer_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO customers (id, name, phone, email, address, zip_code, created_at)"
        " VALUES (?, 'Alex Rivera', '555-0150', 'alex.rivera@example.com', '9 Example St',"
        " ?, '2026-11-09T15:00:00Z')",
        (customer_id, zip_code),
    )
    return customer_id


def open_slots(
    conn: sqlite3.Connection, skill: str, after: str = "2026-11-09T15:00:00Z", count: int = 1
) -> list[sqlite3.Row]:
    """The first open slot after `after` (UTC text) of a technician with the skill, and, for
    count > 1, the following windows of the same technician, all open and touching."""
    rows = conn.execute(
        "SELECT s.* FROM slots s JOIN technicians t ON t.id = s.technician_id"
        " WHERE s.status = 'open' AND s.starts_at > ? AND t.skills LIKE ?"
        " ORDER BY s.starts_at, t.name",
        (after, f'%"{skill}"%'),
    ).fetchall()
    for first in rows:
        run = [first]
        while len(run) < count:
            nxt = conn.execute(
                "SELECT * FROM slots WHERE technician_id = ? AND starts_at = ? AND status = 'open'",
                (run[-1]["technician_id"], run[-1]["ends_at"]),
            ).fetchone()
            if nxt is None:
                break
            run.append(nxt)
        if len(run) == count:
            return run
    raise AssertionError("no open slots")


def add_quote(
    conn: sqlite3.Connection,
    conversation_id: str | None,
    service_code: str,
    slot_id: str,
    zip_code: str = "00012",
    total_minor: int = 16500,
) -> str:
    quote_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO quotes (id, conversation_id, service_code, slot_id, zip_code, lines,"
        " total_minor, is_estimate, price_book_version, created_at, expires_at)"
        " VALUES (?, ?, ?, ?, ?, '[]', ?, 0, 'test', '2026-11-09T15:00:00Z',"
        " '2026-11-09T15:30:00Z')",
        (quote_id, conversation_id, service_code, slot_id, zip_code, total_minor),
    )
    return quote_id


def add_tool_call(conn: sqlite3.Connection, conversation_id: str, tool: str) -> str:
    call_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO tool_calls (id, conversation_id, turn, tool, arguments, gate_decision,"
        " created_at) VALUES (?, ?, 1, ?, '{}', 'ALLOWED', '2026-11-09T15:00:00Z')",
        (call_id, conversation_id, tool),
    )
    return call_id


def booking(conn: sqlite3.Connection, reference: str) -> sqlite3.Row:
    row: sqlite3.Row = conn.execute(
        "SELECT * FROM bookings WHERE reference = ?", (reference,)
    ).fetchone()
    return row


def slot_status(conn: sqlite3.Connection, slot_id: str) -> str:
    status: str = conn.execute("SELECT status FROM slots WHERE id = ?", (slot_id,)).fetchone()[0]
    return status


Helper = Callable[..., Any]
