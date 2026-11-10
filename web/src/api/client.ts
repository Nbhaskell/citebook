import type { components } from "./schema";

export type Status = components["schemas"]["Status"];

export type StatusResult =
  | { kind: "ok"; status: Status }
  | { kind: "error"; httpStatus: number }
  | { kind: "unreachable" };

export async function fetchStatus(signal?: AbortSignal): Promise<StatusResult> {
  let response: Response;
  try {
    response = await fetch("/api/status", { signal });
  } catch (error) {
    if (signal?.aborted) throw error;
    return { kind: "unreachable" };
  }
  if (!response.ok) return { kind: "error", httpStatus: response.status };
  return { kind: "ok", status: (await response.json()) as Status };
}
