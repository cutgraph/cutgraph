import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { TranscriptWord } from "../src/api";
import type { Project } from "../src/types/project";
import { insertAsset } from "../src/v3/story/drawer/insert";
import { assetTab, type LibraryAsset } from "../src/v3/story/drawer/library";

// The co-pilot's suggest_broll places cues server side (pipeline/story_insert.py). Both languages
// assert the same golden cases, so a B-roll proposal lands exactly where a drawer drop would.
interface Case {
  name: string;
  project: Project;
  wordIdx: number;
  entry: LibraryAsset;
  expected: { error: string } | { id: string; tlStartF: number; trackIds: string[]; track: string; clip: unknown; tab: string };
}

const golden = JSON.parse(readFileSync(new URL("../../tests/fixtures/story_insert_cases.json", import.meta.url), "utf8")) as {
  media: string;
  words: TranscriptWord[];
  cases: Case[];
};

describe("drawer insert ↔ pipeline/story_insert.py parity", () => {
  it.each(golden.cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const result = insertAsset(c.project, golden.words, golden.media, c.wordIdx, c.entry);
    if ("error" in c.expected) {
      expect(result).toEqual({ error: c.expected.error });
      return;
    }
    if ("error" in result) throw new Error(result.error);
    const track = result.project.tracks.find((t) => t.clips?.some((clip) => clip.id === result.id));
    expect({
      id: result.id,
      tlStartF: result.tlStartF,
      trackIds: result.project.tracks.map((t) => t.id),
      track: track?.id,
      clip: track?.clips?.find((clip) => clip.id === result.id),
      tab: assetTab(c.entry.asset),
    }).toEqual(c.expected);
  });
});
