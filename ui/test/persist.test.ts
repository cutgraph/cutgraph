import { describe, it, expect, vi, beforeEach } from "vitest";
import type { Project } from "../src/types/project";

const { saveProjectMock } = vi.hoisted(() => ({ saveProjectMock: vi.fn() }));
const toastMock = vi.hoisted(() => vi.fn());
vi.mock("../src/api", () => ({ api: { saveProject: saveProjectMock } }));
vi.mock("../src/toast", () => ({ toast: toastMock }));

import { saveWithBase, persistCurrent } from "../src/persist";
import { resetSaveStatusForTest, useSaveStatus } from "../src/saveStatus";
import { useProjectStore } from "../src/store/useProjectStore";

function proj(name: string): Project {
  return { fps: 30, width: 1920, height: 1080, name, revision: 1, schemaVersion: 1, tracks: [{ id: "speech", kind: "speech", clips: [{ id: "c1", media: { source: "s.mp4", proxy: null }, srcInF: 0, srcOutF: 100, tlStartF: 0 }] }] };
}
const tick = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  saveProjectMock.mockReset(); toastMock.mockReset(); resetSaveStatusForTest();
  useProjectStore.setState({ project: null, revision: 0 }); useProjectStore.temporal.getState().clear();
});

describe("saveWithBase", () => {
  it("records a successful save", async () => {
    useProjectStore.setState({ project: proj("local"), revision: 5 });
    saveProjectMock.mockResolvedValueOnce({ ok: true, project: proj("local"), revision: 6 });
    expect(await saveWithBase("proj", proj("local"), 5)).toBe(true);
    expect(useProjectStore.getState().revision).toBe(6);
    expect(useSaveStatus.getState().state).toBe("saved");
  });

  it("preserves local work when the revision gate conflicts", async () => {
    useProjectStore.setState({ project: proj("local"), revision: 5 });
    saveProjectMock.mockResolvedValueOnce({ ok: false, currentRevision: 7 });
    expect(await saveWithBase("proj", proj("local"), 5)).toBe(false);
    expect(useProjectStore.getState().project!.name).toBe("local");
    expect(useProjectStore.getState().revision).toBe(5);
    expect(useSaveStatus.getState().state).toBe("conflict");
  });

  it("preserves local work and exposes retry state after a request failure", async () => {
    useProjectStore.setState({ project: proj("local"), revision: 5 });
    saveProjectMock.mockRejectedValueOnce(new Error("500 Internal Server Error"));
    expect(await saveWithBase("proj", proj("local"), 5)).toBe(false);
    expect(useProjectStore.getState().project!.name).toBe("local");
    expect(useSaveStatus.getState().state).toBe("failed");
    expect(toastMock.mock.calls[0][0]).toContain("still available");
  });
});

describe("persistCurrent", () => {
  it("coalesces rapid saves and uses the revision from the preceding save", async () => {
    useProjectStore.setState({ project: proj("a"), revision: 5 });
    const deferreds: Array<(value: unknown) => void> = [];
    saveProjectMock.mockImplementation(() => new Promise((resolve) => deferreds.push(resolve as (value: unknown) => void)));
    const first = persistCurrent("proj");
    useProjectStore.setState({ project: proj("b") }); void persistCurrent("proj");
    useProjectStore.setState({ project: proj("c") }); void persistCurrent("proj");
    expect(saveProjectMock.mock.calls[0][1].name).toBe("a");
    deferreds[0]({ ok: true, project: proj("a"), revision: 6 }); await tick();
    expect(saveProjectMock.mock.calls[1][1].name).toBe("c");
    expect(saveProjectMock.mock.calls[1][2]).toBe(6);
    deferreds[1]({ ok: true, project: proj("c"), revision: 7 }); await first;
    expect(useProjectStore.getState().revision).toBe(7);
  });
});
