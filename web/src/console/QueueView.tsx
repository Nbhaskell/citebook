import { useRef, type KeyboardEvent } from "react";
import { staffApi, type Queue, type QueueItem } from "../api/client";
import { Badge } from "../shared/Badge";
import { ACTION_LABELS, REASON_LABELS, STATUS_LABELS, age } from "../shared/labels";
import { useLoad } from "./useLoad";

export const TABS: { key: keyof Queue; label: string; empty: string }[] = [
  { key: "needs_decision", label: "Needs decision", empty: "Nothing needs a decision." },
  { key: "handoffs", label: "Handoffs", empty: "No conversations are waiting for a person." },
  { key: "expired_holds", label: "Expired holds", empty: "No holds expired in the last two days." },
  { key: "recent", label: "Recent", empty: "No decisions in the last two days." },
];

function reasonText(item: QueueItem): string {
  if (item.reason) return REASON_LABELS[item.reason] ?? item.reason;
  if (item.action_type === "hold_create" && item.status === "pending_staff")
    return "New booking to confirm";
  return "—";
}

export function QueueView({ tab, now }: { tab: keyof Queue; now: () => Date }) {
  const { load, reload } = useLoad(() => staffApi.queue(), [], 10000);
  const tabs = useRef<(HTMLAnchorElement | null)[]>([]);
  const current = TABS.find((t) => t.key === tab) ?? TABS[0];

  function onKeyDown(event: KeyboardEvent, index: number) {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = (index + step + TABS.length) % TABS.length;
    tabs.current[next]?.focus();
    window.location.assign(`#/queue/${TABS[next].key}`);
  }

  const rows = load.state === "ready" ? load.data[current.key] : [];
  return (
    <section aria-labelledby="queue-title">
      <div className="view-head">
        <h1 id="queue-title">Queue</h1>
        <button type="button" onClick={reload}>
          Refresh
        </button>
      </div>
      <div className="tabs" role="tablist" aria-label="Queue groups">
        {TABS.map((t, index) => {
          const count = load.state === "ready" ? load.data[t.key].length : null;
          const selected = t.key === current.key;
          return (
            <a
              key={t.key}
              ref={(element) => {
                tabs.current[index] = element;
              }}
              role="tab"
              id={`tab-${t.key}`}
              aria-selected={selected}
              aria-controls="queue-panel"
              tabIndex={selected ? 0 : -1}
              href={`#/queue/${t.key}`}
              className="tab"
              onKeyDown={(event) => onKeyDown(event, index)}
            >
              {t.label}
              {count !== null && <span className="tab-count">{count}</span>}
            </a>
          );
        })}
      </div>
      <div role="tabpanel" id="queue-panel" aria-labelledby={`tab-${current.key}`}>
        {load.state === "loading" && <p role="status">Loading the queue…</p>}
        {load.state === "error" && (
          <p className="error" role="alert">
            {load.message}
          </p>
        )}
        {load.state === "ready" && rows.length === 0 && <p className="empty">{current.empty}</p>}
        {rows.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Customer</th>
                <th scope="col">Visit</th>
                <th scope="col" className="num">
                  Total
                </th>
                <th scope="col">Age</th>
                <th scope="col">Reason</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => {
                const type = ACTION_LABELS[item.action_type] ?? { word: item.action_type, icon: "question" };
                const status =
                  item.action_type === "handoff" && item.status === "pending_staff"
                    ? ({ word: "Waiting for staff", icon: "person", tone: "warn" } as const)
                    : (STATUS_LABELS[item.status] ?? { word: item.status, icon: "question", tone: "neutral" });
                return (
                  <tr key={item.action_id}>
                    <td data-label="Type">
                      <Badge word={type.word} icon={type.icon} />
                    </td>
                    <td data-label="Customer">
                      <a href={`#/conversation/${item.conversation_id}`}>
                        {item.customer_name ?? "Unknown customer"}
                      </a>
                      {item.booking_reference && <div className="ref">{item.booking_reference}</div>}
                    </td>
                    <td data-label="Visit">{item.visit ?? "—"}</td>
                    <td data-label="Total" className="num">{item.total ?? "—"}</td>
                    <td data-label="Age">{age(item.created_at, now())}</td>
                    <td data-label="Reason">{reasonText(item)}</td>
                    <td data-label="Status">
                      <Badge word={status.word} icon={status.icon} tone={status.tone} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
