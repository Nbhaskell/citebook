"""request_cancellation: a card for cancelling the verified booking.

Free, and executed by code after Confirm, when the booking is still held or the visit is at least
`free_cancel_hours` away; otherwise staff review it after Confirm (LATE_CANCELLATION).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field

from citebook.models import ActionType, ToolKind, ToolName
from citebook.timeutil import is_late, parse_utc
from citebook.tools.base import Block, ToolArgs, ToolContext, ToolOutput, ToolSpec, blocked
from citebook.tools.common import (
    SAY_NO_VERIFIED_BOOKING,
    arrival,
    card_expiry,
    check_booking_open,
    check_reference_is_verified,
    make_card,
    normal_reference,
    service,
    slot,
    verified_booking,
)


class Args(ToolArgs):
    booking_reference: str = Field(max_length=20, description="The verified booking's reference")
    reason_text: str | None = Field(default=None, max_length=500)


# Words a customer uses to ask for a cancellation. A move ("move", "change", "reschedule") is
# not one: the model was seen to offer a cancellation card when the customer asked to move
# (S-106, S-111, S-136; D-082).
CANCEL_WORDS = re.compile(
    r"\bcancel|\bcall (?:it|this|that|the \w+) off\b|\bno longer need|\bdon'?t need",
    re.IGNORECASE,
)
SAY_CANCEL_NOT_ASKED = (
    "The customer has not asked to cancel. If they want a different time, use check_availability "
    "and propose_reschedule. If it is not clear what they want, ask them."
)


def cancel_not_asked(ctx: ToolContext) -> Block | None:
    """A cancellation card only after the customer asks to cancel in this conversation.

    As with contact details (D-080), a conversation with no customer message (tools driven
    directly) has nothing to compare with."""
    texts = [
        row["content"]
        for row in ctx.conn.execute(
            "SELECT content FROM messages WHERE conversation_id = ? AND role = 'customer'",
            (ctx.conversation_id,),
        )
    ]
    if not texts or any(CANCEL_WORDS.search(text) for text in texts):
        return None
    return blocked(SAY_CANCEL_NOT_ASKED, "cancel_not_asked")


def check_policy(ctx: ToolContext, args: Any) -> Block | None:
    return check_booking_open(ctx, args) or cancel_not_asked(ctx)


def handle(ctx: ToolContext, args: Args) -> ToolOutput:
    booking = verified_booking(ctx)
    if booking is None or booking["reference"] != normal_reference(args.booking_reference):
        return ToolOutput(ok=False, error="NOT_FOUND", say=SAY_NO_VERIFIED_BOOKING)
    row = service(ctx.conn, booking["service_code"])
    first = slot(ctx.conn, booking["slot_id"])
    assert row is not None and first is not None
    parts = [f"Cancel {booking['reference']}, {row['name']}", arrival(ctx.rules.clock, first)]
    hours = ctx.settings["free_cancel_hours"]
    late = is_late(ctx.now, parse_utc(first["starts_at"]), hours)
    if late and booking["status"] != "held":
        parts.append(
            f"Less than {hours} hours before the visit: a team member will review the "
            "cancellation and any late fee."
        )
    else:
        parts.append("No fee: cancelled as soon as you confirm.")
    payload = {"booking_id": booking["id"], "reason_text": args.reason_text}
    return make_card(
        ctx,
        ActionType.CANCELLATION,
        payload,
        " · ".join(parts),
        expires_at=card_expiry(ctx),
        booking_id=booking["id"],
    )


SPEC = ToolSpec(
    name=ToolName.REQUEST_CANCELLATION,
    kind=ToolKind.REQUEST,
    args=Args,
    description=(
        "Show the customer a confirmation card to cancel the verified booking. Late "
        "cancellations go to a team member after Confirm."
    ),
    handler=handle,
    needs_verified=True,
    creates=ActionType.CANCELLATION,
    rate_limit=5,
    check_owner=check_reference_is_verified,
    check_policy=check_policy,
)
