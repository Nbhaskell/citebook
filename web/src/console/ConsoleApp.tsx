import { useEffect, useRef, useState } from "react";
import { fetchStatus, type Queue } from "../api/client";
import { StatusPanel } from "../shared/StatusPanel";
import { AblationBanner } from "../shared/AblationBanner";
import { CalendarView } from "./CalendarView";
import { ConversationView } from "./ConversationView";
import { OutboxView } from "./OutboxView";
import { QueueView, TABS } from "./QueueView";
import "../shared/tokens.css";
import "./console.css";

type Route =
  | { view: "queue"; tab: keyof Queue }
  | { view: "conversation"; id: string }
  | { view: "calendar" }
  | { view: "outbox" }
  | { view: "status" };

export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/");
  if (parts[0] === "conversation" && parts[1]) return { view: "conversation", id: parts[1] };
  if (parts[0] === "calendar") return { view: "calendar" };
  if (parts[0] === "outbox") return { view: "outbox" };
  if (parts[0] === "status") return { view: "status" };
  const tab = TABS.find((t) => t.key === parts[1])?.key ?? "needs_decision";
  return { view: "queue", tab };
}

const NAV: { view: Route["view"]; href: string; label: string }[] = [
  { view: "queue", href: "#/queue/needs_decision", label: "Queue" },
  { view: "calendar", href: "#/calendar", label: "Calendar" },
  { view: "outbox", href: "#/outbox", label: "Outbox" },
  { view: "status", href: "#/status", label: "Status" },
];

/** The API's clock can run ahead or behind real time (D-066); ages use the API's time. */
function useApiNow(): () => Date {
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    fetchStatus(controller.signal).then(
      (result) => {
        if (result.kind === "ok" && result.status.now)
          setOffset(new Date(result.status.now).getTime() - Date.now());
      },
      () => {},
    );
    return () => controller.abort();
  }, []);
  return () => new Date(Date.now() + offset);
}

export function ConsoleApp() {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  const main = useRef<HTMLElement>(null);
  const now = useApiNow();

  useEffect(() => {
    const onHash = () => {
      const next = parseRoute(window.location.hash);
      setRoute((previous) => {
        // Moving between queue tabs keeps focus on the tab; other moves start at the new view.
        if (!(previous.view === "queue" && next.view === "queue")) window.setTimeout(() => main.current?.focus(), 0);
        return next;
      });
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <div className="console">
      <a className="skip" href="#console-main" onClick={(event) => {
        event.preventDefault();
        main.current?.focus();
      }}>
        Skip to content
      </a>
      <p className="demo-label">Demo data. This business, its staff and customers are invented.</p>
      <AblationBanner />
      <header className="console-header">
        <p className="console-title">
          <strong>Citebook staff console</strong> <span className="muted">Kestrel Hollow Plumbing</span>
        </p>
        <nav aria-label="Console">
          <ul>
            {NAV.map((item) => (
              <li key={item.view}>
                <a href={item.href} aria-current={route.view === item.view ? "page" : undefined}>
                  {item.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </header>
      <main id="console-main" ref={main} tabIndex={-1}>
        {route.view === "queue" && <QueueView tab={route.tab} now={now} />}
        {route.view === "conversation" && <ConversationView key={route.id} id={route.id} />}
        {route.view === "calendar" && <CalendarView />}
        {route.view === "outbox" && <OutboxView />}
        {route.view === "status" && (
          <section aria-labelledby="status-title">
            <h1 id="status-title">Status</h1>
            <StatusPanel />
            <p className="muted">
              Business rules (prices, hold time, cancellation window) are read-only in v1 and
              live in the database settings.
            </p>
          </section>
        )}
      </main>
    </div>
  );
}
