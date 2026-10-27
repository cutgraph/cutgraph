// A Board panel's still shows the scene at its first B-roll or
// graphic, 55% of the way through it, so a scene carrying B-roll shows the B-roll and one carrying a
// graphic shows it over the footage. A scene with neither shows its first kept frame.
import type { Clip, GraphicItem } from "../../../types/project";
import type { SceneSpan } from "../model";
import type { Cue } from "../source/cues";

const INTO = 0.55;

export type PanelStill =
  /** The B-roll clip at source frame `srcF`. */
  | { kind: "broll"; clip: Clip; srcF: number }
  /** The footage at timeline frame `tlF`, with the graphic on screen then. */
  | { kind: "footage"; tlF: number; graphic: GraphicItem | null };

export function panelStill(span: SceneSpan, cues: readonly Cue[]): PanelStill | null {
  if (span.tlStartF === null) return null;
  const cue = cues.find((c) => c.wordIdx >= span.startWord && c.wordIdx < span.endWord && (c.kind === "broll" || c.kind === "graphic"));
  if (cue?.kind === "broll") {
    const { clip } = cue;
    return { kind: "broll", clip, srcF: clip.srcInF + Math.floor((clip.srcOutF - clip.srcInF) * INTO) };
  }
  if (cue?.kind === "graphic") return { kind: "footage", tlF: cue.item.tlStartF + Math.floor(cue.item.durF * INTO), graphic: cue.item };
  return { kind: "footage", tlF: span.tlStartF, graphic: null };
}
