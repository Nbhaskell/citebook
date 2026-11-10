"""Customer verification: booking reference plus the last 4 digits of the booking's phone.

Code compares, never the model. Every failure returns the same words, whether the reference
exists or not, so a guesser learns nothing (TRD section 13). After `max_attempts` failures the
conversation escalates with VERIFICATION_FAILED, and later attempts are refused even with the
right details. Success opens one booking only (D-014).
"""

from __future__ import annotations

import hmac
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from citebook.db import transaction
from citebook.events import audit
from citebook.models import Actor
from citebook.timeutil import to_utc_text

FAILED_SAY = (
    "Those details do not match a booking. Check the reference (it starts with CB-) and the "
    "last 4 digits of the phone number on the booking."
)
LIMIT_SAY = "I couldn't match those details. I've asked a team member to help."
_DUMMY_DIGITS = "\x00\x00\x00\x00"  # compared when the reference is unknown, for even timing


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    say: str | None
    escalate: bool


def _last4(phone: str) -> str:
    return re.sub(r"\D", "", phone)[-4:]


def verify(
    conn: sqlite3.Connection,
    conversation_id: str,
    reference: str,
    phone_last4: str,
    max_attempts: int,
    now: datetime,
) -> VerifyResult:
    ref = reference.strip().upper()
    digits = re.sub(r"\D", "", phone_last4)
    with transaction(conn):
        conversation = conn.execute(
            "SELECT verification_failures FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if conversation["verification_failures"] >= max_attempts:
            audit(conn, Actor.SYSTEM, "verification_refused", conversation_id=conversation_id)
            return VerifyResult(False, LIMIT_SAY, True)

        row = conn.execute(
            "SELECT b.id, b.customer_id, b.status, c.phone FROM bookings b"
            " JOIN customers c ON c.id = b.customer_id WHERE b.reference = ?",
            (ref,),
        ).fetchone()
        expected = _last4(row["phone"]) if row else _DUMMY_DIGITS
        matches = hmac.compare_digest(expected.encode(), digits.encode())
        usable = row is not None and row["status"] in {"held", "confirmed"}

        if matches and usable and len(digits) == 4:
            conn.execute(
                "UPDATE conversations SET verification_state = 'verified', verified_booking_id = ?,"
                " customer_id = ?, last_activity_at = ? WHERE id = ?",
                (row["id"], row["customer_id"], to_utc_text(now), conversation_id),
            )
            audit(conn, Actor.SYSTEM, "verification_succeeded", conversation_id=conversation_id)
            return VerifyResult(True, None, False)

        failures = conversation["verification_failures"] + 1
        conn.execute(
            "UPDATE conversations SET verification_failures = ?, last_activity_at = ? WHERE id = ?",
            (failures, to_utc_text(now), conversation_id),
        )
        audit(
            conn,
            Actor.SYSTEM,
            "verification_failed",
            conversation_id=conversation_id,
            attempt=failures,
        )
        if failures >= max_attempts:
            return VerifyResult(False, LIMIT_SAY, True)
        return VerifyResult(False, FAILED_SAY, False)
