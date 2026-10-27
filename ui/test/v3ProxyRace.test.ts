// The scrub proxy that lands after a new video is a second writer. The video open in this tab
// takes it through the store's serialised save, so it can never race the user's own edits; a video
// open nowhere here keeps the direct PATCH, taking turns with the footage flow's auto-scenes write.
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Project } from "../src/types/project";

const { saveProject, getProject, patchProject, autoScenes, proxyStatus, toast } = vi.hoisted(() => ({
  saveProject: vi.fn(), getProject: vi.fn(), patchProject: vi.fn(), autoScenes: vi.fn(), proxyStatus: vi.fn(), toast: vi.fn(),
}));
vi.mock("../src/api", () => ({ api: { saveProject, getProject, patchProject, autoScenes, proxyStatus } }));
vi.mock("../src/toast", () => ({ toast }));

import { persistCurrent } from "../src/persist";
import { adoptSaved, applyLocal, beginPreview, commit, metaState, undo, useProjectStore } from "../src/store/useProjectStore";
import { liveDeps, liveSetProxyWhenReady } from "../src/v3/newvideo/runFlow";

const PROXY = "proxies/talk_540.mp4";
const SCENES: Project["scenes"] = [
  { id: "a", title: "Hook", role: "Hook", startWord: 0 },
  { id: "b", title: "Wrap", role: "Wrap", startWord: 4 },
];

const doc = (name = "talk"): Project => ({
  fps: 30, width: 1920, height: 1080, name, revision: 5, schemaVersion: 3,
  tracks: [{ id: "speech", kind: "speech", clips: [{ id: "source", media: { source: "media/talk.mp4", proxy: null }, srcInF: 0, srcOutF: 300, tlStartF: 0 }] }],
  scenes: [{ id: "s1", title: "Open", role: "Hook", startWord: 0, shot: { mode: "animation", pose: "wave" } }],
});

const proxyOf = (p: Project | null) => p?.tracks[0].clips?.[0].media.proxy;
const poseOf = (p: Project | null) => p?.scenes?.[0].shot?.pose;

// A revision-gated server like studio_app's PUT/PATCH/auto-scenes; `hold` parks the next save until released.
function serve(initial: Project) {
  const server = { doc: structuredClone(initial), revision: initial.revision };
  const commit = (next: Project) => {
    server.revision += 1;
    server.doc = { ...structuredClone(next), revision: server.revision };
    return { ok: true as const, project: server.doc, revision: server.revision };
  };
  const gate = (base: number, next: () => Project) =>
    base === server.revision ? commit(next()) : { ok: false as const, currentRevision: server.revision };
  let held: Promise<void> | null = null;
  saveProject.mockImplementation(async (_name: string, next: Project, base: number) => {
    if (held) await held;
    return gate(base, () => next);
  });
  patchProject.mockImplementation(async (_name: string, ops: { path: string; value: string }[], base: number) =>
    gate(base, () => {
      expect(ops).toEqual([{ op: "replace", path: "/tracks/0/clips/0/media/proxy", value: PROXY }]);
      const next = structuredClone(server.doc);
      next.tracks[0].clips![0].media.proxy = PROXY;
      return next;
    }));
  autoScenes.mockImplementation(async (_name: string, base: number) => gate(base, () => ({ ...server.doc, scenes: SCENES })));
  getProject.mockImplementation(async () => ({ project: server.doc, revision: server.revision }));
  proxyStatus.mockResolvedValue({ state: "ready", proxy: PROXY });
  return {
    server,
    commit,
    hold: () => {
      let release!: () => void;
      held = new Promise((resolve) => { release = resolve; });
      return () => { held = null; release(); };
    },
  };
}

function editPose(pose: string): void {
  applyLocal((s) => ({ project: { ...s.project!, scenes: [{ ...s.project!.scenes![0], shot: { mode: "animation", pose } }] } }),
    { origin: "human", label: `Pose ${pose}` });
}

beforeEach(() => {
  for (const fn of [saveProject, getProject, patchProject, autoScenes, proxyStatus, toast]) fn.mockReset();
  useProjectStore.setState({ project: null, revision: 0 });
  useProjectStore.temporal.getState().clear();
});

describe("scrub proxy after a new video", () => {
  it("an edit saving when the proxy lands is saved first, then the proxy on its revision: no 409", async () => {
    const { server, hold } = serve(doc());
    adoptSaved(structuredClone(server.doc), server.revision);
    const release = hold();
    editPose("cheer");
    const editSave = persistCurrent("talk");

    const proxied = liveSetProxyWhenReady("talk");
    await vi.waitFor(() => expect(proxyOf(useProjectStore.getState().project)).toBe(PROXY));
    release();
    await Promise.all([editSave, proxied]);

    expect(saveProject.mock.calls.map((c) => c[2])).toEqual([5, 6]);
    expect(getProject).not.toHaveBeenCalled();
    expect(patchProject).not.toHaveBeenCalled();
    expect(poseOf(server.doc)).toBe("cheer");
    expect(proxyOf(server.doc)).toBe(PROXY);
    expect(useProjectStore.getState().revision).toBe(server.revision);
    expect(toast).not.toHaveBeenCalled();
  });

  it("the open video takes the proxy without an undo step, and undo never removes it", async () => {
    const { server } = serve(doc());
    adoptSaved(structuredClone(server.doc), server.revision);
    editPose("cheer");
    await persistCurrent("talk");
    const labels = metaState().past.length;
    const steps = useProjectStore.temporal.getState().pastStates.length;

    await liveSetProxyWhenReady("talk");
    expect(metaState().past.length).toBe(labels);
    expect(useProjectStore.temporal.getState().pastStates).toHaveLength(steps);
    expect(proxyOf(server.doc)).toBe(PROXY);

    undo();
    expect(poseOf(useProjectStore.getState().project)).toBe("wave");
    expect(proxyOf(useProjectStore.getState().project)).toBe(PROXY);
    await persistCurrent("talk");
    expect(poseOf(server.doc)).toBe("wave");
    expect(proxyOf(server.doc)).toBe(PROXY);
    expect(patchProject).not.toHaveBeenCalled();
  });

  it("during a live drag the proxy is PATCHed, and the drag's save rebases over it instead of dropping", async () => {
    const { server } = serve(doc());
    adoptSaved(structuredClone(server.doc), server.revision);
    beginPreview();
    useProjectStore.setState({ project: { ...useProjectStore.getState().project!, title: "Dragged" } });

    await liveSetProxyWhenReady("talk");
    expect(patchProject).toHaveBeenCalledTimes(1);
    expect(proxyOf(useProjectStore.getState().project)).toBeNull();

    commit("Drag");
    expect(await persistCurrent("talk")).toBe(true);
    expect(saveProject.mock.calls.map((c) => c[2])).toEqual([5, 6]);
    expect(server.doc.title).toBe("Dragged");
    expect(proxyOf(server.doc)).toBe(PROXY);
    expect(toast).not.toHaveBeenCalled();
  });

  it("a video not open in this tab is PATCHed directly on its current revision", async () => {
    const { server } = serve(doc());
    adoptSaved(doc("another-video"), 3);

    await liveSetProxyWhenReady("talk");

    expect(patchProject).toHaveBeenCalledTimes(1);
    expect(patchProject.mock.calls[0][2]).toBe(5);
    expect(saveProject).not.toHaveBeenCalled();
    expect(proxyOf(server.doc)).toBe(PROXY);
    expect(useProjectStore.getState().project?.name).toBe("another-video");
    expect(proxyOf(useProjectStore.getState().project)).toBeNull();
  });

  it("does nothing when the open video already has that proxy", async () => {
    const initial = doc();
    initial.tracks[0].clips![0].media.proxy = PROXY;
    serve(initial);
    adoptSaved(structuredClone(initial), 5);

    await liveSetProxyWhenReady("talk");
    expect(saveProject).not.toHaveBeenCalled();
    expect(patchProject).not.toHaveBeenCalled();
  });
});

describe("new video from footage: auto scenes beside the proxy", () => {
  it("applies scenes and the proxy one after the other, each on the revision it read", async () => {
    const { server } = serve({ ...doc(), scenes: [] });

    const [scenes] = await Promise.all([liveDeps.applyScenes("talk"), liveSetProxyWhenReady("talk")]);

    expect(scenes.sceneCount).toBe(2);
    expect(autoScenes).toHaveBeenCalledTimes(1);
    expect(patchProject).toHaveBeenCalledTimes(1);
    expect(server.revision).toBe(7);
    expect(server.doc.scenes).toEqual(SCENES);
    expect(proxyOf(server.doc)).toBe(PROXY);
  });

  it("retries the scenes apply once on a fresh revision when a writer elsewhere got in between", async () => {
    const { server, commit: land } = serve({ ...doc(), scenes: [] });
    getProject.mockImplementationOnce(async () => {
      const read = { project: server.doc, revision: server.revision };
      land({ ...server.doc, title: "Renamed elsewhere" });
      return read;
    });

    const scenes = await liveDeps.applyScenes("talk");

    expect(autoScenes.mock.calls.map((c) => c[1])).toEqual([5, 6]);
    expect(scenes.revision).toBe(7);
    expect(server.doc.title).toBe("Renamed elsewhere");
    expect(server.doc.scenes).toEqual(SCENES);
  });
});
