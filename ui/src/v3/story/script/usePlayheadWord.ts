import { useEffect, useMemo, type RefObject } from "react";
import type { TranscriptWord } from "../../../api";
import { useProjectStore } from "../../../store/useProjectStore";
import { currentWordIdx, timedWords, timelineToSrc } from "../../../transcript/cutmap";
import type { Project } from "../../../types/project";
import { useStory } from "../storyStore";

// Lights the word under the playhead by toggling a `data-now` attribute straight on the DOM, so a
// playhead tick re-renders no React component however many words the script holds. While playback
// runs, the lit word is kept gently in view.
export function usePlayheadWord(
  rootRef: RefObject<HTMLElement | null>,
  project: Project | null,
  media: string | null,
  words: TranscriptWord[],
  cut: Set<number>,
): void {
  const timed = useMemo(() => timedWords(words), [words]);

  useEffect(() => {
    const root = rootRef.current;
    if (!root || !project || !media) return;
    root.querySelectorAll("[data-now]").forEach((el) => el.removeAttribute("data-now"));
    let lit: Element | null = null;
    let lastF = useProjectStore.getState().playheadF;

    const paint = (frame: number, follow: boolean): void => {
      const at = timelineToSrc(project, frame);
      // The middle of the frame: a word's start rounds to the nearest frame, possibly just before it.
      const idx = at && at.media === media ? currentWordIdx(timed, (at.srcF + 0.5) / project.fps, cut) : -1;
      const el = idx >= 0 ? root.querySelector(`[data-word-idx="${idx}"]`) : null;
      if (el === lit) return;
      lit?.removeAttribute("data-now");
      el?.setAttribute("data-now", "");
      lit = el;
      if (el && follow) keepInView(el);
    };

    paint(lastF, false);
    return useProjectStore.subscribe((s) => {
      if (s.playheadF === lastF) return;
      lastF = s.playheadF;
      paint(s.playheadF, useStory.getState().playing);
    });
  }, [rootRef, project, media, timed, cut]);
}

function keepInView(el: Element): void {
  const r = el.getBoundingClientRect();
  const pane = el.closest(".pane")?.getBoundingClientRect();
  const top = Math.max(pane?.top ?? 0, 0);
  const bottom = Math.min(pane?.bottom ?? window.innerHeight, window.innerHeight);
  if (r.top < top + 40 || r.bottom > bottom - 40) el.scrollIntoView({ block: "center", behavior: "smooth" });
}
