import type { IconName } from "./Icon";

// Plain words for the codes in docs/BACKEND_SCHEMA.md (UI brief sections 3 to 5).

export const GATE_LABELS: Record<string, string> = {
  ALLOWED: "Allowed",
  NOT_ALLOWED_IN_STATE: "Blocked: not allowed now",
  NEEDS_VERIFICATION: "Blocked: needs verification",
  NEEDS_CONTACT: "Blocked: needs contact details",
  INVALID_ARGUMENTS: "Blocked: invalid details",
  UNKNOWN_TOOL: "Blocked: unknown tool",
  RATE_LIMITED: "Blocked: too many attempts",
  POLICY_BLOCKED: "Blocked: policy",
  STAFF_ONLY: "Blocked: staff only",
};

export const REASON_LABELS: Record<string, string> = {
  CUSTOMER_ASKED_FOR_PERSON: "Customer asked for a person",
  EMERGENCY: "Emergency",
  REFUND: "Refund request",
  LATE_CANCELLATION: "Late cancellation",
  LATE_RESCHEDULE: "Late move",
  POLICY_EXCEPTION: "Request outside policy",
  OUT_OF_SERVICE_AREA: "Outside service area",
  NOT_IN_KNOWLEDGE_BASE: "Not in knowledge base",
  VERIFICATION_FAILED: "Verification failed",
  REPEATED_GATE_BLOCKS: "Repeated blocked actions",
  REPLY_CHECK_FAILED: "Reply failed checks",
  SUSPICIOUS_INPUT: "Suspicious input",
  MODEL_UNAVAILABLE: "Model unavailable",
};

export const ACTION_LABELS: Record<string, { word: string; icon: IconName }> = {
  hold_create: { word: "Hold", icon: "clock" },
  reschedule: { word: "Move", icon: "move" },
  cancellation: { word: "Cancellation", icon: "cross" },
  refund_request: { word: "Refund request", icon: "undo" },
  policy_exception: { word: "Exception", icon: "question" },
  handoff: { word: "Handoff", icon: "person" },
};

export const CHECK_LABELS: Record<string, string> = {
  PRICE_FROM_TOOL: "Prices from tools",
  TIME_FROM_TOOL: "Times from tools",
  REFERENCE_FROM_TOOL: "References belong to customer",
  POLICY_GROUNDED: "Policy figures grounded",
  NO_FALSE_CONFIRMATION: "No false confirmation",
  NO_OTHER_CUSTOMER_DATA: "No other customer's data",
  NO_PROMISED_REFUND: "No promised refund",
  SCOPE: "In scope",
};

/** "2026-11-09T16:00:00Z" as "Mon 9 Nov, 8:00 AM" in the business time zone. */
export function businessTime(utc: string): string {
  return new Date(utc).toLocaleString("en-US", {
    timeZone: "America/Los_Angeles",
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function age(utc: string, now: Date = new Date()): string {
  const minutes = Math.max(0, Math.round((now.getTime() - new Date(utc).getTime()) / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.floor(hours / 24)} d`;
}

export const STATUS_LABELS: Record<string, { word: string; icon: IconName; tone: Tone }> = {
  proposed: { word: "Waiting for customer", icon: "clock", tone: "neutral" },
  customer_confirmed: { word: "Customer confirmed", icon: "check", tone: "neutral" },
  pending_staff: { word: "Needs decision", icon: "inbox", tone: "warn" },
  approved: { word: "Approved", icon: "check", tone: "ok" },
  rejected: { word: "Rejected", icon: "cross", tone: "bad" },
  executed: { word: "Done", icon: "check", tone: "ok" },
  expired: { word: "Expired", icon: "clock", tone: "bad" },
  withdrawn: { word: "Withdrawn by customer", icon: "undo", tone: "neutral" },
  failed: { word: "Failed", icon: "cross", tone: "bad" },
};

export type Tone = "ok" | "warn" | "bad" | "neutral";

export const SLOT_LABELS: Record<string, { word: string; icon: IconName; tone: Tone }> = {
  open: { word: "Open", icon: "check", tone: "ok" },
  held: { word: "Held", icon: "clock", tone: "warn" },
  booked: { word: "Booked", icon: "person", tone: "neutral" },
  blocked: { word: "Blocked", icon: "cross", tone: "bad" },
};

export const OUTBOX_LABELS: Record<string, string> = {
  hold_received: "Hold received",
  booking_confirmed: "Booking confirmed",
  hold_declined: "Hold declined",
  hold_expired: "Hold expired",
  booking_rescheduled: "Booking moved",
  booking_cancelled: "Booking cancelled",
  request_received: "Request received",
};

export const CONVERSATION_LABELS: Record<string, string> = {
  active: "Bot answering",
  awaiting_confirmation: "Card open",
  handed_off: "With staff",
  closed: "Closed",
  abandoned: "Abandoned",
};

const TZ = "America/Los_Angeles";

/** "8:00 AM" in the business time zone. */
export function businessClock(utc: string): string {
  return new Date(utc).toLocaleTimeString("en-US", { timeZone: TZ, hour: "numeric", minute: "2-digit" });
}

/** "Mon 9 Nov" in the business time zone. */
export function businessDay(utc: string): string {
  return new Date(utc).toLocaleDateString("en-GB", {
    timeZone: TZ,
    weekday: "short",
    day: "numeric",
    month: "short",
  }).replace(",", "");
}

/** The business-time date as "2026-11-09", for grouping. */
export function businessDate(utc: string): string {
  return new Date(utc).toLocaleDateString("en-CA", { timeZone: TZ });
}
