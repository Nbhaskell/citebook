"""expire_due(now): every expiry in one idempotent transaction (TRD section 7.1, D-011).

- Proposals past `expires_at` become `expired`.
- Holds past `bookings.hold_expires_at` with no staff decision: the action and booking become
  `expired`, the slots reopen, and a `hold_expired` draft is written for staff to follow up.
- Conversations idle for `conversation_idle_minutes` while `active` or `awaiting_confirmation`
  become `abandoned`, and their open proposals expire.
Quotes have no status: a quote is expired when `expires_at` has passed, and the gate checks that.
The API calls this at the start of each request and every 60 seconds (Phase 5).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from citebook.actions import Rules, release_hold, set_action_status, settle_conversation
from citebook.db import transaction
from citebook.events import audit
from citebook.models import ActionStatus, Actor, BookingStatus, OutboxKind
from citebook.timeutil import to_utc_text

IDLE_DEFAULT_MINUTES = 60


@dataclass(frozen=True)
class Expired:
    proposals: int
    holds: int
    conversations: int


def expire_due(
    conn: sqlite3.Connection, rules: Rules, now: datetime, idle_minutes: int | None = None
) -> Expired:
    idle = idle_minutes if idle_minutes is not None else _idle_minutes(conn)
    stamp = to_utc_text(now)
    with transaction(conn):
        idle_before = to_utc_text(now - timedelta(minutes=idle))
        abandoned = conn.execute(
            "SELECT id FROM conversations WHERE status IN ('active', 'awaiting_confirmation')"
            " AND last_activity_at <= ?",
            (idle_before,),
        ).fetchall()
        for row in abandoned:
            _expire_proposals(conn, now, "conversation_id = ?", (row["id"],))
            conn.execute(
                "UPDATE conversations SET status = 'abandoned', closed_at = ? WHERE id = ?",
                (stamp, row["id"]),
            )
            audit(conn, Actor.SYSTEM, "conversation_abandoned", conversation_id=row["id"])

        proposals = _expire_proposals(conn, now, "expires_at <= ?", (stamp,))

        holds = conn.execute(
            "SELECT a.id AS action_id, a.conversation_id, b.id AS booking_id FROM actions a"
            " JOIN bookings b ON b.id = a.booking_id"
            " WHERE a.action_type = 'hold_create' AND a.status = 'pending_staff'"
            " AND b.status = 'held' AND b.hold_expires_at <= ?",
            (stamp,),
        ).fetchall()
        for row in holds:
            set_action_status(
                conn, row["action_id"], ActionStatus.PENDING_STAFF, ActionStatus.EXPIRED, now
            )
            release_hold(
                conn, rules, row["booking_id"], BookingStatus.EXPIRED, OutboxKind.HOLD_EXPIRED, now
            )
            audit(
                conn,
                Actor.SYSTEM,
                "hold_expired",
                conversation_id=row["conversation_id"],
                action_id=row["action_id"],
                booking_id=row["booking_id"],
            )
    return Expired(proposals=proposals, holds=len(holds), conversations=len(abandoned))


def _expire_proposals(
    conn: sqlite3.Connection, now: datetime, where: str, args: tuple[str, ...]
) -> int:
    rows = conn.execute(
        f"SELECT id, conversation_id FROM actions WHERE status = 'proposed' AND {where}", args
    ).fetchall()
    for row in rows:
        set_action_status(conn, row["id"], ActionStatus.PROPOSED, ActionStatus.EXPIRED, now)
        settle_conversation(conn, row["conversation_id"], now)
        audit(
            conn,
            Actor.SYSTEM,
            "action_expired",
            conversation_id=row["conversation_id"],
            action_id=row["id"],
        )
    return len(rows)


def _idle_minutes(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'conversation_idle_minutes'"
    ).fetchone()
    return int(row["value"]) if row else IDLE_DEFAULT_MINUTES
