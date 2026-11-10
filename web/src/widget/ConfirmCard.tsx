import { useEffect, useRef, useState } from "react";
import type { CardOut } from "../api/client";
import { Icon, type IconName } from "../shared/Icon";

export type CardState = "open" | "busy" | "done" | "cancelled" | "expired" | "failed" | "replaced";

export type Receipt = { reference: string | null; word: string; icon: IconName };

type Props = {
  card: CardOut;
  state: CardState;
  receipt?: Receipt;
  /** Moves focus to the card title each time it changes to a new value above 0. */
  focusKey: number;
  onConfirm: () => void;
  onCancel: () => void;
};

const NOTES: Partial<Record<CardState, string>> = {
  cancelled: "You cancelled this. Nothing was changed.",
  expired: "This expired. Ask again for current times.",
  failed: "That time was just taken. Ask for other times.",
  replaced: "Replaced by a newer card.",
};

/** The confirmation card: built by code from the action's summary, never by the model. */
export function ConfirmCard({ card, state, receipt, focusKey, onConfirm, onCancel }: Props) {
  const heading = useRef<HTMLHeadingElement>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (focusKey > 0) heading.current?.focus();
  }, [focusKey]);

  const titleId = `card-${card.action_id}`;
  const closed = state !== "open" && state !== "busy";
  return (
    <section className={`card card-${state}`} aria-labelledby={titleId}>
      <h3 id={titleId} ref={heading} tabIndex={-1} className="card-title">
        {card.title}
      </h3>
      <ul className="card-rows">
        {card.rows.map((row, index) => (
          <li key={index}>{row}</li>
        ))}
      </ul>
      {state === "done" && receipt && (
        <div className="receipt" role="status">
          <span className="receipt-status">
            <Icon name={receipt.icon} /> {receipt.word}
          </span>
          {receipt.reference && (
            <span className="receipt-ref">
              Reference <strong>{receipt.reference}</strong>
              <button
                type="button"
                className="link"
                onClick={() => {
                  void navigator.clipboard?.writeText(receipt.reference ?? "");
                  setCopied(true);
                }}
              >
                <Icon name="copy" /> {copied ? "Copied" : "Copy"}
              </button>
            </span>
          )}
        </div>
      )}
      {closed && state !== "done" && <p className="card-note">{NOTES[state]}</p>}
      {!closed && (
        <div className="card-actions">
          <button type="button" className="primary" onClick={onConfirm} disabled={state === "busy"}>
            {state === "busy" ? "Confirming…" : "Confirm"}
          </button>
          <button type="button" onClick={onCancel} disabled={state === "busy"}>
            Cancel
          </button>
        </div>
      )}
      <p className="card-small">Nothing changes until you press Confirm. There is no payment.</p>
    </section>
  );
}
