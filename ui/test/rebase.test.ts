import { describe, expect, it } from "vitest";
import { changes, fold, overlaps } from "../src/store/rebase";

interface Doc {
  name: string;
  tracks: { id: string; clips: { id: string; media: { source: string; proxy: string | null } }[] }[];
  scenes: { id: string; title: string; shot?: { pose: string } }[];
}

const base: Doc = {
  name: "talk",
  tracks: [{ id: "speech", clips: [{ id: "c1", media: { source: "talk.mp4", proxy: null } }] }],
  scenes: [{ id: "s1", title: "Open", shot: { pose: "wave" } }, { id: "s2", title: "Close" }],
};
const clone = (): Doc => structuredClone(base);

describe("changes", () => {
  it("lists the deepest changed paths, including added and removed keys", () => {
    const next = clone();
    next.tracks[0].clips[0].media.proxy = "proxies/talk_540.mp4";
    next.scenes[1].shot = { pose: "cheer" };
    delete next.scenes[0].shot;
    expect(changes(base, next)).toEqual([
      { path: ["tracks", 0, "clips", 0, "media", "proxy"], value: "proxies/talk_540.mp4" },
      { path: ["scenes", 0, "shot"], value: undefined },
      { path: ["scenes", 1, "shot"], value: { pose: "cheer" } },
    ]);
  });

  it("treats an array that changed length as one change", () => {
    const next = clone();
    next.scenes.push({ id: "s3", title: "Extra" });
    expect(changes(base, next)).toEqual([{ path: ["scenes"], value: next.scenes }]);
    expect(changes(base, clone())).toEqual([]);
  });
});

describe("overlaps", () => {
  const at = (...path: (string | number)[]) => [{ path, value: 1 }];
  it("is true for the same path or one nested inside the other, false for siblings", () => {
    expect(overlaps(at("scenes", 0, "shot", "pose"), at("scenes", 0, "shot", "pose"))).toBe(true);
    expect(overlaps(at("scenes"), at("scenes", 1, "title"))).toBe(true);
    expect(overlaps(at("scenes", 0, "shot", "move"), at("scenes"))).toBe(true);
    expect(overlaps(at("scenes", 0, "shot", "pose"), at("scenes", 0, "shot", "move"))).toBe(false);
    expect(overlaps(at("tracks", 0, "clips", 0, "media", "proxy"), at("tracks", 0, "clips", 0, "srcInF"))).toBe(false);
  });
});

describe("fold", () => {
  const server = clone();
  server.tracks[0].clips[0].media.proxy = "proxies/talk_540.mp4";
  server.scenes[1].title = "Wrap it up";
  const theirs = changes(base, server);

  it("carries the server's changes into a document that changed elsewhere, immutably", () => {
    const local = clone();
    local.scenes[0].shot = { pose: "cheer" };
    const merged = fold(local, theirs, base);
    expect(merged.scenes[0].shot?.pose).toBe("cheer");
    expect(merged.scenes[1].title).toBe("Wrap it up");
    expect(merged.tracks[0].clips[0].media.proxy).toBe("proxies/talk_540.mp4");
    expect(local.scenes[1].title).toBe("Close");
    expect(merged.scenes[0]).toBe(local.scenes[0]);
  });

  it("leaves a path whose value differs from the base alone", () => {
    const older = clone();
    older.scenes[1].title = "An older title";
    expect(fold(older, theirs, base).scenes[1].title).toBe("An older title");
  });

  it("never lands a change on a different element that moved into the same index", () => {
    const reordered = clone();
    reordered.scenes = [{ id: "s2", title: "Close" }, { id: "s1", title: "Close" }];
    const merged = fold(reordered, theirs, base);
    expect(merged.scenes.map((s) => `${s.id}:${s.title}`)).toEqual(["s2:Close", "s1:Close"]);
    expect(merged.tracks[0].clips[0].media.proxy).toBe("proxies/talk_540.mp4");
  });
});
