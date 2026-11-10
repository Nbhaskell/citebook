import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Without `globals: true`, Testing Library does not register its own cleanup, so rendered
// components would leak into the next test.
afterEach(() => {
  cleanup();
});
