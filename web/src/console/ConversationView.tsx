import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  ApiFailure,
  staffApi,
  type ActionOut,
  type ConversationDetail,
  type MessageOut,
} from "../api/client";
import { Badge } from "../shared/Badge";
import { Icon } from "../shared/Icon";
import {
  ACTION_LABELS,
  CHECK_LABELS,
  CONVERSATION_LABELS,
  GATE_LABELS,
  REASON_LABELS,
  STATUS_LABELS,
  businessTime,
} from "../shared/labels";
import { useLoad } from "./useLoad";

export const QUICK_REASONS = [
  "That time is no longer free",
  "Needs a site visit first",
  "Outside our service area",
  "Details could not be checked",
];

const ROLE_NAMES: Record<string, string> = {
  customer: "Customer",
  assistant: "Assistant",
  staff: "Team member",
};

function rows(summary: string): string[] {
  return summary.split(" · ");
}

function actionLabel(action: { action_type: string }) {
  return ACTION_LABELS[action.action_type] ?? { word: action.action_type, icon: "question" as const };
}

function statusBadge(status: string) {
  const label = STATUS_LABELS[status] ?? { word: status, icon: "question" as const, tone: "neutral" as const };
  return <Badge word={label.word} icon={label.icon} tone={label.tone} />;
}

// --- transcript ---------------------------------------------------------------------------------

function Transcript({ detail }: { detail: ConversationDetail }) {
  type Entry = { at: string; key: string; node: ReactNode };
  const entries: Entry[] = [];
  for (const message of detail.messages) {
    const who = ROLE_NAMES[message.role];
    if (!who) continue;
    entries.push({
      at: message.at,
      key: `m${message.seq}`,
      node: (
        <div className={`t-msg t-${message.role}`}>
          <span className="t-who">
            {who} <span className="t-time">{businessTime(message.at)}</span>
          </span>
          <p className="t-text">{message.text}</p>
        </div>
      ),
    });
  }
  for (const action of detail.actions) {
    if (action.action_type === "handoff") continue;
    const label = actionLabel(action);
    entries.push({
      at: action.created_at,
      key: `a${action.action_id}`,
      node: (
        <div className="t-card">
          <p className="t-card-title">
            <Icon name={label.icon} /> Card: {label.word}
          </p>
          <ul>
            {rows(action.summary).map((row, index) => (
              <li key={index}>{row}</li>
            ))}
          </ul>
          {statusBadge(action.status)}
          {action.booking_reference && <span className="ref"> {action.booking_reference}</span>}
        </div>
      ),
    });
  }
  entries.sort((a, b) => a.at.localeCompare(b.at));
  return (
    <section className="col" aria-labelledby="transcript-title">
      <h2 id="transcript-title">Transcript</h2>
      {entries.length === 0 ? (
        <p className="empty">No messages yet.</p>
      ) : (
        <div className="transcript">
          {entries.map((entry) => (
            <div key={entry.key}>{entry.node}</div>
          ))}
        </div>
      )}
    </section>
  );
}

// --- activity timeline --------------------------------------------------------------------------

function Checks({ message }: { message: MessageOut }) {
  const attempts = [...new Set(message.checks.map((check) => check.attempt))];
  return (
    <>
      {attempts.map((attempt) => {
        const checks = message.checks.filter((check) => check.attempt === attempt);
        const failed = checks.filter((check) => !check.passed);
        return (
          <div key={attempt} className="checks">
            <p className="checks-head">
              {attempt > 1 ? `Draft ${attempt} (regenerated)` : "Reply checks"}:{" "}
              {failed.length === 0 ? (
                <Badge word="All passed" icon="check" tone="ok" />
              ) : (
                <Badge word={`${failed.length} failed`} icon="cross" tone="bad" />
              )}
            </p>
            <ul className="check-list">
              {checks.map((check) => (
                <li key={check.code}>
                  {check.passed ? (
                    <Badge word="Pass" icon="check" tone="ok" />
                  ) : (
                    <Badge word="Fail" icon="cross" tone="bad" />
                  )}{" "}
                  {CHECK_LABELS[check.code] ?? check.code}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </>
  );
}

function Activity({ detail }: { detail: ConversationDetail }) {
  return (
    <section className="col" aria-labelledby="activity-title">
      <h2 id="activity-title">Activity</h2>
      {detail.messages.length === 0 ? (
        <p className="empty">No activity yet.</p>
      ) : (
        <ol className="timeline">
          {detail.messages.map((message) => (
            <li key={message.seq} className={`tl tl-${message.role}`}>
              <span className="tl-time">{businessTime(message.at)}</span>
              {message.role === "tool" && message.tool ? (
                <>
                  <p className="tl-head">
                    Tool <code>{message.tool.name}</code>{" "}
                    {message.tool.gate_decision === "ALLOWED" ? (
                      <Badge word={GATE_LABELS.ALLOWED} icon="check" tone="ok" />
                    ) : (
                      <Badge
                        word={GATE_LABELS[message.tool.gate_decision] ?? message.tool.gate_decision}
                        icon="cross"
                        tone="bad"
                      />
                    )}
                    {message.tool.gate_decision === "ALLOWED" && !message.tool.ok && (
                      <Badge word="Tool error" icon="alert" tone="warn" />
                    )}
                  </p>
                  <details>
                    <summary>Arguments and result</summary>
                    <pre>{JSON.stringify(message.tool.arguments, null, 2)}</pre>
                    <pre>{JSON.stringify(message.tool.result, null, 2)}</pre>
                  </details>
                </>
              ) : (
                <>
                  <p className="tl-head">
                    {message.role === "system_note" ? "System note" : ROLE_NAMES[message.role] ?? message.role}
                    {message.flags.map((flag) => (
                      <Badge key={flag} word={`Flag: ${flag}`} icon="alert" tone="warn" />
                    ))}
                  </p>
                  <p className="tl-text">{message.text}</p>
                  {message.checks.length > 0 && <Checks message={message} />}
                </>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

// --- decision panel -----------------------------------------------------------------------------

function DecisionForm({
  action,
  openedAt,
  onDone,
}: {
  action: ActionOut;
  openedAt: number;
  onDone: (message: string) => void;
}) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reasonBox = useRef<HTMLTextAreaElement>(null);
  const label = actionLabel(action);
  const isRefund = action.action_type === "refund_request";
  const errorId = `reject-error-${action.action_id}`;

  async function decide(decision: "approve" | "reject") {
    if (decision === "reject" && reason.trim() === "") {
      setError("Give a reason. The customer's notification draft uses it.");
      reasonBox.current?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const seconds = Math.round((Date.now() - openedAt) / 1000);
      const result = await staffApi.decide(action.action_id, decision, reason.trim(), seconds);
      const done =
        decision === "reject"
          ? `${label.word} rejected.`
          : action.action_type === "hold_create" && result.action_status === "executed"
            ? "Hold approved: the booking is confirmed."
            : `${label.word} approved.`;
      onDone(`${done} A notification draft is in the outbox, not sent.`);
    } catch (failure) {
      setError(failure instanceof ApiFailure ? failure.message : "The decision was not saved.");
      setBusy(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void decide("reject");
  }

  return (
    <article className="decision" aria-labelledby={`decision-${action.action_id}`}>
      <h3 id={`decision-${action.action_id}`}>
        <Icon name={label.icon} /> {label.word}
        {action.reason && <span className="muted"> · {REASON_LABELS[action.reason] ?? action.reason}</span>}
      </h3>
      <ul className="decision-rows">
        {rows(action.summary).map((row, index) => (
          <li key={index}>{row}</li>
        ))}
        {action.booking_reference && (
          <li>
            Reference <span className="ref">{action.booking_reference}</span>
          </li>
        )}
      </ul>
      {isRefund && <p className="note">Approving records your decision. Citebook does not move money.</p>}
      {!rejecting ? (
        <div className="button-row">
          <button type="button" className="primary" disabled={busy} onClick={() => void decide("approve")}>
            Approve {label.word.toLowerCase()}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setRejecting(true);
              window.setTimeout(() => reasonBox.current?.focus(), 0);
            }}
          >
            Reject…
          </button>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="reject-form">
          <label htmlFor={`reason-${action.action_id}`}>Reason for rejecting (required)</label>
          <div className="chips" role="group" aria-label="Quick reasons">
            {QUICK_REASONS.map((text) => (
              <button key={text} type="button" className="chip" onClick={() => setReason(text)}>
                {text}
              </button>
            ))}
          </div>
          <textarea
            id={`reason-${action.action_id}`}
            ref={reasonBox}
            rows={2}
            maxLength={500}
            value={reason}
            aria-invalid={error !== null}
            aria-describedby={error ? errorId : undefined}
            onChange={(event) => setReason(event.target.value)}
          />
          <div className="button-row">
            <button type="submit" className="danger" disabled={busy}>
              Reject {label.word.toLowerCase()}
            </button>
            <button type="button" disabled={busy} onClick={() => setRejecting(false)}>
              Back
            </button>
          </div>
        </form>
      )}
      {error && (
        <p id={errorId} className="error" role="alert">
          {error}
        </p>
      )}
    </article>
  );
}

function StaffMessage({ conversationId, onSent }: { conversationId: string; onSent: () => void }) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!text.trim()) return;
    try {
      await staffApi.message(conversationId, text.trim());
      setText("");
      setError(null);
      onSent();
    } catch (failure) {
      setError(failure instanceof ApiFailure ? failure.message : "The message was not sent.");
    }
  }
  return (
    <form onSubmit={(event) => void onSubmit(event)} className="staff-message">
      <label htmlFor="staff-text">Message to the customer (shown in their chat)</label>
      <textarea id="staff-text" rows={3} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} />
      <button type="submit" className="primary" disabled={!text.trim()}>
        Send to customer
      </button>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

function DecisionPanel({ detail, reload }: { detail: ConversationDetail; reload: () => void }) {
  const [notice, setNotice] = useState<string | null>(null);
  const noticeBox = useRef<HTMLParagraphElement>(null);
  // The decision buttons are gone after a decision; move focus to the result.
  useEffect(() => {
    if (notice) noticeBox.current?.focus();
  }, [notice]);
  const [openedAt] = useState(() => Date.now());
  const pending = detail.actions.filter((action) => action.status === "pending_staff");
  const earlier = detail.actions.filter((action) => action.status !== "pending_staff");
  const ended = detail.status === "closed" || detail.status === "abandoned";

  async function conversationStep(step: () => Promise<unknown>, done: string) {
    try {
      await step();
      setNotice(done);
    } catch (failure) {
      setNotice(failure instanceof ApiFailure ? failure.message : "That did not work. Try again.");
    }
    reload();
  }

  return (
    <section className="col" aria-labelledby="decision-title">
      <h2 id="decision-title">Decision</h2>
      <p className="notice" role="status" ref={noticeBox} tabIndex={-1}>
        {notice}
      </p>
      {detail.customer && (
        <dl className="customer">
          <dt>Customer</dt>
          <dd>{detail.customer.name}</dd>
          <dt>Phone</dt>
          <dd>{detail.customer.phone}</dd>
          <dt>Address</dt>
          <dd>
            {detail.customer.address} {detail.customer.zip_code}
          </dd>
        </dl>
      )}
      {pending.length === 0 ? (
        <p className="empty">Nothing in this conversation needs a decision.</p>
      ) : (
        pending.map((action) => (
          <DecisionForm
            key={action.action_id}
            action={action}
            openedAt={openedAt}
            onDone={(message) => {
              setNotice(message);
              reload();
            }}
          />
        ))
      )}
      <h3>Conversation</h3>
      <div className="button-row">
        {!ended && detail.status !== "handed_off" && (
          <button
            type="button"
            onClick={() =>
              void conversationStep(
                () => staffApi.takeOver(detail.conversation_id),
                "You have taken over. The assistant is paused for this conversation.",
              )
            }
          >
            <Icon name="person" /> Take over
          </button>
        )}
        {!ended && (
          <button
            type="button"
            onClick={() =>
              void conversationStep(() => staffApi.close(detail.conversation_id), "Conversation closed.")
            }
          >
            <Icon name="cross" /> Close conversation
          </button>
        )}
      </div>
      {detail.status === "handed_off" && (
        <StaffMessage conversationId={detail.conversation_id} onSent={reload} />
      )}
      {earlier.length > 0 && (
        <>
          <h3>Earlier actions</h3>
          <ul className="history">
            {earlier.map((action) => (
              <li key={action.action_id}>
                {actionLabel(action).word}: {statusBadge(action.status)}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

export function ConversationView({ id }: { id: string }) {
  const { load, reload } = useLoad(() => staffApi.conversation(id), [id], 8000);
  if (load.state === "loading") return <p role="status">Loading the conversation…</p>;
  if (load.state === "error")
    return (
      <p className="error" role="alert">
        {load.message}
      </p>
    );
  const detail = load.data;
  return (
    <section aria-labelledby="conversation-title">
      <p>
        <a href="#/queue/needs_decision">← Back to the queue</a>
      </p>
      <div className="view-head">
        <h1 id="conversation-title">
          {detail.customer?.name ?? "Unknown customer"}
          <span className="muted"> · {CONVERSATION_LABELS[detail.status] ?? detail.status}</span>
        </h1>
        <button type="button" onClick={reload}>
          Refresh
        </button>
      </div>
      <div className="detail">
        <DecisionPanel detail={detail} reload={reload} />
        <Transcript detail={detail} />
        <Activity detail={detail} />
      </div>
    </section>
  );
}
