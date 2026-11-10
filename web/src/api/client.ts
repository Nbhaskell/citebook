import type { components } from "./schema";

type S = components["schemas"];
export type Status = S["Status"];
export type TurnOut = S["TurnOut"];
export type CardOut = S["CardOut"];
export type ReplyOut = S["ReplyOut"];
export type SlotOut = S["SlotOut"];
export type QuoteOut = S["QuoteOut"];
export type ConfirmOut = S["ConfirmOut"];
export type CancelOut = S["CancelOut"];
export type TranscriptOut = S["TranscriptOut"];
export type Queue = S["Queue"];
export type QueueItem = S["QueueItem"];
export type ConversationDetail = S["ConversationDetail"];
export type MessageOut = S["MessageOut"];
export type ActionOut = S["ActionOut"];
export type Calendar = S["Calendar"];
export type CalendarSlot = S["CalendarSlot"];
export type DraftOut = S["DraftOut"];
export type DecisionOut = S["DecisionOut"];
export type ConversationState = S["ConversationState"];

export type StatusResult =
  | { kind: "ok"; status: Status }
  | { kind: "error"; httpStatus: number }
  | { kind: "unreachable" };

/** A failed call: `code` is the API's error code, or NETWORK when the API was not reached. */
export class ApiFailure extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly httpStatus: number,
    readonly requestId: string | null,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["X-Session-Token"] = token;
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers: { ...headers, ...init.headers } });
  } catch {
    throw new ApiFailure("NETWORK", "The service could not be reached.", 0, null);
  }
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = (body as { error?: { code: string; message: string; request_id: string } })
      ?.error;
    throw new ApiFailure(
      error?.code ?? "HTTP_ERROR",
      error?.message ?? `The service answered ${response.status}.`,
      response.status,
      error?.request_id ?? null,
    );
  }
  return body as T;
}

const post = (body?: unknown): RequestInit => ({
  method: "POST",
  body: body === undefined ? undefined : JSON.stringify(body),
});

export async function fetchStatus(signal?: AbortSignal): Promise<StatusResult> {
  let response: Response;
  try {
    response = await fetch("/api/status", { signal });
  } catch (error) {
    if (signal?.aborted) throw error;
    return { kind: "unreachable" };
  }
  if (!response.ok) return { kind: "error", httpStatus: response.status };
  return { kind: "ok", status: (await response.json()) as Status };
}

// --- customer -----------------------------------------------------------------------------------

export const customerApi = {
  start: () =>
    call<S["ConversationCreated"]>("/api/conversations", post({ channel: "web_widget" })),
  send: (conversationId: string, token: string, text: string) =>
    call<TurnOut>(`/api/conversations/${conversationId}/messages`, post({ text }), token),
  transcript: (conversationId: string, token: string) =>
    call<TranscriptOut>(`/api/conversations/${conversationId}`, {}, token),
  confirm: (actionId: string, token: string, payloadHash: string) =>
    call<ConfirmOut>(`/api/actions/${actionId}/confirm`, post({ payload_hash: payloadHash }), token),
  cancel: (actionId: string, token: string) =>
    call<CancelOut>(`/api/actions/${actionId}/cancel`, post(), token),
};

// --- staff --------------------------------------------------------------------------------------

export const staffApi = {
  queue: () => call<Queue>("/api/staff/queue"),
  conversation: (id: string) => call<ConversationDetail>(`/api/staff/conversations/${id}`),
  decide: (actionId: string, decision: "approve" | "reject", reason?: string, reviewSeconds?: number) =>
    call<DecisionOut>(
      `/api/staff/actions/${actionId}/decide`,
      post({ decision, reason: reason || null, review_seconds: reviewSeconds ?? null }),
    ),
  takeOver: (id: string) =>
    call<ConversationState>(`/api/staff/conversations/${id}/take-over`, post()),
  close: (id: string) => call<ConversationState>(`/api/staff/conversations/${id}/close`, post()),
  message: (id: string, text: string) =>
    call<ConversationState>(`/api/staff/conversations/${id}/messages`, post({ text })),
  calendar: (from: string | null, days: number) =>
    call<Calendar>(`/api/staff/calendar?${new URLSearchParams(from ? { from, days: String(days) } : { days: String(days) })}`),
  outbox: () => call<DraftOut[]>("/api/staff/outbox"),
};
