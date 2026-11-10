"""collect_contact: name, phone, optional email, service address and ZIP code for this conversation.

Creates a customer for this conversation, or updates the one it already has. There is no lookup
of existing customers by phone, which would let a caller enumerate customers (TRD section 13).
"""

from __future__ import annotations

import re
import uuid

from pydantic import Field, field_validator

from citebook.models import ToolKind, ToolName
from citebook.timeutil import to_utc_text
from citebook.tools.base import Block, ToolArgs, ToolContext, ToolOutput, ToolSpec, blocked
from citebook.tools.common import SAY_OUT_OF_AREA

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Args(ToolArgs):
    name: str = Field(min_length=1, max_length=80)
    phone: str = Field(max_length=20, description="Phone number, for example 555-0142")
    email: str | None = Field(default=None, max_length=120)
    address: str = Field(min_length=3, max_length=200, description="Street address of the job")
    zip_code: str = Field(pattern=r"^\d{5}$")

    @field_validator("phone")
    @classmethod
    def phone_format(cls, value: str) -> str:
        if re.search(r"[^\d\s().+-]", value):
            raise ValueError("a phone number has digits only")
        digits = re.sub(r"\D", "", value)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) == 7:
            return f"{digits[:3]}-{digits[3:]}"
        if len(digits) == 10:
            return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
        raise ValueError("a phone number has 7 or 10 digits")

    @field_validator("email")
    @classmethod
    def email_format(cls, value: str | None) -> str | None:
        if value is not None and not EMAIL.match(value):
            raise ValueError("not an email address")
        return value

    @field_validator("name")
    @classmethod
    def name_has_letters(cls, value: str) -> str:
        if not re.search(r"[^\W\d_]", value):
            raise ValueError("a name has letters")
        return value


def check_policy(ctx: ToolContext, args: Args) -> Block | None:
    if ctx.conversation["verification_state"] == "verified":
        return blocked(
            "Contact details already come from the verified booking. Do not collect them again.",
            "already_verified",
        )
    if ctx.conversation["status"] == "awaiting_confirmation":
        return blocked(
            "A card is waiting for the customer. Ask them to press Cancel on it before you "
            "change the contact details.",
            "card_open",
        )
    if args.zip_code not in ctx.settings["zip_areas"]:
        return blocked(SAY_OUT_OF_AREA, "out_of_area")
    return None


def handle(ctx: ToolContext, args: Args) -> ToolOutput:
    customer_id = ctx.conversation["customer_id"]
    values = (args.name, args.phone, args.email, args.address, args.zip_code)
    if customer_id:
        ctx.conn.execute(
            "UPDATE customers SET name = ?, phone = ?, email = ?, address = ?, zip_code = ?"
            " WHERE id = ?",
            (*values, customer_id),
        )
    else:
        customer_id = str(uuid.uuid4())
        ctx.conn.execute(
            "INSERT INTO customers (id, name, phone, email, address, zip_code, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (customer_id, *values, to_utc_text(ctx.now)),
        )
    ctx.conn.execute(
        "UPDATE conversations SET customer_id = ?, verification_state = 'contact_collected',"
        " last_activity_at = ? WHERE id = ?",
        (customer_id, to_utc_text(ctx.now), ctx.conversation_id),
    )
    area = ctx.settings["zip_areas"][args.zip_code]
    return ToolOutput(data={"saved": True, "area": area["area"], "zone": area["zone"]})


SPEC = ToolSpec(
    name=ToolName.COLLECT_CONTACT,
    kind=ToolKind.READ,
    args=Args,
    description=(
        "Save the customer's name, phone number, optional email, service address and ZIP code "
        "for this conversation. Needed before propose_hold."
    ),
    handler=handle,
    rate_limit=5,
    check_policy=check_policy,
)
