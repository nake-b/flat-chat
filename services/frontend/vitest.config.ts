import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Test-only config. Kept separate from vite.config.ts (dev server / proxy) so
// the two concerns don't entangle. jsdom gives hooks a DOM + React effects;
// `globals: false` (default) means tests import { describe, it, expect, vi }
// explicitly, which keeps `tsc -b` honest without pulling global type shims.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
