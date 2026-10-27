// Adding a drawer item to the script: where it lands and the one-step edit that puts it there. A drop
// on a word, a click on a drawer tile and a pick from the selection bar all place through here.

import type { TranscriptWord } from "../../../api";
import { GRAPHIC_REGISTRY } from "../../../graphics/registry";
import { setPlayhead, useProjectStore } from "../../../store/useProjectStore";
import { toast } from "../../Toast";
import { commitEdit } from "../history";
import { orderedRange, selectCue, type WordRange } from "../storyStore";
import { insertAsset, insertGraphicAt, type InsertResult } from "./insert";
import { assetTab, findAsset, type LibraryAsset } from "./library";
import { TYPE_NAME } from "./Tiles";
import type { CuePayload } from "./wordDrop";

export interface CueTarget {
  at: number;
  /** The selection's last word, which B-roll runs to. */
  end?: number;
}

// Where a click-to-add lands: the selection's first kept word (B-roll then spans the selection), else
// the caret word, else `fallback` (the word under the playhead).
export function cueTarget(range: WordRange | null, caret: number | null, cut: ReadonlySet<number>, fallback: number | null): CueTarget | null {
  if (range) {
    const { a, b } = orderedRange(range);
    let at = a;
    while (at < b && cut.has(at)) at++;
    return { at, end: b };
  }
  const at = caret ?? fallback;
  return at === null ? null : { at };
}

export function placeCue(
  payload: CuePayload,
  target: CueTarget | null,
  assets: LibraryAsset[],
  words: TranscriptWord[],
  media: string | null,
): void {
  const current = useProjectStore.getState().project;
  if (!current || !media || !words.length || !target) {
    toast("The transcript is still loading, so there are no words to add to yet.");
    return;
  }
  let result: InsertResult;
  let label: string;
  if (payload.type === "graphic") {
    result = insertGraphicAt(current, words, media, target.at, payload.componentId);
    label = `Added ${GRAPHIC_REGISTRY[payload.componentId].name.toLowerCase()}`;
  } else {
    const entry = findAsset(assets, payload.workspaceId, payload.assetId);
    if (!entry) return;
    result = insertAsset(current, words, media, target.at, entry, target.end);
    label = `Added ${TYPE_NAME[assetTab(entry.asset)].toLowerCase()}: ${entry.asset.name}`;
  }
  if ("error" in result) {
    toast(result.error);
    return;
  }
  commitEdit(result.project, label);
  selectCue(result.id);
  setPlayhead(result.tlStartF);
}
