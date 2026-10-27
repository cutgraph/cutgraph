import { describe, it, expect, vi, beforeEach } from "vitest";
import type { Project } from "../src/types/project";
import type { PendingCut } from "../src/store/useProjectStore";

// Mock the api + toast bus (hoisted). The stores are REAL — we exercise the actual planCuts →
// rippleCut → saveWithBase → applyLocal checkpoint path, only faking the network and the toast sink.
const { rippleMock, saveMock, getMock } = vi.hoisted(() => ({
  rippleMock: vi.fn(),
  saveMock: vi.fn(),
  getMock: vi.fn(),
}));
const toastMock = vi.hoisted(() => vi.fn());
vi.mock("../src/api", () => ({
  api: { rippleCut: rippleMock, saveProject: saveMock, getProject: getMock },
}));
vi.mock("../src/toast", () => ({ toast: toastMock }));

import { acceptSourceCuts } from "../src/transcript/acceptCuts";
import { useProjectStore, metaState, adoptSaved, setPendingCuts, applyLocal } from "../src/store/useProjectStore";
import { resetSaveStatusForTest, useSaveStatus } from "../src/saveStatus";

const MEDIA = "s.mp4";

function proj(name: string): Project {
  return {
    fps: 30, width: 1920, height: 1080, name, revision: 3, schemaVersion: 1,
    tracks: [{
      id: "speech", kind: "speech",
      clips: [{ id: "c1", media: { source: MEDIA, proxy: null }, srcInF: 0, srcOutF: 100, tlStartF: 0 }],
    }],
  };
}

const fillerCut = (srcStartF: number, srcEndF: number): PendingCut =>
  ({ media: MEDIA, srcStartF, srcEndF, kind: "filler", durSec: (srcEndF - srcStartF) / 30, conf: 1 });

const past = () => useProjectStore.temporal.getState().pastStates.length;

beforeEach(() => {
  rippleMock.mockReset();
  saveMock.mockReset();
  getMock.mockReset();
  toastMock.mockReset();
  useProjectStore.setState({ project: null, revision: 0 });
  useProjectStore.temporal.getState().clear();
  resetSaveStatusForTest();
});

describe("acceptSourceCuts — filler batch is ONE undo checkpoint", () => {
  it("routes a set of filler cuts through a single labeled checkpoint and adopts the revision", async () => {
    adoptSaved(proj("local"), 3); // load without recording history
    rippleMock.mockResolvedValueOnce({ project: proj("server"), warnings: [] });
    saveMock.mockResolvedValueOnce({ ok: true, revision: 4 });

    const past0 = past();
    const ok = await acceptSourceCuts("proj", [fillerCut(10, 20), fillerCut(30, 40)], 30);

    expect(ok).toBe(true);
    expect(past()).toBe(past0 + 1); // EXACTLY one undo delta for the whole batch
    expect(metaState().past[metaState().past.length - 1]).toEqual({
      origin: "human",
      label: "Removed 2 fillers",
    });
    expect(useProjectStore.getState().revision).toBe(4);
    expect(rippleMock).toHaveBeenCalledTimes(1); // one server ripple for the batch, not one per filler
    expect(saveMock).toHaveBeenCalledWith("proj", proj("server"), 3); // snapshotted base revision
  });

  it("a single filler cut labels 'Removed 1 filler'", async () => {
    adoptSaved(proj("local"), 3);
    rippleMock.mockResolvedValueOnce({ project: proj("server"), warnings: [] });
    saveMock.mockResolvedValueOnce({ ok: true, revision: 4 });

    await acceptSourceCuts("proj", [fillerCut(10, 20)], 30);
    expect(metaState().past[metaState().past.length - 1].label).toBe("Removed 1 filler");
  });

  it("labels by the cuts that LANDED, not the input, and toasts the dropped count", async () => {
    adoptSaved(proj("local"), 3);
    rippleMock.mockResolvedValueOnce({ project: proj("server"), warnings: [] });
    saveMock.mockResolvedValueOnce({ ok: true, revision: 4 });

    // [10,20) maps inside clip [0,100); [200,220) is past srcOutF and is dropped as stale.
    const ok = await acceptSourceCuts("proj", [fillerCut(10, 20), fillerCut(200, 220)], 30);

    expect(ok).toBe(true);
    // The checkpoint counts APPLIED cuts (1), never the full input (2).
    expect(metaState().past[metaState().past.length - 1].label).toBe("Removed 1 filler");
    expect(toastMock).toHaveBeenCalledWith("1 cut already removed");
    expect(rippleMock).toHaveBeenCalledTimes(1);
  });

  it("records NO checkpoint when every cut is already stale (nothing on the timeline)", async () => {
    adoptSaved(proj("local"), 3);
    const past0 = past();
    // Source range [200,220) is past the clip's srcOutF (100) → planCuts drops it, no ripple/save.
    const ok = await acceptSourceCuts("proj", [fillerCut(200, 220)], 30);
    expect(ok).toBe(true); // consumed (stale) so the caller can clear the pending set
    expect(past()).toBe(past0);
    expect(rippleMock).not.toHaveBeenCalled();
    expect(saveMock).not.toHaveBeenCalled();
  });

  it("is atomic on save failure: project, revision, history, and pending review stay unchanged", async () => {
    const local = proj("local");
    adoptSaved(local, 3);
    const pending = [fillerCut(10, 20)];
    setPendingCuts(pending);
    const past0 = past();
    const before = useProjectStore.getState().project;
    rippleMock.mockResolvedValue({ project: proj("would-be-next"), warnings: [] });
    saveMock.mockRejectedValue(new Error("offline"));

    expect(await acceptSourceCuts("proj", pending, 30)).toBe(false);
    expect(useProjectStore.getState().project).toBe(before);
    expect(useProjectStore.getState().revision).toBe(3);
    expect(past()).toBe(past0);
    expect(useProjectStore.getState().pendingCuts).toEqual(pending);
  });

  it("does not overwrite a newer local edit that lands while ripple/save is in flight", async () => {
    const local = proj("local");
    adoptSaved(local, 3);
    const pending = [fillerCut(10, 20)];
    setPendingCuts(pending);
    let resolveRipple!: (value: { project: Project; warnings: never[] }) => void;
    rippleMock.mockReturnValueOnce(new Promise((resolve) => { resolveRipple = resolve; }));
    saveMock.mockResolvedValueOnce({ ok: true, revision: 4 });

    const accept = acceptSourceCuts("proj", pending, 30);
    await Promise.resolve();
    const edited = proj("newer-local-edit");
    applyLocal(() => ({ project: edited }), { origin: "human", label: "Newer local edit" });
    resolveRipple({ project: proj("stale-server-result"), warnings: [] });

    expect(await accept).toBe(false);
    expect(useProjectStore.getState().project).toBe(edited);
    expect(metaState().past.at(-1)?.label).toBe("Newer local edit");
    expect(useSaveStatus.getState().state).toBe("unsaved");
    expect(saveMock).not.toHaveBeenCalled();
  });

  it("keeps newer local state when an edit lands during the save request", async () => {
    const local = proj("local");
    adoptSaved(local, 3);
    const pending = [fillerCut(10, 20)];
    let resolveSave!: (value: { ok: true; revision: number }) => void;
    rippleMock.mockResolvedValueOnce({ project: proj("server-result"), warnings: [] });
    saveMock.mockReturnValueOnce(new Promise((resolve) => { resolveSave = resolve; }));

    const accept = acceptSourceCuts("proj", pending, 30);
    await Promise.resolve();
    await Promise.resolve();
    expect(saveMock).toHaveBeenCalledTimes(1);
    const edited = proj("edit-during-save");
    applyLocal(() => ({ project: edited }), { origin: "human", label: "Edit during save" });
    resolveSave({ ok: true, revision: 4 });

    expect(await accept).toBe(false);
    expect(useProjectStore.getState().project).toBe(edited);
    expect(useProjectStore.getState().revision).toBe(3);
    expect(useSaveStatus.getState().state).toBe("failed");
  });

  it("keeps review state and reports a rejected ripple request", async () => {
    adoptSaved(proj("local"), 3);
    const pending = [fillerCut(10, 20)];
    setPendingCuts(pending);
    rippleMock.mockRejectedValueOnce(new Error("backend unavailable"));

    expect(await acceptSourceCuts("proj", pending, 30)).toBe(false);
    expect(useProjectStore.getState().pendingCuts).toEqual(pending);
    expect(toastMock).toHaveBeenCalledWith(expect.stringContaining("pending review kept"));
    expect(saveMock).not.toHaveBeenCalled();
  });

  it("prevents duplicate accepts while the first batch is in flight", async () => {
    adoptSaved(proj("local"), 3);
    const pending = [fillerCut(10, 20)];
    let resolveRipple!: (value: { project: Project; warnings: never[] }) => void;
    rippleMock.mockReturnValueOnce(new Promise((resolve) => { resolveRipple = resolve; }));
    saveMock.mockResolvedValueOnce({ ok: true, revision: 4 });

    const first = acceptSourceCuts("proj", pending, 30);
    await Promise.resolve();
    expect(await acceptSourceCuts("proj", pending, 30)).toBe(false);
    resolveRipple({ project: proj("server"), warnings: [] });
    expect(await first).toBe(true);
    expect(rippleMock).toHaveBeenCalledTimes(1);
  });

  it("explains a partial overlap with an authoritative existing cut", async () => {
    const local = proj("local");
    local.tracks[0].clips = [
      { id: "left", media: { source: MEDIA, proxy: null }, srcInF: 0, srcOutF: 50, tlStartF: 0 },
      { id: "right", media: { source: MEDIA, proxy: null }, srcInF: 70, srcOutF: 100, tlStartF: 50 },
    ];
    adoptSaved(local, 3);
    rippleMock.mockResolvedValueOnce({ project: proj("server"), warnings: [] });
    saveMock.mockResolvedValueOnce({ ok: true, revision: 4 });

    expect(await acceptSourceCuts("proj", [fillerCut(40, 80)], 30)).toBe(true);
    expect(toastMock).toHaveBeenCalledWith(expect.stringContaining("partially overlapped"));
  });
});
