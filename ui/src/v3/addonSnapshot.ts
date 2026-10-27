import { v3api } from "./api";

export async function addonSnapshot() {
  const [catalog, health] = await Promise.all([v3api.addons(), v3api.health()]);
  return { ...catalog, packaged: health?.capabilities?.packaged === true,
    addons: catalog.addons.map((addon) => {
      const status = addon.name === "transcription" ? health?.capabilities?.transcription
        : addon.name === "assistant" ? health?.capabilities?.assistant : undefined;
      return { ...addon, ...status };
    }) };
}
