"""list_services: the services, in plain words, with pricing kind and whether bookable online."""

from __future__ import annotations

from typing import Any

from citebook.models import PricingKind, ToolKind, ToolName
from citebook.tools.base import NoArgs, ToolContext, ToolOutput, ToolSpec
from citebook.tools.common import money, price_book


def handle(ctx: ToolContext, _args: NoArgs) -> ToolOutput:
    rows = ctx.conn.execute(
        "SELECT * FROM services WHERE active = 1 ORDER BY bookable_online DESC, code"
    ).fetchall()
    names = {r["code"]: r["name"] for r in rows}
    services: list[dict[str, Any]] = []
    for r in rows:
        kind = PricingKind(r["pricing_kind"])
        entry: dict[str, Any] = {
            "code": r["code"],
            "name": r["name"],
            "description": r["description"],
            "pricing_kind": kind.value,
            "bookable_online": bool(r["bookable_online"]),
        }
        if kind is PricingKind.FIXED:
            entry["price"] = money(r["price_minor"])
        elif kind is PricingKind.STARTING_PRICE:
            entry["price"] = f"from {money(r['price_minor'])}"
        elif kind is PricingKind.HOURLY:
            entry["price"] = f"{money(price_book()['labour_per_hour_minor'])} per hour"
        if r["first_step_code"]:
            entry["book_first"] = {
                "code": r["first_step_code"],
                "name": names.get(r["first_step_code"]),
            }
        services.append(entry)
    return ToolOutput(
        data={
            "services": services,
            "note": "Prices exclude the call-out fee. Use quote_price for a full price.",
        }
    )


SPEC = ToolSpec(
    name=ToolName.LIST_SERVICES,
    kind=ToolKind.READ,
    args=NoArgs,
    description=(
        "List the services the business offers, with their codes, plain descriptions, "
        "pricing kind and whether they can be booked online."
    ),
    handler=handle,
    rate_limit=5,
)
