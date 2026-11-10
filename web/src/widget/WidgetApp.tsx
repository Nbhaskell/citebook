import { useEffect, useRef, useState } from "react";
import { AblationBanner } from "../shared/AblationBanner";
import { Icon } from "../shared/Icon";
import { Chat } from "./Chat";
import "../shared/tokens.css";
import "./widget.css";

/**
 * The customer widget. "page" fills the window (/widget/); "launcher" is the floating button and
 * panel on the demo website.
 */
export function WidgetApp({ mode = "page" }: { mode?: "page" | "launcher" }) {
  const [open, setOpen] = useState(false);
  const launcher = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const wasOpen = useRef(false);

  useEffect(() => {
    if (open) panel.current?.querySelector<HTMLElement>("textarea")?.focus();
    else if (wasOpen.current) launcher.current?.focus();
    wasOpen.current = open;
  }, [open]);

  if (mode === "page") {
    return (
      <main className="widget-page">
        <AblationBanner />
        <Chat headingLevel={1} />
      </main>
    );
  }

  return (
    <div className="widget-float">
      <div
        ref={panel}
        id="chat-panel"
        className="widget-panel"
        role="dialog"
        aria-label="Chat with Kestrel Hollow Plumbing"
        hidden={!open}
        onKeyDown={(event) => {
          if (event.key === "Escape") setOpen(false);
        }}
      >
        <button type="button" className="widget-close" onClick={() => setOpen(false)}>
          <Icon name="cross" /> Close chat
        </button>
        <AblationBanner />
        <Chat />
      </div>
      {!open && (
        <button
          ref={launcher}
          type="button"
          className="primary widget-launcher"
          aria-expanded={open}
          aria-controls="chat-panel"
          onClick={() => setOpen(true)}
        >
          Book or ask a question
        </button>
      )}
    </div>
  );
}
