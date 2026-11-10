import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  ApiFailure,
  customerApi,
  type CardOut,
  type ConfirmOut,
  type QuoteOut,
  type SlotOut,
  type TurnOut,
} from "../api/client";
import { Icon } from "../shared/Icon";
import { ConfirmCard, type CardState, type Receipt } from "./ConfirmCard";
import { QuoteTable } from "./QuoteTable";

// The customer chat (UI brief section 3). Every booking change is a card built by code; the
// model's text is shown as plain text, never as HTML.

export const BUSINESS = {
  name: "Kestrel Hollow Plumbing",
  phone: "555-0171",
  emergencyPhone: "555-0172",
};

const SESSION_KEY = "citebook.session";
const SLOW_AFTER_MS = 6000;
const POLL_MS = 5000;
const PERSON_TEXT = "I'd like to talk to a person, please.";
const STARTERS = ["Book a drain cleaning", "What does a visit cost?", "Change my booking"];

type Session = { conversationId: string; token: string };

type Item =
  | { id: number; kind: "customer"; text: string }
  | { id: number; kind: "assistant"; text: string; tone: string }
  | { id: number; kind: "staff"; text: string }
  | { id: number; kind: "emergency"; text: string }
  | { id: number; kind: "slots"; slots: SlotOut[] }
  | { id: number; kind: "quote"; quote: QuoteOut }
  | {
      id: number;
      kind: "card";
      card: CardOut;
      state: CardState;
      receipt?: Receipt;
      focusKey: number;
    }
  | { id: number; kind: "error"; text: string; retry: string | null };

type ItemInput = Item extends infer T ? (T extends Item ? Omit<T, "id"> : never) : never;

function loadSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

function saveSession(session: Session | null): void {
  try {
    if (session) sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
    else sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // Storage can be blocked; the chat still works for this page view.
  }
}

/** The receipt word and icon after Confirm (UI brief section 3, receipt). */
export function receiptFor(actionType: string, outcome: string, reference: string | null): Receipt {
  if (outcome === "executed") {
    if (actionType === "cancellation") return { word: "Cancelled", icon: "cross", reference };
    return { word: "Confirmed", icon: "check", reference };
  }
  if (actionType === "hold_create") return { word: "Held", icon: "clock", reference };
  return { word: "Requested", icon: "inbox", reference };
}

function failureText(error: unknown): { text: string; retry: boolean } {
  if (error instanceof ApiFailure) {
    if (error.code === "NETWORK")
      return {
        text: "We couldn't reach the booking assistant. Your message was not sent.",
        retry: true,
      };
    if (error.code === "CONVERSATION_ENDED")
      return { text: "This conversation has ended. Start a new one to continue.", retry: false };
    if (error.code === "RATE_LIMITED")
      return { text: "Too many messages at once. Please wait a moment and try again.", retry: true };
  }
  return {
    text: `Something went wrong on our side. Nothing was booked or changed. You can try again or call us at ${BUSINESS.phone}.`,
    retry: true,
  };
}

export function Chat({ headingLevel = 2 }: { headingLevel?: 1 | 2 }) {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const [items, setItems] = useState<Item[]>([]);
  const [status, setStatus] = useState("active");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [slow, setSlow] = useState(false);
  const nextId = useRef(1);
  const staffSeen = useRef(0);
  const focusCounter = useRef(0);
  const input = useRef<HTMLTextAreaElement>(null);
  const logEnd = useRef<HTMLDivElement>(null);

  const add = useCallback((...entries: ItemInput[]) => {
    setItems((current) => [
      ...current,
      ...entries.map((entry) => ({ ...entry, id: nextId.current++ }) as Item),
    ]);
  }, []);

  const updateCard = useCallback((actionId: string, change: Partial<Extract<Item, { kind: "card" }>>) => {
    setItems((current) =>
      current.map((item) =>
        item.kind === "card" && item.card.action_id === actionId ? { ...item, ...change } : item,
      ),
    );
  }, []);

  // Restore an earlier conversation of this browser tab from the transcript.
  useEffect(() => {
    if (!session) return;
    let live = true;
    customerApi.transcript(session.conversationId, session.token).then(
      (transcript) => {
        if (!live) return;
        const restored: ItemInput[] = [];
        for (const message of transcript.messages) {
          if (message.role === "customer") restored.push({ kind: "customer", text: message.text });
          else if (message.role === "staff") restored.push({ kind: "staff", text: message.text });
          else restored.push({ kind: "assistant", text: message.text, tone: "text" });
        }
        if (transcript.card)
          restored.push({ kind: "card", card: transcript.card, state: "open", focusKey: 0 });
        staffSeen.current = transcript.messages.filter((m) => m.role === "staff").length;
        nextId.current = 1;
        setItems(restored.map((entry) => ({ ...entry, id: nextId.current++ }) as Item));
        setStatus(transcript.status);
      },
      () => {
        if (!live) return;
        // The conversation is gone (for example, the demo database was reset): start over.
        saveSession(null);
        setSession(null);
      },
    );
    return () => {
      live = false;
    };
    // Only on first load: later changes to the session come from this component itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Staff can take over and write here; check for their messages while the chat is open.
  useEffect(() => {
    if (!session || busy || status === "closed" || status === "abandoned") return;
    const timer = window.setInterval(() => {
      customerApi.transcript(session.conversationId, session.token).then(
        (transcript) => {
          const staff = transcript.messages.filter((m) => m.role === "staff");
          if (staff.length > staffSeen.current) {
            add(...staff.slice(staffSeen.current).map((m) => ({ kind: "staff" as const, text: m.text })));
            staffSeen.current = staff.length;
          }
          setStatus(transcript.status);
        },
        () => {},
      );
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [session, busy, status, add]);

  useEffect(() => {
    logEnd.current?.scrollIntoView?.({ block: "end" });
  }, [items, busy]);

  useEffect(() => {
    if (!busy) return;
    const timer = window.setTimeout(() => setSlow(true), SLOW_AFTER_MS);
    return () => {
      window.clearTimeout(timer);
      setSlow(false);
    };
  }, [busy]);

  function showTurn(turn: TurnOut) {
    const entries: ItemInput[] = [];
    for (const reply of turn.replies) {
      if (reply.kind === "emergency") entries.push({ kind: "emergency", text: reply.text });
      else entries.push({ kind: "assistant", text: reply.text, tone: reply.kind });
    }
    if (turn.quote) entries.push({ kind: "quote", quote: turn.quote });
    if (turn.slots.length > 0) entries.push({ kind: "slots", slots: turn.slots });
    setStatus(turn.status);
    const card = turn.card;
    if (!card) {
      add(...entries);
      return;
    }
    focusCounter.current += 1;
    const focusKey = focusCounter.current;
    setItems((current) => {
      const known = current.some(
        (item) => item.kind === "card" && item.card.action_id === card.action_id,
      );
      const marked = current.map((item) => {
        if (item.kind !== "card") return item;
        if (item.card.action_id === card.action_id) return { ...item, focusKey };
        if (item.state === "open") return { ...item, state: "replaced" as const };
        return item;
      });
      const added = entries.map((entry) => ({ ...entry, id: nextId.current++ }) as Item);
      if (!known)
        added.push({ id: nextId.current++, kind: "card", card, state: "open", focusKey });
      return [...marked, ...added];
    });
  }

  async function send(text: string) {
    const clean = text.trim();
    if (!clean || busy) return;
    setBusy(true);
    setDraft("");
    // Chips and earlier errors belong to the previous turn.
    setItems((current) => current.filter((item) => item.kind !== "slots" && item.kind !== "error"));
    add({ kind: "customer", text: clean });
    try {
      let current = session;
      if (!current) {
        const created = await customerApi.start();
        current = { conversationId: created.conversation_id, token: created.session_token };
        saveSession(current);
        setSession(current);
      }
      showTurn(await customerApi.send(current.conversationId, current.token, clean));
    } catch (error) {
      const failure = failureText(error);
      if (error instanceof ApiFailure && error.code === "CONVERSATION_ENDED") setStatus("closed");
      add({ kind: "error", text: failure.text, retry: failure.retry ? clean : null });
    } finally {
      setBusy(false);
    }
  }

  function retry(text: string) {
    // The failed message is shown again when it is sent again.
    setItems((current) => {
      const last = [...current].reverse().find((item) => item.kind === "customer");
      return current.filter((item) => item !== last && item.kind !== "error");
    });
    void send(text);
  }

  async function confirmCard(card: CardOut) {
    if (!session) return;
    updateCard(card.action_id, { state: "busy" });
    let result: ConfirmOut;
    try {
      result = await customerApi.confirm(card.action_id, session.token, card.payload_hash);
    } catch (error) {
      if (error instanceof ApiFailure && (error.code === "NOT_OPEN" || error.code === "HASH_MISMATCH")) {
        updateCard(card.action_id, { state: "expired" });
        add({ kind: "assistant", text: error.message, tone: "expired" });
      } else {
        updateCard(card.action_id, { state: "open" });
        add({ kind: "error", text: failureText(error).text + " Nothing was changed.", retry: null });
      }
      return;
    }
    setStatus(result.status);
    if (result.outcome === "EXPIRED") updateCard(card.action_id, { state: "expired" });
    else if (result.outcome === "failed") updateCard(card.action_id, { state: "failed" });
    else
      updateCard(card.action_id, {
        state: "done",
        receipt: receiptFor(card.action_type, result.outcome, result.booking_reference),
      });
    if (result.reply) add({ kind: "assistant", text: result.reply.text, tone: result.reply.kind });
  }

  async function cancelCard(card: CardOut) {
    if (!session) return;
    updateCard(card.action_id, { state: "busy" });
    try {
      const result = await customerApi.cancel(card.action_id, session.token);
      setStatus(result.status);
      updateCard(card.action_id, { state: result.outcome === "withdrawn" ? "cancelled" : "expired" });
    } catch (error) {
      updateCard(card.action_id, { state: "open" });
      add({ kind: "error", text: failureText(error).text, retry: null });
    }
    input.current?.focus();
  }

  function startOver() {
    saveSession(null);
    setSession(null);
    setItems([]);
    setStatus("active");
    staffSeen.current = 0;
    input.current?.focus();
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void send(draft);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send(draft);
    }
  }

  const ended = status === "closed" || status === "abandoned";
  const Heading = headingLevel === 1 ? "h1" : "h2";
  const lastSlots = [...items].reverse().find((item) => item.kind === "slots");

  return (
    <div className="chat">
      <header className="chat-header">
        <div>
          <Heading className="chat-title">{BUSINESS.name}</Heading>
          <p className="chat-sub">Usually replies in a few seconds</p>
        </div>
        <div className="chat-header-links">
          <button
            type="button"
            className="link"
            onClick={() => void send(PERSON_TEXT)}
            disabled={busy || ended}
          >
            <Icon name="person" /> Talk to a person
          </button>
          <a className="chat-emergency" href={`tel:${BUSINESS.emergencyPhone}`}>
            <Icon name="phone" /> Emergency? Call {BUSINESS.emergencyPhone}
          </a>
        </div>
      </header>

      {status === "handed_off" && (
        <p className="chat-banner" role="status">
          <Icon name="person" /> You're now talking with our team. They'll reply here.
        </p>
      )}

      <div className="chat-log" role="log" aria-live="polite" aria-label="Conversation">
        {items.length === 0 && (
          <div className="chat-welcome">
            <p>
              Hi. I can check open times, give prices and hold a booking for our team to
              confirm. What do you need help with?
            </p>
            <div className="chips" role="group" aria-label="Suggestions">
              {STARTERS.map((text) => (
                <button key={text} type="button" className="chip" onClick={() => void send(text)}>
                  {text}
                </button>
              ))}
            </div>
          </div>
        )}
        {items.map((item) => {
          switch (item.kind) {
            case "customer":
              return (
                <div key={item.id} className="msg msg-customer">
                  <span className="msg-who">You</span>
                  <p className="msg-text">{item.text}</p>
                </div>
              );
            case "assistant":
              return (
                <div key={item.id} className={`msg msg-assistant msg-${item.tone}`}>
                  <span className="msg-who">Assistant</span>
                  <p className="msg-text">{item.text}</p>
                </div>
              );
            case "staff":
              return (
                <div key={item.id} className="msg msg-staff">
                  <span className="msg-who">
                    <Icon name="person" /> Team member
                  </span>
                  <p className="msg-text">{item.text}</p>
                </div>
              );
            case "emergency":
              return (
                <div key={item.id} className="emergency" role="alert">
                  <p className="emergency-title">
                    <Icon name="alert" /> This sounds urgent
                  </p>
                  <p className="msg-text">{item.text}</p>
                  <a className="emergency-call" href={`tel:${BUSINESS.emergencyPhone}`}>
                    <Icon name="phone" /> Call {BUSINESS.emergencyPhone} now
                  </a>
                </div>
              );
            case "quote":
              return <QuoteTable key={item.id} quote={item.quote} />;
            case "slots":
              if (item !== lastSlots) return null;
              return (
                <div key={item.id} className="chips" role="group" aria-label="Open times">
                  {item.slots.map((slot) => (
                    <button
                      key={slot.slot_id}
                      type="button"
                      className="chip"
                      disabled={busy || ended}
                      onClick={() => void send(slot.when)}
                    >
                      {slot.when}
                    </button>
                  ))}
                </div>
              );
            case "card":
              return (
                <ConfirmCard
                  key={item.id}
                  card={item.card}
                  state={item.state}
                  receipt={item.receipt}
                  focusKey={item.focusKey}
                  onConfirm={() => void confirmCard(item.card)}
                  onCancel={() => void cancelCard(item.card)}
                />
              );
            case "error":
              return (
                <div key={item.id} className="chat-error">
                  <p>
                    <Icon name="alert" /> {item.text}
                  </p>
                  {item.retry !== null && (
                    <button type="button" onClick={() => retry(item.retry ?? "")}>
                      Try again
                    </button>
                  )}
                </div>
              );
          }
        })}
        <div ref={logEnd} />
      </div>

      <p className="chat-typing" role="status">
        {busy ? (slow ? "Still working on that…" : "Typing…") : ""}
      </p>

      {ended ? (
        <div className="chat-ended">
          <p>This conversation has ended.</p>
          <button type="button" className="primary" onClick={startOver}>
            Start a new conversation
          </button>
        </div>
      ) : (
        <form className="chat-form" onSubmit={onSubmit}>
          <label htmlFor="chat-input" className="visually-hidden">
            Your message
          </label>
          <textarea
            id="chat-input"
            ref={input}
            rows={2}
            maxLength={2000}
            value={draft}
            placeholder="Type your message"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onKeyDown}
          />
          <button type="submit" className="primary" disabled={busy || draft.trim() === ""}>
            Send
          </button>
        </form>
      )}
      <p className="chat-footer">Demo: all details are invented. Don't enter real information.</p>
    </div>
  );
}
