import { fileURLToPath } from "node:url";
import { defineConfig, searchForWorkspaceRoot } from "vite";
import react from "@vitejs/plugin-react";

const STUDIO = process.env.CUTGRAPH_DEV_STUDIO_URL ?? "http://127.0.0.1:8765";
// /api/events is the SSE stream — listed explicitly (ahead of /api) so it is proxied
// verbatim; the default http-proxy streams Server-Sent Events without buffering.
const proxy = Object.fromEntries(
  ["/api/events", "/api", "/media", "/upload"].map((p) => [
    p,
    { target: STUDIO, changeOrigin: true },
  ]),
);

// motion-core is a sibling source package (TypeScript, no build step), shared with the Node
// renderer; vitest.config.ts reuses this alias.
const MOTION_CORE = fileURLToPath(new URL("../motion-core", import.meta.url));
export const alias = { "motion-core": MOTION_CORE + "/src/index.ts" };

export default defineConfig({
  cacheDir: ".vite-cache",
  base: "/editor/",
  plugins: [react()],
  resolve: { alias },
  build: { outDir: "dist", emptyOutDir: true },
  server: { proxy, fs: { allow: [searchForWorkspaceRoot(process.cwd()), MOTION_CORE, fileURLToPath(new URL("../packs", import.meta.url)), fileURLToPath(new URL("../assets/fonts", import.meta.url))] } },
});
