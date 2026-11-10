import { useCallback, useEffect, useState } from "react";
import { ApiFailure } from "../api/client";

export type Load<T> =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; data: T };

/** Loads data on mount and on `reload()`; with `everyMs`, also reloads on a timer. */
export function useLoad<T>(fetcher: () => Promise<T>, deps: unknown[], everyMs?: number) {
  const [load, setLoad] = useState<Load<T>>({ state: "loading" });
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((value) => value + 1), []);

  useEffect(() => {
    let live = true;
    fetcher().then(
      (data) => live && setLoad({ state: "ready", data }),
      (error: unknown) =>
        live &&
        setLoad({
          state: "error",
          message:
            error instanceof ApiFailure && error.code !== "NETWORK"
              ? error.message
              : "The Citebook API is not reachable. Is it running on port 8300?",
        }),
    );
    return () => {
      live = false;
    };
    // `fetcher` changes on every render; `deps` says when it really changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps]);

  useEffect(() => {
    if (!everyMs) return;
    const timer = window.setInterval(reload, everyMs);
    return () => window.clearInterval(timer);
  }, [everyMs, reload]);

  return { load, reload };
}
