"""Quotes computed from the price book. The model never does arithmetic (rule 3).

Rules (docs/DECISIONS.md D-001, D-002, D-003, D-037, D-046):
- A call-out fee line, then the job line: the fixed or starting price, or labour for the minimum
  hours of an hourly service (parts extra).
- The standard call-out fee is credited back on a negative line, because the business credits it
  when the customer approves the work on the same visit.
- A visit outside the published visit windows pays the emergency call-out fee instead, with no
  credit line. Online slots are always inside the windows, so online quotes never pay it.
- An outer-zone ZIP code adds the travel fee; a ZIP code outside the map is refused.
- Quote-only services are refused and name their bookable first step, if any.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from citebook.models import PricingKind, ServiceZone
from citebook.money import format_money, multiply
from citebook.timeutil import BusinessClock


class OutOfServiceArea(Exception):
    """The ZIP code is not in the service area map."""


class NotQuotable(Exception):
    """The service is priced by quote; `first_step_code` names a bookable first step, if any."""

    def __init__(self, service_code: str, first_step_code: str | None) -> None:
        super().__init__(f"{service_code} is priced by quote")
        self.service_code = service_code
        self.first_step_code = first_step_code


@dataclass(frozen=True)
class Quote:
    service_code: str
    zip_code: str
    slot_start: datetime | None
    lines: list[tuple[str, int]]
    total_minor: int
    is_estimate: bool
    estimate_note: str
    bookable_online: bool
    price_book_version: str
    notes: list[str] = field(default_factory=list)


def _in_visit_hours(slot_start: datetime, clock: BusinessClock) -> bool:
    local = clock.local(slot_start)
    return any(start == local for start, _ in clock.visit_windows(local.date()))


def build_quote(
    service: dict[str, Any],
    zip_code: str,
    slot_start: datetime | None,
    book: dict[str, Any],
    zip_areas: dict[str, dict[str, str]],
    clock: BusinessClock,
) -> Quote:
    kind = PricingKind(service["pricing_kind"])
    if kind is PricingKind.QUOTE_ONLY:
        raise NotQuotable(service["code"], service.get("first_step_code"))
    area = zip_areas.get(zip_code)
    if area is None:
        raise OutOfServiceArea(zip_code)

    lines: list[tuple[str, int]] = []
    emergency = slot_start is not None and not _in_visit_hours(slot_start, clock)
    call_out = book["call_out_fee_minor"]
    if emergency:
        lines.append(("Emergency call-out fee", book["emergency_call_out_fee_minor"]))
    else:
        lines.append(("Call-out fee", call_out))

    if kind is PricingKind.HOURLY:
        hours = Decimal(service["min_hours"])
        unit = "hour" if hours == 1 else "hours"
        lines.append(
            (
                f"Labour, minimum {hours.normalize()} {unit}",
                multiply(book["labour_per_hour_minor"], hours),
            )
        )
        note = "Labour for the minimum time. Parts are extra and priced on a written estimate."
    elif kind is PricingKind.STARTING_PRICE:
        lines.append((f"{service['name']} (starting price)", service["price_minor"]))
        note = "Starting price: final price confirmed on site."
    else:
        lines.append((service["name"], service["price_minor"]))
        note = ""

    if not emergency:
        lines.append(("Call-out fee credited when you approve the work", -call_out))
    if ServiceZone(area["zone"]) is ServiceZone.OUTER:
        lines.append((f"Travel fee ({area['area']})", book["outer_travel_fee_minor"]))

    notes = []
    if not emergency:
        notes.append(
            f"If you decline the work on the day, the {format_money(call_out)} "
            "call-out fee applies."
        )
    if not service["bookable_online"]:
        limit = format_money(book["written_estimate_required_over_minor"])
        notes.append(
            f"Jobs over {limit} need a written estimate before work starts, "
            "so a team member arranges this booking."
        )
    return Quote(
        service_code=service["code"],
        zip_code=zip_code,
        slot_start=slot_start,
        lines=lines,
        total_minor=sum(amount for _, amount in lines),
        is_estimate=kind in {PricingKind.STARTING_PRICE, PricingKind.HOURLY},
        estimate_note=note,
        bookable_online=bool(service["bookable_online"]),
        price_book_version=book["version"],
        notes=notes,
    )
