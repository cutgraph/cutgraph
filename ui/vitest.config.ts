import { defineConfig } from "vitest/config";
import { alias } from "./vite.config";

// Node by default; component tests opt into jsdom per file (`// @vitest-environment jsdom`).
export default defineConfig({
  cacheDir: ".vite-cache",
  esbuild: { jsx: "automatic" },
  resolve: { alias },
  test: {
    environment: "node",
    include: ["test/**/*.test.{ts,tsx}"],
    setupFiles: ["test/setup.ts"],
  },
});
