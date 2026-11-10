"""Audit events and structured logs with ids, codes and timings only (rule 7).

Every value is checked before it is written: numbers, booleans, None, and short strings made of
letters, digits and `_ - . :` (ids, enum codes, ISO times). Anything else, such as a sentence, a
name, a phone number with spaces or an amount with a `$`, is refused with ValueError, so message
content cannot reach the audit table or the log even by mistake.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid

from citebook.db import utc_now
from citebook.models import Actor

_SAFE_TEXT = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
# Values made only of safe characters that still identify a customer: a phone number such as
# 555-0142 and a booking reference such as CB-7KQ4MX. Events use row ids instead.
_PERSONAL = re.compile(
    r"^(?:\+?1[-.])?(?:\d{3}[-.])?\d{3}[-.]\d{4}$|^\d{7,11}$|^CB-[A-Z0-9]{6}$", re.IGNORECASE
)
logger = logging.getLogger("citebook")

Value = str | int | float | bool | None


def check_fields(fields: dict[str, Value]) -> dict[str, Value]:
    for key, value in fields.items():
        if not _SAFE_TEXT.match(key):
            raise ValueError(f"unsafe event field name {key!r}")
        if value is None or isinstance(value, bool | int | float):
            continue
        if isinstance(value, str) and _SAFE_TEXT.match(value) and not _PERSONAL.search(value):
            continue
        raise ValueError(f"event field {key!r} holds text that is not an id or a code")
    return fields


def audit(
    conn: sqlite3.Connection,
    actor: Actor,
    event: str,
    *,
    conversation_id: str | None = None,
    action_id: str | None = None,
    **fields: Value,
) -> None:
    """Record an audit event and log the same fields."""
    check_fields({"event": event, "conversation_id": conversation_id, "action_id": action_id})
    payload = check_fields(dict(fields))
    conn.execute(
        "INSERT INTO audit_events (id, at, actor, event, conversation_id, action_id, payload)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            utc_now(),
            actor.value,
            event,
            conversation_id,
            action_id,
            json.dumps(payload, sort_keys=True),
        ),
    )
    log(event, actor=actor.value, conversation_id=conversation_id, action_id=action_id, **payload)


def log(event: str, **fields: Value) -> None:
    """A structured log line: one JSON object of safe fields."""
    safe = check_fields({"event": event, **fields})
    logger.info(json.dumps(safe, sort_keys=True))
