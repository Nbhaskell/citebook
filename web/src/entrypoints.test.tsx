import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConsoleApp } from "./console/ConsoleApp";
import { WidgetApp } from "./widget/WidgetApp";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("entry points", () => {
  it("widget carries the demo-data label", () => {
    render(<WidgetApp />);
    expect(screen.getByRole("heading", { name: "Kestrel Hollow Plumbing" })).toBeInTheDocument();
    expect(screen.getByText("Demo: all details are invented. Don't enter real information.")).toBeInTheDocument();
  });

  it("console carries the demo-data label", () => {
    render(<ConsoleApp />);
    expect(screen.getByRole("heading", { name: "Citebook staff console" })).toBeInTheDocument();
    expect(
      screen.getByText("Demo data. This business, its staff and customers are invented."),
    ).toBeInTheDocument();
  });
});
