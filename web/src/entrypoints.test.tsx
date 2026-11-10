import { fireEvent, render, screen } from "@testing-library/react";
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

  it("demo page launcher opens the chat panel and Escape closes it", () => {
    render(<WidgetApp mode="launcher" />);
    const launcher = screen.getByRole("button", { name: "Book or ask a question" });
    fireEvent.click(launcher);
    const panel = screen.getByRole("dialog", { name: "Chat with Kestrel Hollow Plumbing" });
    expect(panel).toBeVisible();
    expect(screen.getByLabelText("Your message")).toHaveFocus();
    fireEvent.keyDown(panel, { key: "Escape" });
    expect(screen.getByRole("button", { name: "Book or ask a question" })).toHaveFocus();
  });

  it("console carries the demo-data label", () => {
    render(<ConsoleApp />);
    expect(screen.getByRole("heading", { name: "Queue" })).toBeInTheDocument();
    expect(
      screen.getByText("Demo data. This business, its staff and customers are invented."),
    ).toBeInTheDocument();
  });
});
