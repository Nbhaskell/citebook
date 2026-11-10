"""verify_customer: booking reference plus the last 4 digits of the phone; code compares.

Every failure gets the same words. After `verification_max_attempts` failures the gate refuses
further attempts (RATE_LIMITED), and the failing result asks the turn loop for a
VERIFICATION_FAILED handoff (origin `loop`).
"""

from __future__ import annotations

from pydantic import Field

from citebook.models import EscalationReason, GateDecision, ToolKind, ToolName
from citebook.tools.base import Block, ToolArgs, ToolContext, ToolOutput, ToolSpec
from citebook.verification import LIMIT_SAY, verify


class Args(ToolArgs):
    reference: str = Field(max_length=20, description="Booking reference, starts with CB-")
    phone_last4: str = Field(max_length=12, description="Last 4 digits of the phone on the booking")


def check_rate(ctx: ToolContext, _args: Args) -> Block | None:
    if ctx.conversation["verification_failures"] >= ctx.settings["verification_max_attempts"]:
        return Block(GateDecision.RATE_LIMITED, LIMIT_SAY, "verification_attempts")
    return None


def handle(ctx: ToolContext, args: Args) -> ToolOutput:
    result = verify(
        ctx.conn,
        ctx.conversation_id,
        args.reference,
        args.phone_last4,
        ctx.settings["verification_max_attempts"],
        ctx.now,
    )
    if result.ok:
        return ToolOutput(
            data={
                "verified": True,
                "booking_reference": args.reference.strip().upper(),
                "next": "Call find_booking to see this booking.",
            }
        )
    data: dict[str, object] = {"verified": False, "say": result.say}
    if result.escalate:
        data["escalate"] = EscalationReason.VERIFICATION_FAILED.value
    return ToolOutput(data=data)


SPEC = ToolSpec(
    name=ToolName.VERIFY_CUSTOMER,
    kind=ToolKind.READ,
    args=Args,
    description=(
        "Check a booking reference (CB-...) and the last 4 digits of the phone number on that "
        "booking. Success opens that one booking for find_booking and changes."
    ),
    handler=handle,
    check_rate=check_rate,
)
