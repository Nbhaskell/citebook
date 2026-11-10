import { useEffect, useState } from "react";
import { fetchStatus } from "../api/client";
import { Icon } from "./Icon";

/** Red banner when the API runs an evaluation ablation (UI brief section 4, status). */
export function AblationBanner() {
  const [ablation, setAblation] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    fetchStatus(controller.signal).then(
      (result) => {
        if (result.kind === "ok" && result.status.ablation !== "none")
          setAblation(result.status.ablation);
      },
      () => {},
    );
    return () => controller.abort();
  }, []);
  if (!ablation) return null;
  return (
    <p className="banner-eval" role="alert">
      <Icon name="alert" /> Evaluation mode: safety layer switched off ({ablation})
    </p>
  );
}
