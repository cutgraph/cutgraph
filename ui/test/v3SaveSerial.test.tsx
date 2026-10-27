// @vitest-environment jsdom
// An undo pressed while an edit is still saving must wait for that save and then save with the
// revision it produced, never race it with the stale base (which the server answers with a 409).
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import type { Project } from "../src/types/project";

const { saveProject } = vi.hoisted(() => ({ saveProject: vi.fn() }));
vi.mock("../src/api", () => ({ api: { saveProject } }));

import TopBar from "../src/v3/story/TopBar";
import { adoptSaved, useProjectStore } from "../src/store/useProjectStore";

const doc = (): Project => ({
  fps: 30, width: 1920, height: 1080, name: "herb-care", revision: 5, schemaVersion: 3,
  tracks: [{ id: "speech", kind: "speech", clips: [] }],
});

beforeEach(() => {
  saveProject.mockReset();
  adoptSaved(doc(), 5);
});
afterEach(cleanup);

describe("v3 saves are serialised", () => {
  it("an undo during an in-flight rename saves after it, on the new revision", async () => {
    let release!: (v: { ok: true; revision: number }) => void;
    saveProject.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    saveProject.mockResolvedValue({ ok: true, revision: 7 });

    const { getByRole } = render(<TopBar video="herb-care" onUndo={() => {}} />);
    const title = getByRole("heading", { level: 1 });
    title.textContent = "herb care";
    fireEvent.blur(title);
    expect(saveProject).toHaveBeenCalledTimes(1);
    expect(saveProject.mock.calls[0][2]).toBe(5);

    const { persistCurrent } = await import("../src/persist");
    const second = persistCurrent("herb-care");
    expect(saveProject).toHaveBeenCalledTimes(1);

    await act(async () => {
      release({ ok: true, revision: 6 });
      await second;
    });
    expect(saveProject).toHaveBeenCalledTimes(2);
    expect(saveProject.mock.calls[1][2]).toBe(6);
    expect(useProjectStore.getState().revision).toBe(7);
  });
});
