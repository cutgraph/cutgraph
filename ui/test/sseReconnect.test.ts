import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// on SSE RECONNECT (a gap where agent events may have been missed — the stream has no ids and does
// not replay), sse.ts must re-run the chat/proposals reconcile — the SAME code path as the initial load,
// but NOT on the very first open (the Ask bar mount owns that). We drive connectProjectSSE with a
// fake EventSource + fake timers and assert reconcileChat fires only on the reconnect open.

const { reconcileMock, transcriptMock, setConnectedMock, ingestMock, applyRemoteMock, getProjectMock, getComputeJobMock, isSelfPendingMock } = vi.hoisted(() => ({
  reconcileMock: vi.fn(),
  transcriptMock: vi.fn(),
  setConnectedMock: vi.fn(),
  ingestMock: vi.fn(),
  applyRemoteMock: vi.fn(),
  getProjectMock: vi.fn().mockResolvedValue({ project: {}, revision: 0 }),
  getComputeJobMock: vi.fn(),
  isSelfPendingMock: vi.fn().mockReturnValue(false),
}));

vi.mock("../src/api", () => ({ api: { getProject: getProjectMock, getComputeJob: getComputeJobMock } }));
vi.mock("../src/selfPending", () => ({ isSelfPending: isSelfPendingMock }));
vi.mock("../src/store/useProjectStore", () => ({
  useProjectStore: { getState: () => ({ revision: 0 }) },
  applyRemote: applyRemoteMock,
}));
vi.mock("../src/store/useChatStore", () => ({
  ingestAgentEvent: ingestMock,
  reconcileChat: reconcileMock,
  setConnected: setConnectedMock,
}));
vi.mock("../src/transcript/useTranscript", () => ({ reconcileTranscript: transcriptMock }));

import { connectProjectSSE } from "../src/sse";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  url: string;
  closed = false;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  close() {
    this.closed = true;
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  reconcileMock.mockReset();
  transcriptMock.mockReset();
  setConnectedMock.mockReset();
  isSelfPendingMock.mockReset();
  isSelfPendingMock.mockReturnValue(false);
  vi.useFakeTimers();
  (globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource;
});

afterEach(() => {
  vi.useRealTimers();
});

describe("connectProjectSSE — reconnect reconcile", () => {
  it("reconciles chat/proposals on RECONNECT but not on the initial open", async () => {
    const cleanup = connectProjectSSE("proj");

    const es1 = FakeEventSource.instances.at(-1)!;
    es1.onopen?.(new Event("open"));
    expect(setConnectedMock).toHaveBeenCalledWith(true);
    expect(reconcileMock).not.toHaveBeenCalled(); // initial open: Ask bar mount owns the reconcile
    expect(transcriptMock).toHaveBeenCalledWith("proj");

    // Simulate a dropped stream: onerror closes + schedules a reconnect open.
    es1.onerror?.(new Event("error"));
    expect(setConnectedMock).toHaveBeenCalledWith(false);
    expect(es1.closed).toBe(true);

    vi.advanceTimersByTime(1000); // reconnect timer fires → a NEW EventSource
    const es2 = FakeEventSource.instances.at(-1)!;
    expect(es2).not.toBe(es1);

    es2.onopen?.(new Event("open")); // reconnect open
    expect(reconcileMock).toHaveBeenCalledTimes(1);
    expect(reconcileMock).toHaveBeenCalledWith("proj");
    expect(transcriptMock).toHaveBeenCalledTimes(2);

    // A completed transcription is an SSE job event, so it invalidates the
    // shared document and causes the visible panel to refetch without reload.
    es2.onmessage?.(new MessageEvent("message", { data: JSON.stringify({ type: "job", kind: "transcribe", status: "done" }) }));
    expect(transcriptMock).toHaveBeenCalledTimes(3);

    // Older SSE job payloads do not include createdAt. Jobs are enriched once from
    // the durable endpoint when an id is available, without changing the wire event.
    getComputeJobMock.mockResolvedValueOnce({ id: "job-1", kind: "render", status: "running", sequence: 1, createdAt: "2026-08-25T00:00:00.123456Z", createdOrdinal: 99 });
    es2.onmessage?.(new MessageEvent("message", { data: JSON.stringify({ type: "job", id: "job-1", kind: "render", status: "running", sequence: 1 }) }));
    await Promise.resolve();
    expect(getComputeJobMock).toHaveBeenCalledWith("job-1");
    expect(ingestMock).toHaveBeenLastCalledWith(expect.objectContaining({ type: "job", id: "job-1", createdAt: "2026-08-25T00:00:00.123456Z", createdOrdinal: 99 }));

    cleanup();
  });

  it("ignores the own revision echo while an atomic accept response is pending", () => {
    const cleanup = connectProjectSSE("proj");
    const es = FakeEventSource.instances.at(-1)!;
    isSelfPendingMock.mockReturnValue(true);
    es.onmessage?.(new MessageEvent("message", {
      data: JSON.stringify({ type: "project", revision: 1 }),
    }));
    expect(applyRemoteMock).not.toHaveBeenCalled();
    cleanup();
  });
});
