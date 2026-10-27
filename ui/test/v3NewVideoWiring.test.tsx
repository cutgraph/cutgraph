// @vitest-environment jsdom
// End-to-end wiring for "new video from footage": Home's and the Workspace page's entry points
// driving the real store -> runFlow -> liveDeps -> fetch chain (no XHR/SSE — those are exercised
// by the Playwright e2e against the real server; jsdom has neither).
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Asset, Workspace, WorkspaceDetail } from "../src/v3/api";


import V3App from "../src/v3/V3App";
import { useNewVideoFlow } from "../src/v3/newvideo/store";

const NOW = "2026-09-20T10:00:00Z";

const workspace = (id: string, name: string): Workspace => ({
  id, name, createdAt: NOW, updatedAt: NOW, videoCount: 0, assetCount: 1,
});

const videoAsset = (id: string, name: string): Asset => ({
  id, sha256: `sha-${id}`, name, kind: "video", file: `${id}.mp4`, bytes: 2048, tags: [], createdAt: NOW, durationS: 20,
});

const GARDEN: WorkspaceDetail = { workspace: workspace("garden", "Kitchen equipment"), videos: [], assets: [videoAsset("a1", "seedling-rack.mp4")] };

type Handler = unknown | ((init: RequestInit | undefined) => unknown);

// A real browser has EventSource; jsdom doesn't. Reaching "ready" navigates into StoryView, which
// opens one via useProjectSSE — stub it so that initial-load gap doesn't crash
// the test. The real SSE wiring is exercised by the Playwright e2e against the actual server.
class FakeEventSource {
  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {}
  close(): void {}
}

function mockApi(routes: Record<string, Handler>) {
  routes = { "GET /api/health": { capabilities: { localInference: true } }, ...routes };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    if (!(key in routes)) {
      return new Response(JSON.stringify({ error: `no route ${key}` }), { status: 404, statusText: "Not Found" });
    }
    const handler = routes[key];
    const body = typeof handler === "function" ? (handler as (i?: RequestInit) => unknown)(init) : handler;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("EventSource", FakeEventSource);
  // The completed flow mounts the player; jsdom has no browser layout observer.
  vi.stubGlobal("ResizeObserver", class {
    observe(): void {}
    disconnect(): void {}
  });
  return fetchMock;
}

function openAt(search: string) {
  window.history.replaceState(null, "", `/editor/${search}`);
  return render(<V3App />);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
  useNewVideoFlow.setState({ open: false, wsId: "", source: null, meta: {}, reconciled: false });
  try {
    sessionStorage.clear();
  } catch {
    // not available in every environment; harmless either way
  }
});

describe("Home: From footage picks or creates a workspace, then opens the same flow", () => {
  it("lists real workspaces (not brand/unsorted) and opens the picker for the one chosen", async () => {
    mockApi({
      "GET /api/workspaces": [workspace("garden", "Kitchen equipment"), workspace("brand", "Brand"), workspace("unsorted", "Unsorted")],
    });
    openAt("");
    await userEvent.click(await screen.findByRole("button", { name: /From footage/ }));
    const dialog = await screen.findByRole("dialog", { name: "Which workspace is this video for?" });
    expect(within(dialog).queryByText("Brand")).toBeNull();
    expect(within(dialog).queryByText("Unsorted")).toBeNull();

    await userEvent.click(within(dialog).getByRole("button", { name: "Kitchen equipment" }));
    expect(dialog.isConnected).toBe(false);
    expect(screen.getByRole("dialog", { name: "New video from footage" })).toBeTruthy();
    expect(useNewVideoFlow.getState().wsId).toBe("garden");
  });

  it("creates a new workspace inline, then opens the picker for it", async () => {
    const created = workspace("ws-new", "New shoots");
    const fetchMock = mockApi({
      "GET /api/workspaces": [],
      "POST /api/workspaces": created,
    });
    openAt("");
    await userEvent.click(await screen.findByRole("button", { name: /From footage/ }));
    await screen.findByRole("dialog", { name: "Which workspace is this video for?" });
    await userEvent.type(screen.getByPlaceholderText("Workspace name"), "New shoots");
    await userEvent.click(screen.getByRole("button", { name: "Create & continue" }));

    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/workspaces");
    await waitFor(() => expect(screen.getByRole("dialog", { name: "New video from footage" })).toBeTruthy());
    expect(useNewVideoFlow.getState().wsId).toBe("ws-new");
  });
});

describe("Workspace page: New video -> From footage, picking a library asset end to end", () => {
  it("drives the store through the real API chain to a finished story, no re-upload", async () => {
    const fetchMock = mockApi({
      "GET /api/workspaces/garden": GARDEN,
      "POST /api/workspaces/garden/assets/a1/use-as-source": { ok: true, name: "a1.mp4", slug: "a1" },
      "POST /api/projects": {
        project: { name: "seedling-rack", fps: 30, width: 1920, height: 1080, schemaVersion: 3, revision: 1, tracks: [] },
        revision: 1,
        transcription: { status: "queued" },
      },
      "PUT /api/workspaces/garden/videos/seedling-rack": { project: { name: "seedling-rack", workspaceId: "garden" }, revision: 1 },
      "GET /api/project/seedling-rack/transcription": { state: "ready", job: null },
      "GET /api/project/seedling-rack/proxy": { state: "none", proxy: null },
      "GET /api/project/seedling-rack": { project: { name: "seedling-rack", fps: 30, width: 1920, height: 1080, schemaVersion: 3, revision: 1, tracks: [] }, revision: 1 },
      "POST /api/project/seedling-rack/scenes/auto?apply=1": {
        project: { name: "seedling-rack", fps: 30, width: 1920, height: 1080, schemaVersion: 3, revision: 2, tracks: [], scenes: [{ id: "s1" }, { id: "s2" }] },
        revision: 2,
      },
    });

    openAt("?view=project&ws=garden");
    await userEvent.click(await screen.findByRole("button", { name: "New video" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /From footage/ }));
    await userEvent.click(screen.getByRole("tab", { name: "From your library" }));
    await userEvent.click(await screen.findByRole("button", { name: /seedling-rack\.mp4/ }));

    const titleInput = screen.getByLabelText("Title");
    expect((titleInput as HTMLInputElement).value).toBe("seedling-rack");
    await userEvent.click(screen.getByRole("button", { name: "Create video" }));

    await waitFor(() => expect(screen.getByRole("heading", { name: "Your story is ready" })).toBeTruthy());
    expect(screen.getByText("Laid out your story · 2 scenes")).toBeTruthy();

    const calledUrls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(calledUrls).toContain("/api/workspaces/garden/assets/a1/use-as-source");
    expect(calledUrls).toContain("/api/projects");
    expect(calledUrls).toContain("/api/workspaces/garden/videos/seedling-rack");
    expect(calledUrls).toContain("/api/project/seedling-rack/transcription");
    expect(calledUrls).toContain("/api/project/seedling-rack/scenes/auto?apply=1");
    expect(useNewVideoFlow.getState().flow).toMatchObject({ phase: "ready", ws: "garden", video: "seedling-rack", sceneCount: 2 });
    // Let the store's own auto-close `setTimeout` (see afterTransition in store.ts) settle here
    // instead of firing during the next test — it uses real timers, not fake ones.
    await new Promise((resolve) => setTimeout(resolve, 350));
  });

  it("shows a clear failure and a working Retry when transcription fails", async () => {
    mockApi({
      "GET /api/workspaces/garden": GARDEN,
      "POST /api/workspaces/garden/assets/a1/use-as-source": { ok: true, name: "a1.mp4", slug: "a1" },
      "POST /api/projects": {
        project: { name: "seedling-rack", fps: 30, width: 1920, height: 1080, schemaVersion: 3, revision: 1, tracks: [] },
        revision: 1,
        transcription: { status: "queued" },
      },
      "PUT /api/workspaces/garden/videos/seedling-rack": { project: { name: "seedling-rack", workspaceId: "garden" }, revision: 1 },
      "GET /api/project/seedling-rack/transcription": { state: "failed", job: { id: "j1", error: "WhisperX is not installed." }, message: "WhisperX is not installed." },
      "GET /api/project/seedling-rack/proxy": { state: "none", proxy: null },
    });

    openAt("?view=project&ws=garden");
    await userEvent.click(await screen.findByRole("button", { name: "New video" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /From footage/ }));
    await userEvent.click(screen.getByRole("tab", { name: "From your library" }));
    await userEvent.click(await screen.findByRole("button", { name: /seedling-rack\.mp4/ }));
    await userEvent.click(screen.getByRole("button", { name: "Create video" }));

    await waitFor(() => expect(screen.getByText("WhisperX is not installed.")).toBeTruthy());
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });
});
