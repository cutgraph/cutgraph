import { create } from "zustand";

// View state for the v3 Story view. The edited document itself lives in the shared project store
// (store/useProjectStore.ts); nothing here is persisted or tracked by undo.
export type Lens = "script" | "board" | "timeline" | "source";

export interface WordRange {
  a: number;
  b: number;
}

export interface StoryState {
  lens: Lens;
  caretWord: number | null;
  range: WordRange | null;
  cueId: string | null;
  exportOpen: boolean;
  /** The Thumbnail editor, opened from the Export sheet. */
  thumbnailOpen: boolean;
  shotSceneId: string | null;
  /** Mirrors the FramePlayer's own play state so other regions (e.g. the Script lens' word
   * highlight) don't have to infer it from the playhead advancing. */
  playing: boolean;
  /** "anim" is the Character tab: a cameo on footage videos. */
  drawerTab: "broll" | "graphics" | "music" | "sfx" | "anim" | null;
  /** Words a pending Ask-bar proposal touches: "cut" strikes as a suggestion, "mark" tints. */
  proposalWords: ReadonlyMap<number, ProposalWordKind> | null;
}

export type ProposalWordKind = "cut" | "mark";

const INITIAL: StoryState = {
  lens: "script",
  caretWord: null,
  range: null,
  cueId: null,
  exportOpen: false,
  thumbnailOpen: false,
  shotSceneId: null,
  playing: false,
  drawerTab: null,
  proposalWords: null,
};

export const useStory = create<StoryState>(() => ({ ...INITIAL }));

export const resetStory = (): void => useStory.setState({ ...INITIAL });
export const setLens = (lens: Lens): void => useStory.setState({ lens });
export const setCaret = (wordIdx: number | null): void => useStory.setState({ caretWord: wordIdx, range: null });
export const setRange = (range: WordRange | null): void => useStory.setState({ range });
export const selectCue = (cueId: string | null): void => useStory.setState({ cueId, range: null });
export const openExport = (open: boolean): void => useStory.setState({ exportOpen: open });
export const openThumbnail = (open: boolean): void => useStory.setState({ thumbnailOpen: open });
export const openShot = (sceneId: string | null): void => useStory.setState({ shotSceneId: sceneId });
export const setPlaying = (playing: boolean): void => useStory.setState({ playing });
export const openDrawer = (tab: StoryState["drawerTab"]) => useStory.setState({ drawerTab: tab });
export const markProposalWords = (words: StoryState["proposalWords"]): void => useStory.setState({ proposalWords: words });

export function orderedRange(r: WordRange): WordRange {
  return r.a <= r.b ? r : { a: r.b, b: r.a };
}
