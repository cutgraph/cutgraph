import { describe, expect, it, vi } from "vitest";
import { IDLE_FLOW, transition, type FlowEvent, type FlowState } from "../src/v3/newvideo/flow";
import { retryFlow, runNewVideoFlow, slugify, type FlowDeps, type FlowHandle, type FlowMeta } from "../src/v3/newvideo/runFlow";

function makeHandle(): FlowHandle & { events: FlowEvent[]; flow: FlowState } {
  let flow: FlowState = IDLE_FLOW;
  let meta: FlowMeta = {};
  const events: FlowEvent[] = [];
  return {
    get flow() {
      return flow;
    },
    events,
    getFlow: () => flow,
    dispatch: (event) => {
      events.push(event);
      flow = transition(flow, event);
    },
    getMeta: () => meta,
    setMeta: (patch) => {
      meta = { ...meta, ...patch };
    },
  };
}

function makeDeps(overrides: Partial<FlowDeps> = {}): FlowDeps {
  return {
    upload: vi.fn(async (_file, onProgress) => {
      onProgress(0.5);
      onProgress(1);
      return { slug: "seedling" };
    }),
    useLibraryAsset: vi.fn(async () => ({ slug: "seedling" })),
    createProject: vi.fn(async () => ({ revision: 1 })),
    assignVideo: vi.fn(async () => {}),
    waitForTranscription: vi.fn(async (_p, onUpdate) => {
      onUpdate("Transcribing every word with WhisperX…");
    }),
    applyScenes: vi.fn(async () => ({ revision: 2, sceneCount: 3 })),
    setProxyWhenReady: vi.fn(),
    ...overrides,
  };
}

const upload = (name = "seedling.mp4"): { kind: "upload"; file: File } => ({
  kind: "upload",
  file: new File(["bytes"], name, { type: "video/mp4" }),
});

describe("runNewVideoFlow (mocked API)", () => {
  it("drives START through every real step to READY", async () => {
    const handle = makeHandle();
    const deps = makeDeps();

    await runNewVideoFlow(handle, { wsId: "garden", title: "seedling day", source: upload() }, deps);

    expect(deps.upload).toHaveBeenCalledWith(expect.any(File), expect.any(Function));
    expect(deps.createProject).toHaveBeenCalledWith("seedling_day", "seedling", "seedling day");
    expect(deps.assignVideo).toHaveBeenCalledWith("garden", "seedling_day");
    expect(deps.waitForTranscription).toHaveBeenCalledWith("seedling_day", expect.any(Function));
    expect(deps.applyScenes).toHaveBeenCalledWith("seedling_day");
    // Fired right after CREATED, not awaited — a slow/failed proxy encode must never hold up READY.
    expect(deps.setProxyWhenReady).toHaveBeenCalledWith("seedling_day");

    expect(handle.flow).toMatchObject({ phase: "ready", ws: "garden", video: "seedling_day", sceneCount: 3 });
    expect(handle.events.map((e) => e.type)).toEqual([
      "START", "UPLOAD_PROGRESS", "UPLOAD_PROGRESS", "UPLOADED", "CREATED", "TRANSCRIBING", "TRANSCRIBED", "READY",
    ]);
  });

  it("picks a library asset without calling upload", async () => {
    const handle = makeHandle();
    const deps = makeDeps();

    await runNewVideoFlow(
      handle,
      { wsId: "garden", title: "seedling day", source: { kind: "library", assetId: "abc123", name: "seedling.mp4" } },
      deps,
    );

    expect(deps.upload).not.toHaveBeenCalled();
    expect(deps.useLibraryAsset).toHaveBeenCalledWith("garden", "abc123");
    expect(handle.flow.phase).toBe("ready");
  });

  it("skips assignVideo for the unsorted pseudo-workspace", async () => {
    const handle = makeHandle();
    const deps = makeDeps();
    await runNewVideoFlow(handle, { wsId: "unsorted", title: "seedling day", source: upload() }, deps);
    expect(deps.assignVideo).not.toHaveBeenCalled();
  });

  it("stops at FAILED when the upload step throws, and never calls later steps", async () => {
    const handle = makeHandle();
    const deps = makeDeps({ upload: vi.fn().mockRejectedValue(new Error("disk full")) });

    await runNewVideoFlow(handle, { wsId: "garden", title: "seedling day", source: upload() }, deps);

    expect(handle.flow).toMatchObject({ phase: "error", errorStep: "upload", errorMessage: "disk full" });
    expect(deps.createProject).not.toHaveBeenCalled();
  });

  it("surfaces a transcription failure with its message and stops before scenes", async () => {
    const handle = makeHandle();
    const deps = makeDeps({
      waitForTranscription: vi.fn().mockRejectedValue(new Error("WhisperX is not installed.")),
    });

    await runNewVideoFlow(handle, { wsId: "garden", title: "seedling day", source: upload() }, deps);

    expect(handle.flow).toMatchObject({ phase: "error", errorStep: "transcribe", errorMessage: "WhisperX is not installed." });
    expect(deps.applyScenes).not.toHaveBeenCalled();
  });

  it("retryFlow re-enters only the failed step, reusing what earlier steps already produced", async () => {
    const handle = makeHandle();
    let transcribeAttempts = 0;
    const deps = makeDeps({
      waitForTranscription: vi.fn(async (_p, onUpdate) => {
        transcribeAttempts += 1;
        if (transcribeAttempts === 1) throw new Error("CUDA unavailable");
        onUpdate("Transcribing every word with WhisperX…");
      }),
    });

    await runNewVideoFlow(handle, { wsId: "garden", title: "seedling day", source: upload() }, deps);
    expect(handle.flow.phase).toBe("error");
    expect(deps.createProject).toHaveBeenCalledTimes(1);

    await retryFlow(handle, upload(), deps);

    expect(transcribeAttempts).toBe(2);
    // The already-created project is reused rather than creating a second one.
    expect(deps.createProject).toHaveBeenCalledTimes(1);
    expect(deps.upload).toHaveBeenCalledTimes(1);
    expect(handle.flow.phase).toBe("ready");
  });

  it("a create-step failure retries create, not the already-finished upload", async () => {
    const handle = makeHandle();
    let createAttempts = 0;
    const deps = makeDeps({
      createProject: vi.fn(async () => {
        createAttempts += 1;
        if (createAttempts === 1) throw new Error("project already exists");
        return { revision: 1 };
      }),
    });

    await runNewVideoFlow(handle, { wsId: "garden", title: "seedling day", source: upload() }, deps);
    expect(handle.flow).toMatchObject({ phase: "error", errorStep: "create" });

    await retryFlow(handle, upload(), deps);
    expect(deps.upload).toHaveBeenCalledTimes(1); // not repeated
    expect(createAttempts).toBe(2);
    expect(handle.flow.phase).toBe("ready");
  });
});

describe("slugify", () => {
  it("derives a safe project slug from a title or file name", () => {
    expect(slugify("seedling day")).toBe("seedling_day");
    expect(slugify("kitchen-equipment.mp4")).toBe("kitchen-equipment");
    expect(slugify("  weird!! chars??  ")).toBe("weird_chars");
  });
});
