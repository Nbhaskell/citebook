/// <reference types="vitest/config" />
import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Two entry points: the customer widget (/widget/) and the staff console (/console/).
// The web UI runs on 127.0.0.1:5176 and talks to the API on 127.0.0.1:8300 through a proxy,
// so the browser sees one origin and the API needs no CORS settings.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5176,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8300" },
  },
  build: {
    rollupOptions: {
      input: {
        index: resolve(import.meta.dirname, "index.html"),
        widget: resolve(import.meta.dirname, "widget/index.html"),
        console: resolve(import.meta.dirname, "console/index.html"),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/setupTests.ts"],
  },
});
