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
