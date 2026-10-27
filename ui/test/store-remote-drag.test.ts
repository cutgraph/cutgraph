import { describe, it, expect } from "vitest";
import {
  useProjectStore,
  adoptSaved,
  beginPreview,
  commit,
  cancelPreview,
  applyLocal,
  applyRemote,
  metaState,
} from "../src/store/useProjectStore";
import type { Project } from "../src/types/project";

// A minimal project with one movable b-roll clip at tlStartF.
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
const startF = () => useProjectStore.getState().project!.tracks[0].clips![0].tlStartF;

// pause OWNERSHIP: a drag owns the tracking pause end to end. applyRemote mid-drag cancels the
// drag (checkpointing the PRE-drag state, never the draft) and manages its OWN pause; commit / abort
// resume the drag's pause. Module-level history accumulates across `it`s → measure deltas.
describe("remote-during-drag + pause ownership", () => {
  it("applyRemote mid-drag cancels the drag and checkpoints the PRE-drag state (not the draft)", () => {
    adoptSaved(project(0), 1); // pre-drag baseline at tlStartF 0
    const past0 = past();
    const meta0 = metaState().past.length;

    beginPreview(); // drag starts; tracking paused (owner "drag")
    useProjectStore.setState({ project: project(50) }); // a draft pointer-move
    expect(past()).toBe(past0); // paused → nothing recorded yet

    applyRemote(project(999), 2); // a remote write lands mid-drag

    // exactly ONE checkpoint (the agent remote update); meta stays in lockstep with zundo history
    expect(past()).toBe(past0 + 1);
    expect(metaState().past.length).toBe(meta0 + 1);
    // the recorded PAST entry is the PRE-drag state (0), never the throwaway draft (50)
    const recorded = useProjectStore.temporal.getState().pastStates[past() - 1] as { project: Project };
    expect(recorded.project.tracks[0].clips![0].tlStartF).toBe(0);
    // the store now holds the remote doc
    expect(startF()).toBe(999);

    // the drag is over: a late commit() is a no-op (records nothing, meta unchanged)
    const pLate = past();
    const mLate = metaState().past.length;
    commit("late");
    expect(past()).toBe(pLate);
    expect(metaState().past.length).toBe(mLate);

    // tracking was resumed by applyRemote's OWN (apply) owner — never left stuck on the drag's pause:
    // a subsequent applyLocal records in lockstep (past +1 AND meta +1).
    applyLocal(() => ({ project: project(111) }), { origin: "human", label: "post" });
    expect(past()).toBe(pLate + 1);
    expect(metaState().past.length).toBe(mLate + 1);
  });

  it("commit resumes the drag pause and records exactly one checkpoint", () => {
    adoptSaved(project(0), 1);
    const past0 = past();
    const meta0 = metaState().past.length;

    beginPreview();
    useProjectStore.setState({ project: project(7) });
    useProjectStore.setState({ project: project(9) });
    expect(past()).toBe(past0); // paused → nothing yet

    commit("Move");
    expect(past()).toBe(past0 + 1); // resumed + one checkpoint
    expect(metaState().past.length).toBe(meta0 + 1);
    expect(startF()).toBe(9); // committed the last draft

    // tracking resumed: a following applyLocal records in lockstep
    applyLocal(() => ({ project: project(11) }), { origin: "human", label: "next" });
    expect(past()).toBe(past0 + 2);
    expect(metaState().past.length).toBe(meta0 + 2);
  });

  it("cancelPreview (pointercancel / Escape) restores the pre-drag state and records nothing", () => {
    adoptSaved(project(0), 1);
    const past0 = past();
    const meta0 = metaState().past.length;

    beginPreview();
    useProjectStore.setState({ project: project(50) }); // draft
    cancelPreview();

    expect(startF()).toBe(0); // pre-drag state restored, draft dropped
    expect(past()).toBe(past0); // abort records nothing
    expect(metaState().past.length).toBe(meta0);

    // tracking resumed: a following applyLocal records in lockstep
    applyLocal(() => ({ project: project(5) }), { origin: "human", label: "after-abort" });
    expect(past()).toBe(past0 + 1);
    expect(metaState().past.length).toBe(meta0 + 1);
  });
});
