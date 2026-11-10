"""Enums from docs/BACKEND_SCHEMA.md section 2: the single source of truth in code.

tests/test_enums.py keeps these, the schema document and the database CHECK constraints in step.
"""

from enum import StrEnum


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    HANDED_OFF = "handed_off"
    CLOSED = "closed"
    ABANDONED = "abandoned"


class Channel(StrEnum):
    WEB_WIDGET = "web_widget"
    SIMULATOR = "simulator"


class MessageRole(StrEnum):
    CUSTOMER = "customer"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM_NOTE = "system_note"
    STAFF = "staff"


class VerificationState(StrEnum):
    ANONYMOUS = "anonymous"
    CONTACT_COLLECTED = "contact_collected"
    VERIFIED = "verified"


class PricingKind(StrEnum):
    FIXED = "fixed"
    STARTING_PRICE = "starting_price"
    HOURLY = "hourly"
    QUOTE_ONLY = "quote_only"


class ServiceZone(StrEnum):
    CORE = "core"
    OUTER = "outer"


class ActionOrigin(StrEnum):
    TOOL = "tool"
    SCREENING = "screening"
    LOOP = "loop"


class SlotStatus(StrEnum):
    OPEN = "open"
    HELD = "held"
    BOOKED = "booked"
    BLOCKED = "blocked"


class BookingStatus(StrEnum):
    HELD = "held"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"
    EXPIRED = "expired"


class ActionType(StrEnum):
    HOLD_CREATE = "hold_create"
    RESCHEDULE = "reschedule"
    CANCELLATION = "cancellation"
    REFUND_REQUEST = "refund_request"
    POLICY_EXCEPTION = "policy_exception"
    HANDOFF = "handoff"


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    CUSTOMER_CONFIRMED = "customer_confirmed"
    PENDING_STAFF = "pending_staff"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"
    FAILED = "failed"


class ToolName(StrEnum):
    LIST_SERVICES = "list_services"
    CHECK_AVAILABILITY = "check_availability"
    QUOTE_PRICE = "quote_price"
    GET_POLICY = "get_policy"
    COLLECT_CONTACT = "collect_contact"
    VERIFY_CUSTOMER = "verify_customer"
    FIND_BOOKING = "find_booking"
    PROPOSE_HOLD = "propose_hold"
    PROPOSE_RESCHEDULE = "propose_reschedule"
    REQUEST_CANCELLATION = "request_cancellation"
    REQUEST_REFUND = "request_refund"
    REQUEST_EXCEPTION = "request_exception"
    HANDOFF_TO_STAFF = "handoff_to_staff"


class ToolKind(StrEnum):
    READ = "read"
    PROPOSE = "propose"
    REQUEST = "request"


class GateDecision(StrEnum):
    ALLOWED = "ALLOWED"
    NOT_ALLOWED_IN_STATE = "NOT_ALLOWED_IN_STATE"
    NEEDS_VERIFICATION = "NEEDS_VERIFICATION"
    NEEDS_CONTACT = "NEEDS_CONTACT"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    RATE_LIMITED = "RATE_LIMITED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    STAFF_ONLY = "STAFF_ONLY"


class ReplyCheckCode(StrEnum):
    PRICE_FROM_TOOL = "PRICE_FROM_TOOL"
    TIME_FROM_TOOL = "TIME_FROM_TOOL"
    REFERENCE_FROM_TOOL = "REFERENCE_FROM_TOOL"
    POLICY_GROUNDED = "POLICY_GROUNDED"
    NO_FALSE_CONFIRMATION = "NO_FALSE_CONFIRMATION"
    NO_OTHER_CUSTOMER_DATA = "NO_OTHER_CUSTOMER_DATA"
    NO_PROMISED_REFUND = "NO_PROMISED_REFUND"
    SCOPE = "SCOPE"


class ScreenFlag(StrEnum):
    EMERGENCY = "EMERGENCY"
    SUSPICIOUS_INPUT = "SUSPICIOUS_INPUT"
    SENSITIVE_NUMBER = "SENSITIVE_NUMBER"
    ABUSIVE = "ABUSIVE"
    SPAM = "SPAM"


class EscalationReason(StrEnum):
    CUSTOMER_ASKED_FOR_PERSON = "CUSTOMER_ASKED_FOR_PERSON"
    EMERGENCY = "EMERGENCY"
    REFUND = "REFUND"
    LATE_CANCELLATION = "LATE_CANCELLATION"
    LATE_RESCHEDULE = "LATE_RESCHEDULE"
    POLICY_EXCEPTION = "POLICY_EXCEPTION"
    OUT_OF_SERVICE_AREA = "OUT_OF_SERVICE_AREA"
    NOT_IN_KNOWLEDGE_BASE = "NOT_IN_KNOWLEDGE_BASE"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    REPEATED_GATE_BLOCKS = "REPEATED_GATE_BLOCKS"
    REPLY_CHECK_FAILED = "REPLY_CHECK_FAILED"
    SUSPICIOUS_INPUT = "SUSPICIOUS_INPUT"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"


class StaffDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    TAKE_OVER = "take_over"
    CLOSE = "close"


class Provider(StrEnum):
    OLLAMA = "ollama"
    HOSTED = "hosted"


class Actor(StrEnum):
    ASSISTANT = "assistant"
    CUSTOMER = "customer"
    STAFF = "staff"
    SYSTEM = "system"
    SIMULATOR = "simulator"


class OutboxKind(StrEnum):
    HOLD_RECEIVED = "hold_received"
    BOOKING_CONFIRMED = "booking_confirmed"
    HOLD_DECLINED = "hold_declined"
    HOLD_EXPIRED = "hold_expired"
    BOOKING_RESCHEDULED = "booking_rescheduled"
    BOOKING_CANCELLED = "booking_cancelled"
    REQUEST_RECEIVED = "request_received"


class Ablation(StrEnum):
    NONE = "none"
    BASELINE = "baseline"
    NO_LEAST_PRIVILEGE = "no_least_privilege"
    NO_GATE_POLICY = "no_gate_policy"
    NO_DATA_MARKING = "no_data_marking"
    NO_REPLY_CHECKS = "no_reply_checks"
    NO_SCREENING = "no_screening"
