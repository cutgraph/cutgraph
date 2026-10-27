import { describe, it, expect } from "vitest";
import {
  useProjectStore,
  beginPreview,
  commit,
  applyLocal,
  adoptSaved,
  metaState,
} from "../src/store/useProjectStore";
import type { Project } from "../src/types/project";

// A minimal project with a single movable b-roll clip at tlStartF.
function project(tlStartF: number): Project {
  return {
    fps: 30,
    width: 1920,
    height: 1080,
    name: "p",
    revision: 1,
    schemaVersion: 1,
    tracks: [
      {
        id: "broll",
        kind: "video",
        clips: [{ id: "bv", media: { source: "b.mp4", proxy: null }, srcInF: 0, srcOutF: 30, tlStartF }],
      },
    ],
  };
}

const past = () => useProjectStore.temporal.getState().pastStates.length;

// The machine proxy for "one drag = one undo entry": the store records history on commit/applyLocal,
// never on the per-move draft writes. Deltas are measured (module-level history accumulates across `it`s).
describe("commit-phase undo", () => {
  it("a preview drag with N draft moves commits exactly one checkpoint", () => {
    adoptSaved(project(0), 1); // load without recording history
    const past0 = past();
    const meta0 = metaState().past.length;

    beginPreview();
    for (const f of [5, 10, 20, 33]) {
      useProjectStore.setState({ project: project(f) });
    }
    commit("Move");

    expect(past()).toBe(past0 + 1);
    expect(metaState().past.length).toBe(meta0 + 1);
    expect(metaState().past[metaState().past.length - 1]).toEqual({ origin: "human", label: "Move" });
    // the committed project reflects the LAST draft.
    expect(useProjectStore.getState().project!.tracks[0].clips![0].tlStartF).toBe(33);
  });

  it("draft moves during a paused preview record no history until commit", () => {
    const past0 = past();
    beginPreview();
    useProjectStore.setState({ project: project(7) });
    useProjectStore.setState({ project: project(9) });
    expect(past()).toBe(past0); // still paused — nothing recorded yet
    commit("Move again");
    expect(past()).toBe(past0 + 1);
  });

  it("applyLocal records exactly one entry", () => {
    const past0 = past();
    const meta0 = metaState().past.length;
    applyLocal(() => ({ project: project(99) }), { origin: "human", label: "Split" });
    expect(past()).toBe(past0 + 1);
    expect(metaState().past.length).toBe(meta0 + 1);
    expect(metaState().past[metaState().past.length - 1]).toEqual({ origin: "human", label: "Split" });
  });
});
