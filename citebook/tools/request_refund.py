"""request_refund: a card that sends a refund request to staff. Citebook never pays money."""

from __future__ import annotations

from pydantic import Field

from citebook.models import ActionType, ToolKind, ToolName
from citebook.tools.base import ToolArgs, ToolContext, ToolOutput, ToolSpec
from citebook.tools.common import (
    SAY_NO_VERIFIED_BOOKING,
    card_expiry,
    check_reference_is_verified,
    make_card,
    normal_reference,
    service,
    verified_booking,
)


class Args(ToolArgs):
    booking_reference: str = Field(max_length=20, description="The verified booking's reference")
    reason_text: str = Field(min_length=1, max_length=500)


def handle(ctx: ToolContext, args: Args) -> ToolOutput:
    booking = verified_booking(ctx)
    if booking is None or booking["reference"] != normal_reference(args.booking_reference):
        return ToolOutput(ok=False, error="NOT_FOUND", say=SAY_NO_VERIFIED_BOOKING)
    row = service(ctx.conn, booking["service_code"])
    assert row is not None
    summary = " · ".join(
        [
            f"Refund request for {booking['reference']}, {row['name']}",
            "A team member will review it. The chat cannot give a refund.",
        ]
    )
    payload = {"booking_id": booking["id"], "reason_text": args.reason_text}
    return make_card(
        ctx,
        ActionType.REFUND_REQUEST,
        payload,
        summary,
        expires_at=card_expiry(ctx),
        booking_id=booking["id"],
    )


SPEC = ToolSpec(
    name=ToolName.REQUEST_REFUND,
    kind=ToolKind.REQUEST,
    args=Args,
    description=(
        "Show the customer a confirmation card that sends a refund request about the verified "
        "booking to a team member. Never promise a refund."
    ),
    handler=handle,
    needs_verified=True,
    creates=ActionType.REFUND_REQUEST,
    rate_limit=5,
    check_owner=check_reference_is_verified,
)
