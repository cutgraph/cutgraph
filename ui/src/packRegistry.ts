import { selectPackManifests, registerPack, type PackRig } from "motion-core";

export async function registerBuildPacks(manifests: Record<string, string>, modules: Record<string, () => Promise<unknown>>): Promise<PackRig[]> {
  const entries: { id: string; manifest: unknown }[] = [];
  const paired = new Map<string, () => Promise<unknown>>();
  for (const path of Object.keys(manifests).sort()) {
    const dir = path.slice(0, path.lastIndexOf("/"));
    const modulePath = `${dir}/index.ts`;
    if (!Object.prototype.hasOwnProperty.call(modules, modulePath)) continue;
    const id = dir.slice(dir.lastIndexOf("/") + 1);
    try {
      entries.push({ id, manifest: JSON.parse(manifests[path]) });
      paired.set(id, modules[modulePath]);
    } catch (error) {
      console.warn(`Skipping character pack ${id}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  const accepted: PackRig[] = [];
  for (const { id, rigs } of selectPackManifests(entries)) {
    try {
      const module = await paired.get(id)!();
      const characters = module && typeof module === "object" && "characters" in module ? module.characters : undefined;
      const registered = registerPack({ characters: rigs }, characters, id);
      if (registered) accepted.push(...registered);
    } catch (error) {
      console.warn(`Skipping character pack ${id}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  return accepted;
}
