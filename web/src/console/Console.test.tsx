import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConversationDetail, Queue, QueueItem } from "../api/client";
import { ConsoleApp, parseRoute } from "./ConsoleApp";

const HOLD: QueueItem = {
  action_id: "act-1",
  conversation_id: "conv-1",
  action_type: "hold_create",
  status: "pending_staff",
  reason: null,
  origin: "customer",
  summary: "Drain cleaning · Thu 12 Nov, arrival 1–3 PM · Total $189.00",
  customer_name: "Mara Quill",
  booking_reference: "CB-7KQ4MX",
  visit: "Thu 12 Nov, arrival 1–3 PM",
  total: "$189.00",
  created_at: "2026-11-09T15:00:00Z",
  updated_at: "2026-11-09T15:00:00Z",
  hold_expires_at: "2026-11-09T19:00:00Z",
};

const QUEUE: Queue = {
  needs_decision: [HOLD, { ...HOLD, action_id: "act-2", action_type: "refund_request", reason: "REFUND", customer_name: "Ivo Brandt", total: null, visit: null }],
  handoffs: [],
  expired_holds: [],
  recent: [],
};

const DETAIL: ConversationDetail = {
  conversation_id: "conv-1",
  status: "awaiting_confirmation",
  channel: "web_widget",
  verification_state: "contact_collected",
  verified_booking_reference: null,
  customer: { name: "Mara Quill", phone: "555-0101", email: null, address: "4 Fern Row", zip_code: "00012" },
  started_at: "2026-11-09T14:55:00Z",
  last_activity_at: "2026-11-09T15:00:00Z",
  messages: [
    { seq: 1, role: "customer", text: "Book a drain cleaning", at: "2026-11-09T14:55:00Z", flags: [], checks: [] },
    {
      seq: 2,
      role: "tool",
      text: "find_booking",
      at: "2026-11-09T14:56:00Z",
      flags: [],
      checks: [],
      tool: {
        name: "find_booking",
        arguments: { reference: "CB-AAAAAA" },
        ok: false,
        gate_decision: "NEEDS_VERIFICATION",
        say: null,
        result: { ok: false },
      },
    },
    {
      seq: 3,
      role: "assistant",
      text: "Here is the card.",
      at: "2026-11-09T14:57:00Z",
      flags: [],
      checks: [
        { attempt: 1, code: "PRICE_FROM_TOOL", passed: false, detail: {} },
        { attempt: 2, code: "PRICE_FROM_TOOL", passed: true, detail: {} },
      ],
    },
  ],
  actions: [
    {
      action_id: "act-1",
      action_type: "hold_create",
      status: "pending_staff",
      origin: "customer",
      reason: null,
      summary: HOLD.summary,
      booking_reference: "CB-7KQ4MX",
      created_at: "2026-11-09T14:58:00Z",
      updated_at: "2026-11-09T15:00:00Z",
      failure_detail: null,
    },
  ],
};

type Call = { url: string; body: unknown };
let calls: Call[];

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body ? JSON.parse(init.body as string) : null });
      if (url === "/api/staff/queue") return Response.json(QUEUE);
      if (url === "/api/staff/conversations/conv-1") return Response.json(DETAIL);
      if (url === "/api/staff/actions/act-1/decide")
        return Response.json({ outcome: "approved", action_status: "approved" });
      return new Promise<Response>(() => {});
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("console", () => {
  it("parses hash routes", () => {
    expect(parseRoute("")).toEqual({ view: "queue", tab: "needs_decision" });
    expect(parseRoute("#/queue/handoffs")).toEqual({ view: "queue", tab: "handoffs" });
    expect(parseRoute("#/conversation/abc")).toEqual({ view: "conversation", id: "abc" });
    expect(parseRoute("#/outbox")).toEqual({ view: "outbox" });
  });

  it("lists queue rows with type word, customer, visit, total and reason", async () => {
    render(<ConsoleApp />);
    const link = await screen.findByRole("link", { name: "Mara Quill" });
    const row = link.closest("tr") as HTMLElement;
    expect(within(row).getByText("Hold")).toBeInTheDocument();
    expect(within(row).getByText("Thu 12 Nov, arrival 1–3 PM")).toBeInTheDocument();
    expect(within(row).getByText("$189.00")).toBeInTheDocument();
    expect(within(row).getByText("New booking to confirm")).toBeInTheDocument();
    expect(within(row).getByText("Needs decision")).toBeInTheDocument();
    const refund = (await screen.findByRole("link", { name: "Ivo Brandt" })).closest("tr") as HTMLElement;
    expect(within(refund).getByText("Refund request", { selector: "td:nth-child(6)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Needs decision/ })).toHaveAttribute("aria-selected", "true");
  });

  it("shows gate decisions and reply checks in words in the activity timeline", async () => {
    window.location.hash = "#/conversation/conv-1";
    render(<ConsoleApp />);
    expect(await screen.findByText("Blocked: needs verification")).toBeInTheDocument();
    expect(screen.getByText("Draft 2 (regenerated):")).toBeInTheDocument();
    expect(screen.getByText("1 failed")).toBeInTheDocument();
    expect(screen.getAllByText("Prices from tools")).toHaveLength(2);
  });

  it("refuses to reject without a reason, then sends the reason", async () => {
    window.location.hash = "#/conversation/conv-1";
    render(<ConsoleApp />);
    fireEvent.click(await screen.findByRole("button", { name: "Reject…" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Reject hold" }));
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Give a reason");
    expect(calls.some((call) => call.url.endsWith("/decide"))).toBe(false);
    const box = screen.getByLabelText("Reason for rejecting (required)");
    expect(box).toHaveAttribute("aria-invalid", "true");
    fireEvent.click(screen.getByRole("button", { name: "Needs a site visit first" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Reject hold" }));
    });
    const decide = calls.find((call) => call.url.endsWith("/decide"));
    expect(decide?.body).toMatchObject({ decision: "reject", reason: "Needs a site visit first" });
  });

  it("offers take-over, not approve or reject, for a handoff", async () => {
    const handoff = {
      ...DETAIL.actions[0],
      action_id: "act-h",
      action_type: "handoff",
      reason: "CUSTOMER_ASKED_FOR_PERSON",
      summary: "Conversation handed to the team",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ ...DETAIL, status: "handed_off", actions: [handoff] })),
    );
    window.location.hash = "#/conversation/conv-1";
    render(<ConsoleApp />);
    expect(await screen.findByRole("button", { name: "Take over" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject…" })).toBeNull();
    expect(screen.getByText(/Customer asked for a person/)).toBeInTheDocument();
  });

  it("approves a hold and says that the notification is only a draft", async () => {
    window.location.hash = "#/conversation/conv-1";
    render(<ConsoleApp />);
    await act(async () => {
      fireEvent.click(await screen.findByRole("button", { name: "Approve hold" }));
    });
    expect(calls.find((call) => call.url.endsWith("/decide"))?.body).toMatchObject({ decision: "approve" });
    expect(await screen.findByText(/Hold approved. A notification draft is in the outbox, not sent./)).toBeInTheDocument();
  });
});
