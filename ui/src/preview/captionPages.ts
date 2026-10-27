// The pill caption rule, shared by preview and export:
// which transcript words are captioned, where they sit on the timeline, how they page into one-line
// captions, and when each page and word state shows. Mirrors pipeline/caption_pages.py exactly;
// tests/fixtures/caption_pages_parity.json holds both to the same output. Times are integer
// centiseconds on the timeline (ASS precision), so the burned events and the player's frames agree.
import { layoutFor } from "motion-core";
import type { TranscriptWord } from "../api";
import type { Project } from "../types/project";
import { sec_to_frame } from "../transcript/cutmap";

/** Mean Inter Bold advance per character (em): the line budget without measuring. */
export const CHAR_EM = 0.624085441468254;
const LINE_FRAC = 0.82;
const GAP_CS = 120;
const SWITCH_CS = 5;
const HOLD_CS = 50;

export type PillWord = { w: string; start: number; end: number };
export type PillPage = { on: number; off: number; words: PillWord[] };
export type WordState = "C" | "P" | "U";

/** Baseline y and font px of the caption line: layoutFor's capY, and its capFs scaled from 1080 px. */
export function captionLayout(width: number, height: number): { capY: number; fs: number } {
  const L = layoutFor(width, height);
  return { capY: L.capY, fs: L.capFs * Math.min(width, height) / 1080 };
}

export function lineBudget(width: number, height: number): number {
  return Math.floor(LINE_FRAC * width / (CHAR_EM * captionLayout(width, height).fs));
}

const cs = (t: number): number => Math.floor(t * 100 + 0.5);
const timed = (w: TranscriptWord): boolean => Number.isFinite(w.start) && Number.isFinite(w.end);

function upperBound(sorted: number[], x: number): number {
  let i = 0;
  while (i < sorted.length && sorted[i] <= x) i += 1;
  return i;
}

/** Kept words in timeline order (see caption_pages.caption_words for the rule). */
export function captionWords(project: Project, words: TranscriptWord[]): (PillWord & { scene: number })[] {
  const speech = project.tracks.find((t) => t.kind === "speech");
  const first = speech?.clips?.[0];
  if (!speech || !first) return [];
  const fps = project.fps;
  const clips = (speech.clips ?? []).filter((c) => c.media.source === first.media.source);
  const placed: ([number, number] | null)[] = words.map((w) => {
    if (!timed(w)) return null;
    const f = sec_to_frame(w.start, fps);
    const clip = clips.find((c) => c.srcInF <= f && f < c.srcOutF);
    if (!clip) return null;
    const base = (clip.tlStartF - clip.srcInF) / fps;
    const start = cs(base + Math.max(w.start, clip.srcInF / fps));
    const end = cs(base + Math.min(w.end, clip.srcOutF / fps));
    return [start, Math.max(start, end)];
  });
  const timedIdx = words.flatMap((w, i) => (timed(w) ? [i] : []));
  words.forEach((w, i) => {
    if (timed(w) || !timedIdx.length) return;
    const k = timedIdx.findIndex((j) => j > i);
    const r = k > 0 ? timedIdx[k - 1] : k === 0 ? timedIdx[0] : timedIdx[timedIdx.length - 1];
    const at = placed[r];
    if (at) placed[i] = r < i ? [at[1], at[1]] : [at[0], at[0]];
  });
  const starts = [...new Set((project.scenes ?? []).map((s) => s.startWord))].sort((a, b) => a - b);
  return words
    .flatMap((w, i) => {
      const p = placed[i];
      return p ? [{ i, word: { w: String(w.w ?? "").trim(), start: p[0], end: p[1], scene: upperBound(starts, i) - 1 } }] : [];
    })
    .sort((a, b) => a.word.start - b.word.start || a.i - b.i)
    .map((x) => x.word)
    .filter((w) => w.w);
}

/** Page the kept words into one-line captions (see caption_pages.caption_pages for the rule). */
export function captionPages(project: Project, words: TranscriptWord[], width: number, height: number): PillPage[] {
  const budget = lineBudget(width, height);
  const groups: (PillWord & { scene: number })[][] = [];
  const breaks: boolean[] = [];
  let chars = 0;
  for (const w of captionWords(project, words)) {
    const n = [...w.w].length;
    const prev = groups.length ? groups[groups.length - 1][groups[groups.length - 1].length - 1] : null;
    if (!prev || w.scene !== prev.scene || w.start - prev.end > GAP_CS) {
      groups.push([w]);
      breaks.push(true);
      chars = n;
    } else if (chars + 1 + n > budget) {
      groups.push([w]);
      breaks.push(false);
      chars = n;
    } else {
      groups[groups.length - 1].push(w);
      chars += 1 + n;
    }
  }
  const pages: PillPage[] = [];
  groups.forEach((group, k) => {
    const last = group[group.length - 1].end;
    const next = groups[k + 1];
    const on = breaks[k] ? group[0].start : pages[pages.length - 1].off;
    const off = next ? Math.min(last + (breaks[k + 1] ? HOLD_CS : SWITCH_CS), next[0].start) : last + HOLD_CS;
    pages.push({ on, off, words: group.map(({ w, start, end }) => ({ w, start, end })) });
  });
  return pages;
}

/** Each page word's state at `t` centiseconds: C being spoken, P spoken, U upcoming. */
export function wordStates(page: PillPage, t: number): WordState[] {
  return page.words.map((w) => (w.start <= t && t < w.end ? "C" : t >= w.end ? "P" : "U"));
}

/** The page on screen at `t` centiseconds (pages are sorted and never overlap), or null. */
export function pageAt(pages: readonly PillPage[], t: number): PillPage | null {
  let low = 0;
  let high = pages.length - 1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (pages[mid].off <= t) low = mid + 1;
    else if (pages[mid].on > t) high = mid - 1;
    else return pages[mid];
  }
  return null;
}
