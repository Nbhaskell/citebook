import { useState } from "react";
import { staffApi } from "../api/client";
import { Badge } from "../shared/Badge";
import { Icon } from "../shared/Icon";
import { OUTBOX_LABELS, businessTime } from "../shared/labels";
import { useLoad } from "./useLoad";

export function OutboxView() {
  const { load } = useLoad(() => staffApi.outbox(), [], 15000);
  const [copied, setCopied] = useState<string | null>(null);

  return (
    <section aria-labelledby="outbox-title">
      <h1 id="outbox-title">Outbox</h1>
      <p className="muted">
        Notifications are drafts. Citebook sends no email or text messages; copy a draft to send it
        yourself.
      </p>
      {load.state === "loading" && <p role="status">Loading drafts…</p>}
      {load.state === "error" && (
        <p className="error" role="alert">
          {load.message}
        </p>
      )}
      {load.state === "ready" && load.data.length === 0 && <p className="empty">No drafts yet.</p>}
      {load.state === "ready" && (
        <ul className="drafts">
          {load.data.map((draft) => (
            <li key={draft.draft_id} className="draft">
              <div className="draft-head">
                <strong>{OUTBOX_LABELS[draft.kind] ?? draft.kind}</strong>
                {draft.booking_reference && <span className="ref">{draft.booking_reference}</span>}
                <span className="muted">{businessTime(draft.created_at)}</span>
                <Badge word="Draft, not sent" icon="inbox" tone="warn" />
              </div>
              <p className="draft-body">{draft.body}</p>
              <button
                type="button"
                onClick={() => {
                  void navigator.clipboard?.writeText(draft.body);
                  setCopied(draft.draft_id);
                }}
              >
                <Icon name="copy" /> {copied === draft.draft_id ? "Copied" : "Copy text"}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
