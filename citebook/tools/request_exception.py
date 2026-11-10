"""request_exception: a card that sends a request outside policy to staff."""

from __future__ import annotations

from pydantic import Field

from citebook.models import ActionType, ToolKind, ToolName
from citebook.tools.base import ToolArgs, ToolContext, ToolOutput, ToolSpec
from citebook.tools.common import card_expiry, make_card, verified_booking


class Args(ToolArgs):
    details: str = Field(min_length=1, max_length=500, description="What the customer asks for")


def handle(ctx: ToolContext, args: Args) -> ToolOutput:
    booking = verified_booking(ctx) if ctx.conversation["verified_booking_id"] else None
    booking_id = booking["id"] if booking is not None else None
    summary = (
        "Request for an exception · A team member will review it. Nothing changes until they reply."
    )
    payload = {
        "customer_id": ctx.conversation["customer_id"],
        "booking_id": booking_id,
        "details": args.details,
    }
    return make_card(
        ctx,
        ActionType.POLICY_EXCEPTION,
        payload,
        summary,
        expires_at=card_expiry(ctx),
        booking_id=booking_id,
    )


SPEC = ToolSpec(
    name=ToolName.REQUEST_EXCEPTION,
    kind=ToolKind.REQUEST,
    args=Args,
    description=(
        "Show the customer a confirmation card that sends a request outside policy (for "
        "example a third move of a booking) to a team member. Never promise the outcome."
    ),
    handler=handle,
    needs_contact=True,
    creates=ActionType.POLICY_EXCEPTION,
    rate_limit=5,
)
