// The co-pilot's library ranking (pipeline/copilot.py `_terms`, `_asset_terms`, `_pick_asset`),
// run locally so the selection bar's "Claude suggests" pick is instant and agrees with what
// suggest_broll would choose for the same words.

import type { Asset } from "../../api";
import type { LibraryAsset } from "./library";

const STOP = new Set(["the", "and", "for", "with", "this", "that", "you", "are", "was", "our", "your", "but",
  "not", "have", "has", "what", "when", "then", "they", "them", "its", "it's", "from"]);

export function terms(text: string): Set<string> {
  const out = new Set<string>();
  for (const t of text.toLowerCase().match(/[a-z0-9']+/g) ?? []) {
    if (t.length > 2 && !STOP.has(t)) out.add(t.endsWith("s") && t.length > 3 ? t.slice(0, -1) : t);
  }
  return out;
}

export function assetTerms(asset: Asset): Set<string> {
  const name = asset.name.replace(/\.[^.]+$/, "").replace(/[_-]/g, " ");
  return terms([name, ...asset.tags].join(" "));
}

// Most words matched first, then (when the phrase length is known) the length closest to it, then name.
export function rankAssets(entries: LibraryAsset[], phrase: string, phraseS?: number): LibraryAsset[] {
  const want = terms(phrase);
  const key = (e: LibraryAsset): [number, number, string] => {
    const hits = [...assetTerms(e.asset)].filter((t) => want.has(t)).length;
    const s = e.asset.durationS;
    const fit = phraseS !== undefined && typeof s === "number" ? Math.abs(s - Math.max(phraseS, 2)) : 0;
    return [-hits, fit, e.asset.name];
  };
  return entries
    .map((e) => ({ e, k: key(e) }))
    .sort((x, y) => x.k[0] - y.k[0] || x.k[1] - y.k[1] || (x.k[2] < y.k[2] ? -1 : x.k[2] > y.k[2] ? 1 : 0))
    .map(({ e }) => e);
}
