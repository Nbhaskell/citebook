"""find_booking: the one verified booking. Booking notes are customer text and marked untrusted."""

from __future__ import annotations

from citebook.models import ToolKind, ToolName
from citebook.money import format_money
from citebook.tools.base import NoArgs, ToolContext, ToolOutput, ToolSpec
from citebook.tools.common import SAY_NO_VERIFIED_BOOKING, arrival, verified_booking


def handle(ctx: ToolContext, _args: NoArgs) -> ToolOutput:
    booking = verified_booking(ctx)
    if booking is None:
        return ToolOutput(ok=False, error="NOT_FOUND", say=SAY_NO_VERIFIED_BOOKING)
    row = ctx.conn.execute(
        "SELECT v.name, q.total_minor FROM services v, quotes q WHERE v.code = ? AND q.id = ?",
        (booking["service_code"], booking["quote_id"]),
    ).fetchone()
    first = ctx.conn.execute("SELECT * FROM slots WHERE id = ?", (booking["slot_id"],)).fetchone()
    data: dict[str, object] = {
        "booking_reference": booking["reference"],
        "service": row["name"],
        "when": arrival(ctx.rules.clock, first),
        "status": booking["status"],
        "total": format_money(row["total_minor"]),
        "moves_left_in_chat": max(0, ctx.settings["max_reschedules"] - booking["reschedule_count"]),
    }
    untrusted: list[str] = []
    if booking["customer_notes"]:
        data["customer_notes"] = booking["customer_notes"]
        untrusted.append("customer_notes")
    return ToolOutput(data=data, untrusted_fields=untrusted)


SPEC = ToolSpec(
    name=ToolName.FIND_BOOKING,
    kind=ToolKind.READ,
    args=NoArgs,
    description="Show the booking this conversation verified with verify_customer.",
    handler=handle,
    needs_verified=True,
)
