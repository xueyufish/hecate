import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    // The wire suite spawns the built server; keep timeouts generous on
    // cold starts but bounded.
    testTimeout: 20000,
    hookTimeout: 20000,
  },
});
