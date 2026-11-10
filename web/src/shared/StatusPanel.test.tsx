import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Status } from "../api/client";
import { StatusPanel } from "./StatusPanel";

const ok: Status = {
  service: "citebook",
  version: "0.0.0",
  provider: "ollama",
  model: "qwen3:8b",
  synthetic_data: true,
  ablation: "none",
  kb: "fake",
  model_reachable: null,
  kb_reachable: null,
  database: null,
};

function stubFetch(impl: () => Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("StatusPanel", () => {
  it("shows a loading state first", () => {
    stubFetch(() => new Promise(() => {}));
    render(<StatusPanel />);
    expect(screen.getByText("Checking the API…")).toBeInTheDocument();
  });

  it("shows the configuration and marks unbuilt checks as not checked", async () => {
    stubFetch(async () => Response.json(ok));
    render(<StatusPanel />);
    expect(await screen.findByText("qwen3:8b")).toBeInTheDocument();
    expect(screen.getByText("ollama")).toBeInTheDocument();
    expect(screen.getAllByText("Not checked yet")).toHaveLength(3);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("names an evaluation ablation in a banner", async () => {
    stubFetch(async () => Response.json({ ...ok, ablation: "baseline" }));
    render(<StatusPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Evaluation mode: safety layer switched off (baseline)",
    );
  });

  it("reports an error response", async () => {
    stubFetch(async () => new Response("nope", { status: 500 }));
    render(<StatusPanel />);
    expect(await screen.findByText("The API answered with an error (500).")).toBeInTheDocument();
  });

  it("reports an unreachable API", async () => {
    stubFetch(async () => {
      throw new TypeError("Failed to fetch");
    });
    render(<StatusPanel />);
    expect(
      await screen.findByText("The API is not reachable. Is it running on port 8300?"),
    ).toBeInTheDocument();
  });
});
