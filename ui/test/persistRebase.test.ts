// A background writer (the scrub-proxy PATCH after a new video) bumped the server revision behind
// the open document, so the user's next save carried a stale base, got a 409 and was silently dropped.
// The safety net rebases disjoint changes and retries once; overlapping changes stay a conflict.
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Project } from "../src/types/project";

const { saveProject, getProject, toast } = vi.hoisted(() => ({ saveProject: vi.fn(), getProject: vi.fn(), toast: vi.fn() }));
vi.mock("../src/api", () => ({ api: { saveProject, getProject } }));
vi.mock("../src/toast", () => ({ toast }));

import { persistCurrent, saveWithBase } from "../src/persist";
import { resetSaveStatusForTest, useSaveStatus } from "../src/saveStatus";
import { adoptSaved, applyLocal, undo, useProjectStore } from "../src/store/useProjectStore";

const PROXY = "proxies/talk_540.mp4";

const doc = (): Project => ({
  fps: 30, width: 1920, height: 1080, name: "talk", revision: 5, schemaVersion: 3,
  tracks: [{ id: "speech", kind: "speech", clips: [{ id: "source", media: { source: "media/talk.mp4", proxy: null }, srcInF: 0, srcOutF: 300, tlStartF: 0 }] }],
  scenes: [
    { id: "s1", title: "Open", role: "Hook", startWord: 0, shot: { mode: "animation", pose: "wave" } },
    { id: "s2", title: "Close", role: "Wrap", startWord: 3 },
  ],
});

const withPose = (p: Project, pose: string): Project => ({
  ...p, scenes: p.scenes!.map((s, i) => (i === 0 ? { ...s, shot: { ...s.shot, pose } } : s)),
});
const withProxy = (p: Project): Project => ({
  ...p, tracks: [{ ...p.tracks[0], clips: [{ ...p.tracks[0].clips![0], media: { source: "media/talk.mp4", proxy: PROXY } }] }],
});
const proxyOf = (p: Project | null) => p?.tracks[0].clips?.[0].media.proxy;
const poseOf = (p: Project | null) => p?.scenes?.[0].shot?.pose;

// A revision-gated server like studio_app's PUT: a stale base is a 409, every accepted write bumps it.
function serve(initial: Project) {
  const server = { doc: structuredClone(initial), revision: initial.revision };
  const commit = (next: Project) => {
    server.revision += 1;
    server.doc = { ...structuredClone(next), revision: server.revision };
    return { ok: true as const, project: server.doc, revision: server.revision };
  };
  saveProject.mockImplementation(async (_name: string, next: Project, base: number) =>
    base === server.revision ? commit(next) : { ok: false as const, currentRevision: server.revision });
  getProject.mockImplementation(async () => ({ project: server.doc, revision: server.revision }));
  return { server, write: (mutate: (p: Project) => Project) => commit(mutate(server.doc)) };
}

function editPose(pose: string): void {
  applyLocal((s) => ({ project: withPose(s.project!, pose) }), { origin: "human", label: `Pose ${pose}` });
}

beforeEach(() => {
  saveProject.mockReset();
  getProject.mockReset();
  toast.mockReset();
  resetSaveStatusForTest();
  useProjectStore.setState({ project: null, revision: 0 });
  useProjectStore.temporal.getState().clear();
});

describe("save rebase after a background write", () => {
  it("saves an edit made while a proxy patch landed behind the open document", async () => {
    const { server, write } = serve(doc());
    adoptSaved(structuredClone(server.doc), server.revision);
    editPose("cheer");
    write(withProxy);

    expect(await persistCurrent("talk")).toBe(true);

    expect(saveProject).toHaveBeenCalledTimes(2);
    expect(saveProject.mock.calls[1][2]).toBe(6);
    expect(server.revision).toBe(7);
    expect(poseOf(server.doc)).toBe("cheer");
    expect(proxyOf(server.doc)).toBe(PROXY);
    const store = useProjectStore.getState();
    expect(store.revision).toBe(7);
    expect(poseOf(store.project)).toBe("cheer");
    expect(proxyOf(store.project)).toBe(PROXY);
    expect(useSaveStatus.getState().state).toBe("saved");
    expect(toast).not.toHaveBeenCalled();
  });

  it("keeps the folded server change out of undo: undo reverts only the user's edit", async () => {
    const { server, write } = serve(doc());
    adoptSaved(structuredClone(server.doc), server.revision);
    editPose("cheer");
    write(withProxy);
    await persistCurrent("talk");

    undo();
    expect(poseOf(useProjectStore.getState().project)).toBe("wave");
    expect(proxyOf(useProjectStore.getState().project)).toBe(PROXY);
    expect(await persistCurrent("talk")).toBe(true);
    expect(poseOf(server.doc)).toBe("wave");
    expect(proxyOf(server.doc)).toBe(PROXY);
  });

  it("keeps the conflict, the toast and the local edit when both sides changed the same value", async () => {
    const { server, write } = serve(doc());
    adoptSaved(structuredClone(server.doc), server.revision);
    editPose("cheer");
    write((p) => withPose(p, "shrug"));

    expect(await persistCurrent("talk")).toBe(false);

    expect(saveProject).toHaveBeenCalledTimes(1);
    expect(poseOf(server.doc)).toBe("shrug");
    expect(poseOf(useProjectStore.getState().project)).toBe("cheer");
    expect(useProjectStore.getState().revision).toBe(5);
    expect(useSaveStatus.getState().state).toBe("conflict");
    expect(toast.mock.calls[0][0]).toContain("Save conflict");
  });

  it("keeps the conflict when a local reorder moved the scene the server changed", async () => {
    // Both scenes share a role, so by index the reorder and the server's role change look disjoint.
    const beats = doc();
    beats.scenes = beats.scenes!.map((s) => ({ ...s, role: "Beat" as const }));
    const { server, write } = serve(beats);
    adoptSaved(structuredClone(server.doc), server.revision);
    applyLocal((s) => ({ project: { ...s.project!, scenes: [...s.project!.scenes!].reverse() } }), { origin: "human", label: "Reorder" });
    write((p) => ({ ...p, scenes: p.scenes!.map((s) => (s.id === "s2" ? { ...s, role: "Wrap" as const } : s)) }));

    expect(await persistCurrent("talk")).toBe(false);
    expect(getProject).toHaveBeenCalledTimes(1);
    expect(saveProject).toHaveBeenCalledTimes(1);
    expect(server.doc.scenes!.map((s) => `${s.id}:${s.role}`)).toEqual(["s1:Beat", "s2:Wrap"]);
    expect(useProjectStore.getState().project!.scenes!.map((s) => `${s.id}:${s.role}`)).toEqual(["s2:Beat", "s1:Beat"]);
    expect(useSaveStatus.getState().state).toBe("conflict");
  });

  it("keeps the conflict when this tab never synced the base it saved from", async () => {
    const { server, write } = serve(doc());
    adoptSaved(structuredClone(server.doc), server.revision);
    write((p) => ({ ...p, title: "Accepted elsewhere" }));
    useProjectStore.setState({ project: withPose(server.doc, "cheer"), revision: server.revision });
    write(withProxy);

    expect(await persistCurrent("talk")).toBe(false);
    expect(getProject).not.toHaveBeenCalled();
    expect(useSaveStatus.getState().state).toBe("conflict");
  });

  it("retries once: a second write landing during the rebase stays a conflict, local edits kept", async () => {
    const { server, write } = serve(doc());
    adoptSaved(structuredClone(server.doc), server.revision);
    editPose("cheer");
    write(withProxy);
    getProject.mockImplementationOnce(async () => {
      const snapshot = { project: server.doc, revision: server.revision };
      write((p) => ({ ...p, title: "Renamed elsewhere" }));
      return snapshot;
    });

    expect(await persistCurrent("talk")).toBe(false);
    expect(saveProject).toHaveBeenCalledTimes(2);
    expect(server.doc.title).toBe("Renamed elsewhere");
    expect(poseOf(useProjectStore.getState().project)).toBe("cheer");
    expect(useSaveStatus.getState().state).toBe("conflict");
    expect(toast.mock.calls[0][0]).toContain("Save conflict");
  });

  it("leaves explicit saveWithBase callers on today's conflict behaviour", async () => {
    const { server, write } = serve(doc());
    adoptSaved(structuredClone(server.doc), server.revision);
    write(withProxy);

    expect(await saveWithBase("talk", withPose(doc(), "cheer"), 5)).toBe(false);
    expect(getProject).not.toHaveBeenCalled();
    expect(useSaveStatus.getState().state).toBe("conflict");
  });
});
