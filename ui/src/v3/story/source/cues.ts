import { audioKind } from "../../../timeline/audioKind";
import type { Anchor, Clip, GraphicItem, Project, TextItem } from "../../../types/project";

// A cue is a word-anchored overlay: B-roll, music and sound-effect clips,
// graphics and text items pinned to a transcript word. Time-pinned items have no word, so they are not
// cues.
export type Cue =
  | { kind: "broll" | "music" | "sfx"; id: string; wordIdx: number; clip: Clip }
  | { kind: "graphic"; id: string; wordIdx: number; item: GraphicItem }
  | { kind: "text"; id: string; wordIdx: number; item: TextItem };

export const CUE_ICON: Record<Cue["kind"], string> = { broll: "▣", music: "♪", sfx: "✧", graphic: "◆", text: "T" };

function anchorWord(anchor: Anchor | undefined, media: string, wordCount: number): number | null {
  if (anchor?.mode !== "word" || anchor.ref?.media !== media) return null;
  const idx = anchor.ref.wordIdx;
  return idx >= 0 && idx < wordCount ? idx : null;
}

// Cues on `media`'s words, in word order (timeline order within a word).
export function wordCues(project: Project, media: string | null, wordCount: number): Cue[] {
  if (!media) return [];
  const out: { cue: Cue; at: number }[] = [];
  for (const track of project.tracks) {
    if (track.kind === "video" || track.kind === "audio") {
      for (const clip of track.clips ?? []) {
        const wordIdx = anchorWord(clip.anchor, media, wordCount);
        if (wordIdx === null) continue;
        const cue: Cue = { kind: track.kind === "video" ? "broll" : audioKind(clip), id: clip.id, wordIdx, clip };
        out.push({ cue, at: clip.tlStartF });
      }
    } else if (track.kind === "text") {
      for (const item of track.items ?? []) {
        const wordIdx = anchorWord(item.anchor, media, wordCount);
        if (wordIdx === null) continue;
        const cue: Cue = "type" in item && item.type === "graphic"
          ? { kind: "graphic", id: item.id, wordIdx, item }
          : { kind: "text", id: item.id, wordIdx, item: item as TextItem };
        out.push({ cue, at: item.tlStartF });
      }
    }
  }
  return out.sort((a, b) => a.cue.wordIdx - b.cue.wordIdx || a.at - b.at).map((x) => x.cue);
}

// A clip's readable name: the name it was imported under when it came from the library (`labels`,
// drawer/library.ts assetLabels), else its file name, without the extension either way.
export function clipName(clip: Clip, labels?: ReadonlyMap<string, string>): string {
  const source = clip.media.source;
  return (labels?.get(source) ?? basename(source)).replace(/\.[^.]+$/, "");
}

export function basename(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

export function cueLabel(cue: Cue): string {
  if (cue.kind === "graphic") return cue.item.componentId;
  if (cue.kind === "text") return cue.item.text;
  return basename(cue.clip.media.source);
}
