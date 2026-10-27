// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resolveMedia } from "../src/preview/resolveMedia";
import { useSourceProxy } from "../src/preview/sourceProxy";
import type { Project } from "../src/types/project";

function project(source: string, proxy: string | null = null): Project {
  return {
    fps: 30, width: 1080, height: 1920, name: "p", revision: 1, schemaVersion: 3,
    tracks: [{ id: "speech", kind: "speech", clips: [{ id: "c", media: { source, proxy }, srcInF: 0, srcOutF: 90, tlStartF: 0 }] }],
  };
}

function Still({ project }: { project: Project }) {
  useSourceProxy(project);
  return <span data-testid="src">{resolveMedia(project.tracks[0].clips![0].media)}</span>;
}

function answer(...states: string[]) {
  const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ state: states.shift() ?? "ready" }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("useSourceProxy", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("encodes a source slug when polling its proxy", async () => {
    const fetchMock = answer("ready");
    render(<Still project={project("data/source/poll name?#%.mp4")} />);
    await act(() => vi.advanceTimersByTimeAsync(1500));
    expect(fetchMock).toHaveBeenCalledWith("/api/proxy/poll%20name%3F%23%25");
  });

  it("moves a first-open source onto its proxy when the server's encode lands", async () => {
    const fetchMock = answer("building", "building", "ready");
    render(<Still project={project("data/source/first-open.mp4")} />);
    expect(screen.getByTestId("src").textContent).toBe("/media/source/first-open");

    await act(() => vi.advanceTimersByTimeAsync(1500));
    expect(screen.getByTestId("src").textContent).toBe("/media/source/first-open");
    await act(() => vi.advanceTimersByTimeAsync(1500));

    expect(screen.getByTestId("src").textContent).toBe("/media/source/first-open?proxy");
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(Array(3).fill("/api/proxy/first-open"));
  });

  it("keeps the URL of a proxy that was ready from the start", async () => {
    const fetchMock = answer("ready");
    render(<Still project={project("data/source/already-built.mp4")} />);
    await act(() => vi.advanceTimersByTimeAsync(3000));

    expect(screen.getByTestId("src").textContent).toBe("/media/source/already-built");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("stops watching a failed encode and leaves the original playing", async () => {
    const fetchMock = answer("building", "failed");
    render(<Still project={project("data/source/broken.mp4")} />);
    await act(() => vi.advanceTimersByTimeAsync(6000));

    expect(screen.getByTestId("src").textContent).toBe("/media/source/broken");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("never asks about a clip that already has its proxy", async () => {
    const fetchMock = answer();
    render(<Still project={project("data/source/done.mp4", "outputs/proxies/done_540.mp4")} />);
    await act(() => vi.advanceTimersByTimeAsync(3000));

    expect(screen.getByTestId("src").textContent).toBe("/media/proxy/done_540.mp4");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
