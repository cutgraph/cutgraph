import { registerBuildPacks } from "./packRegistry";

export function loadBuildPacks() {
  return registerBuildPacks(
    import.meta.glob("../../packs/*/pack.json", { eager: true, query: "?raw", import: "default" }),
    import.meta.glob("../../packs/*/index.ts"),
  );
}
