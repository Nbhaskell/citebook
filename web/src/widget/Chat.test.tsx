import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CardOut, TurnOut } from "../api/client";
import { Chat, receiptFor } from "./Chat";

type Call = { url: string; body: unknown };

const CARD: CardOut = {
  action_id: "act-1",
  action_type: "hold_create",
  title: "Request this booking",
  rows: ["Drain cleaning", "Thu 12 Nov, arrival 1–3 PM", "Total $189.00"],
  payload_hash: "a".repeat(64),
  expires_at: null,
};

function turn(partial: Partial<TurnOut>): TurnOut {
  return { replies: [], card: null, slots: [], quote: null, status: "active", ...partial };
}

let calls: Call[] = [];
let replies: Record<string, () => Response>;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  calls = [];
  sessionStorage.clear();
  replies = {
    "/api/conversations": () =>
      json({ conversation_id: "c-1", session_token: "tok", status: "active", synthetic_data: true }),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body ? JSON.parse(init.body as string) : null });
      const handler = replies[url];
      if (!handler) throw new TypeError("network down");
      return handler();
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function say(text: string) {
  const box = screen.getByLabelText("Your message");
  fireEvent.change(box, { target: { value: text } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
  });
}

describe("chat widget", () => {
  it("sends a message and shows the reply", async () => {
    replies["/api/conversations/c-1/messages"] = () =>
      json(turn({ replies: [{ kind: "text", text: "Which drain is it?" }] }));
    render(<Chat />);
    await say("My sink is slow");
    expect(await screen.findByText("Which drain is it?")).toBeInTheDocument();
    expect(screen.getByText("My sink is slow")).toBeInTheDocument();
    expect(calls.at(-1)).toEqual({ url: "/api/conversations/c-1/messages", body: { text: "My sink is slow" } });
    expect(JSON.parse(sessionStorage.getItem("citebook.session") ?? "{}")).toEqual({
      conversationId: "c-1",
      token: "tok",
    });
  });

  it("shows model text literally, never as markup", async () => {
    const markup = '<b>bold</b><img src="x" onerror="alert(1)">';
    replies["/api/conversations/c-1/messages"] = () =>
      json(turn({ replies: [{ kind: "text", text: markup }] }));
    const { container } = render(<Chat />);
    await say("hello");
    expect(await screen.findByText(markup)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });

  it("confirms a card with its hash and shows the receipt", async () => {
    replies["/api/conversations/c-1/messages"] = () =>
      json(turn({ replies: [{ kind: "text", text: "Here is the card." }], card: CARD }));
    replies["/api/actions/act-1/confirm"] = () =>
      json({
        outcome: "pending_staff",
        booking_reference: "CB-7KQ4MX",
        reply: { kind: "receipt", text: "Your booking is held. Reference CB-7KQ4MX." },
        status: "active",
      });
    render(<Chat />);
    await say("Thursday afternoon please");
    const title = await screen.findByRole("heading", { name: "Request this booking" });
    expect(title).toHaveFocus();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    });
    expect(calls.at(-1)).toEqual({
      url: "/api/actions/act-1/confirm",
      body: { payload_hash: "a".repeat(64) },
    });
    expect(await screen.findByText("Held")).toBeInTheDocument();
    expect(screen.getByText("CB-7KQ4MX")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
  });

  it("marks an older open card as replaced when a new one arrives", async () => {
    let second = false;
    replies["/api/conversations/c-1/messages"] = () => {
      const card = second ? { ...CARD, action_id: "act-2", title: "Request this booking" } : CARD;
      second = true;
      return json(turn({ card }));
    };
    render(<Chat />);
    await say("first");
    await say("second");
    expect(await screen.findByText("Replaced by a newer card.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Confirm" })).toHaveLength(1);
  });

  it("shows an emergency as an alert with the phone link", async () => {
    replies["/api/conversations/c-1/messages"] = () =>
      json(
        turn({
          replies: [
            {
              kind: "emergency",
              text: "Turn off the main water valve.\nA team member has been alerted.",
            },
          ],
          status: "handed_off",
        }),
      );
    render(<Chat />);
    await say("Water is pouring from the ceiling");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Turn off the main water valve.");
    expect(screen.getByRole("link", { name: "Call 555-0172 now" })).toHaveAttribute(
      "href",
      "tel:555-0172",
    );
    expect(screen.getByText("You're now talking with our team. They'll reply here.")).toBeInTheDocument();
  });

  it("offers chips from the slots and sends the chosen time", async () => {
    replies["/api/conversations/c-1/messages"] = () =>
      json(turn({ slots: [{ slot_id: "s1", when: "Thu 12 Nov, arrival 1–3 PM" }] }));
    render(<Chat />);
    await say("When are you free?");
    const chip = await screen.findByRole("button", { name: "Thu 12 Nov, arrival 1–3 PM" });
    await act(async () => {
      fireEvent.click(chip);
    });
    expect(calls.at(-1)?.body).toEqual({ text: "Thu 12 Nov, arrival 1–3 PM" });
    expect(screen.getByLabelText("Your message")).toHaveFocus();
  });

  it("says when the service cannot be reached and offers to try again", async () => {
    render(<Chat />);
    replies = {};
    await say("hello");
    expect(
      await screen.findByText(/We couldn't reach the booking assistant. Your message was not sent./),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("restores the conversation of this tab from the transcript", async () => {
    sessionStorage.setItem("citebook.session", JSON.stringify({ conversationId: "c-1", token: "tok" }));
    replies["/api/conversations/c-1"] = () =>
      json({
        status: "handed_off",
        card: null,
        messages: [
          { role: "customer", text: "Earlier question", at: "2026-11-09T16:00:00Z" },
          { role: "staff", text: "Hi, this is Kenji.", at: "2026-11-09T16:01:00Z" },
        ],
      });
    render(<Chat />);
    await waitFor(() => expect(screen.getByText("Hi, this is Kenji.")).toBeInTheDocument());
    expect(screen.getByText("Earlier question")).toBeInTheDocument();
    expect(screen.getByText("Team member")).toBeInTheDocument();
  });
});

describe("receipt words", () => {
  it("names each outcome with a distinct word", () => {
    expect(receiptFor("hold_create", "pending_staff", "CB-1").word).toBe("Held");
    expect(receiptFor("reschedule", "executed", "CB-1").word).toBe("Confirmed");
    expect(receiptFor("cancellation", "executed", "CB-1").word).toBe("Cancelled");
    expect(receiptFor("refund_request", "pending_staff", null).word).toBe("Requested");
  });
});
