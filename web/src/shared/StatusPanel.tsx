import { useEffect, useState } from "react";
import { fetchStatus, type StatusResult } from "../api/client";
import { businessTime } from "./labels";

function checked(value: boolean | null): string {
  if (value === null) return "Not checked yet";
  return value ? "Reachable" : "Not reachable";
}

export function StatusPanel() {
  const [result, setResult] = useState<StatusResult | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchStatus(controller.signal).then(setResult, () => {});
    return () => controller.abort();
  }, []);

  if (result === null) return <p role="status">Checking the API…</p>;
  if (result.kind === "unreachable")
    return <p role="status">The API is not reachable. Is it running on port 8300?</p>;
  if (result.kind === "error")
    return <p role="status">The API answered with an error ({result.httpStatus}).</p>;

  const s = result.status;
  return (
    <section aria-label="API status">
      {s.ablation !== "none" && (
        <p role="alert">Evaluation mode: safety layer switched off ({s.ablation})</p>
      )}
      <dl>
        <dt>Provider</dt>
        <dd>{s.provider}</dd>
        <dt>Model</dt>
        <dd>{s.model}</dd>
        <dt>Model server</dt>
        <dd>{checked(s.model_reachable)}</dd>
        <dt>Knowledge base</dt>
        <dd>{checked(s.kb_reachable)}</dd>
        <dt>Database</dt>
        <dd>{s.database ?? "Not checked yet"}</dd>
        <dt>Clock</dt>
        <dd>
          {s.clock === "demo" ? "Demo clock" : s.clock === "fixed" ? "Fixed (tests)" : "Real time"}
          {s.now ? `, now ${businessTime(s.now)}` : ""}
        </dd>
        <dt>Data</dt>
        <dd>{s.synthetic_data ? "Synthetic (invented)" : "Not marked synthetic"}</dd>
      </dl>
    </section>
  );
}
