// Preview/export parity for pill captions: the TS preview's caption pages must match the pages the
// Python export burns for the shared fixture (tests/test_caption_pages_parity.py holds the export to
// the same file), and at every burned event the player must show that event's page and word states.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { TranscriptWord } from "../src/api";
import type { Project } from "../src/types/project";
import { captionLayout, captionPages, lineBudget, pageAt, wordStates, type PillPage } from "../src/preview/captionPages";

interface Case {
  width: number;
  height: number;
  layout: [number, number];
  budget: number;
  pages: PillPage[];
  events: { start: number; end: number; page: number; states: string }[];
}

const fx = JSON.parse(readFileSync(resolve(__dirname, "../../tests/fixtures/caption_pages_parity.json"), "utf-8")) as {
  project: Project;
  words: TranscriptWord[];
  cases: Case[];
};

describe("pill caption parity with the export", () => {
  it.each(fx.cases.map((c) => [`${c.width}x${c.height}`, c] as const))("pages %s identically", (_name, c) => {
    const { capY, fs } = captionLayout(c.width, c.height);
    expect([capY, fs]).toEqual(c.layout);
    expect(lineBudget(c.width, c.height)).toBe(c.budget);
    expect(captionPages(fx.project, fx.words, c.width, c.height)).toEqual(c.pages);
  });

  it.each(fx.cases.map((c) => [`${c.width}x${c.height}`, c] as const))("shows every burned event %s", (_name, c) => {
    const pages = captionPages(fx.project, fx.words, c.width, c.height);
    for (const ev of c.events) {
      for (const t of [ev.start, ev.end - 0.5]) {
        const page = pageAt(pages, t);
        expect(page).toBe(pages[ev.page]);
        expect(wordStates(page!, t).join("")).toBe(ev.states);
      }
    }
    const shown = (t: number) => c.events.some((ev) => ev.start <= t && t < ev.end);
    for (let t = 0; t < c.pages[c.pages.length - 1].off + 100; t += 1) {
      expect(pageAt(pages, t) !== null).toBe(shown(t));
    }
  });
});
