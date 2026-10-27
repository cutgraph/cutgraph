// @vitest-environment jsdom
// Home's Recent videos row (thumbnail, title, subtitle, one click to the Story) and the
// start cards' live motion-core art.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import Home from "../src/v3/Home";

const WORKSPACES = [{ id: "garden", name: "Kitchen equipment", createdAt: "", updatedAt: "", videoCount: 1, assetCount: 0 }];
const RECENT = [
  { slug: "herb-care", title: "herb care basics", updatedAt: "2026-09-20T10:00:00Z", durationS: 135, thumbUrl: "/media/thumb/herb-care-src", workspaceId: "garden", workspaceName: "Kitchen equipment" },
  { slug: "no-thumb", title: "", updatedAt: "2026-09-19T10:00:00Z", workspaceId: "unsorted", workspaceName: "Unsorted" },
];

// A script video: its speech media is black, so the summary names it a script video instead.
const WORDS = ["Rain", "on", "the", "windows"].map((w, i) => ({ w, start: i, end: i + 0.8, segIdx: 0 }));
// The routes a script video's cover reads; each test uses its own slug, as the transcript is cached per video.
const scriptVideo = (slug: string, mode: "animation" | undefined) => [
  { slug, title: "Visit a quiet gallery", updatedAt: "2026-09-21T10:00:00Z", durationS: 4, script: true, workspaceId: "unsorted", workspaceName: "Unsorted" },
  {
    [`GET /api/project/${slug}`]: scriptDoc(slug, mode),
    [`GET /api/project/${slug}/transcription`]: { state: "ready", job: null },
    [`GET /api/transcript/${slug}`]: { words: WORDS, segments: [], fillers: [] },
  },
] as const;
const scriptDoc = (name: string, mode: "animation" | undefined) => ({
  project: {
    schemaVersion: 3, name, fps: 30, width: 1920, height: 1080, revision: 1, narration: "estimated",
    tracks: [{ id: "speech", kind: "speech", clips: [{ id: "source", media: { source: "data/source/explainer.mp4" }, srcInF: 0, srcOutF: 120, tlStartF: 0 }] }],
    scenes: [{ id: "s1", title: "A rainy visit", role: "Hook", startWord: 0, ...(mode && { shot: { mode, expression: "confused" } }) }],
  },
  revision: 1,
});

function mockApi(recent: unknown[], extra: Record<string, unknown> = {}) {
  const routes: Record<string, unknown> = { "GET /api/workspaces": WORKSPACES, "GET /api/videos/recent?limit=8": recent, ...extra };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const key = `GET ${String(input)}`;
    return key in routes
      ? new Response(JSON.stringify(routes[key]), { status: 200, headers: { "Content-Type": "application/json" } })
      : new Response(JSON.stringify({ error: `no route ${key}` }), { status: 404 });
  }));
}

HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Home", () => {
  it("shows Workspaces first, then the starts, then the Recent row, each card thumbnailed (or a fallback) and titled, opening its Story", async () => {
    mockApi(RECENT);
    render(<Home />);
    await screen.findByRole("heading", { name: "Recent" });
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(["Workspaces", "New video", "Recent"]);

    const herbCare = screen.getByRole("link", { name: /herb care basics/ });
    expect(herbCare.getAttribute("href")).toBe("/?view=story&ws=garden&video=herb-care");
    expect(herbCare.querySelector("img")?.getAttribute("src")).toBe("/media/thumb/herb-care-src");
    expect(herbCare.textContent).toContain("Kitchen equipment");
    expect(herbCare.textContent).toContain("2:15");

    const noThumb = screen.getByRole("link", { name: /no-thumb/ });
    expect(noThumb.querySelector("img")).toBeNull();
    expect(noThumb.querySelector(".cover-ph")).not.toBeNull();
  });

  it("covers a script video with its first animated scene, drawn by motion-core, never a black frame", async () => {
    const [summary, routes] = scriptVideo("explainer", "animation");
    mockApi([summary], routes);
    render(<Home />);
    const card = await screen.findByRole("link", { name: /Visit a quiet gallery/ });
    await waitFor(() => expect(card.querySelector("canvas.scene-picture")).not.toBeNull());
    expect(card.querySelector(".cover-scene canvas")!.getAttribute("data-mode")).toBe("animation");
    expect(card.querySelector("img")).toBeNull();
  });

  it("covers a video that has a thumbnail with that poster, drawn by motion-core, instead of its footage still", async () => {
    const summary = { slug: "visit", title: "Garden visit", updatedAt: "2026-09-24T10:00:00Z", thumbUrl: "/media/thumb/visit-src", thumbnail: true, workspaceId: "unsorted", workspaceName: "Unsorted" };
    const project = { schemaVersion: 3, name: "visit", fps: 30, width: 1920, height: 1080, revision: 1, tracks: [], thumbnail: { headline: "Museum Visit" } };
    mockApi([summary], { "GET /api/project/visit": { project, revision: 1 } });
    render(<Home />);
    const card = await screen.findByRole("link", { name: /Garden visit/ });
    await waitFor(() => expect(card.querySelector(".cover-scene canvas.poster-picture")).not.toBeNull());
    expect(card.querySelector("canvas")!.getAttribute("data-format")).toBe("16:9");
    expect(card.querySelector("img")).toBeNull();
    expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).not.toContain("/api/transcript/visit");
  });

  it("falls back to the letter cover for a script video with no animated scene to draw", async () => {
    const [summary, routes] = scriptVideo("sketch", undefined);
    mockApi([summary], routes);
    render(<Home />);
    const card = await screen.findByRole("link", { name: /Visit a quiet gallery/ });
    const fetched = () => vi.mocked(fetch).mock.calls.map(([url]) => String(url));
    await waitFor(() => expect(fetched()).toContain("/api/transcript/sketch"));
    await new Promise((r) => setTimeout(r, 0));
    expect(card.querySelector("canvas")).toBeNull();
    expect(card.querySelector("img")).toBeNull();
    expect(card.querySelector(".cover-ph")!.textContent).toBe("V");
  });

  it("keeps Recent to one short row, leaving out archived workspaces' videos", async () => {
    const video = (slug: string, workspaceId: string) => ({ slug, title: slug, updatedAt: "2026-09-20T10:00:00Z", workspaceId, workspaceName: workspaceId });
    const recent = [video("old-cut", "old"), ...["a", "b", "c", "d", "e"].map((s) => video(s, "garden"))];
    mockApi(recent, { "GET /api/workspaces": [...WORKSPACES, { id: "old", name: "Old series", createdAt: "", updatedAt: "", videoCount: 1, assetCount: 0, archived: true }] });
    render(<Home />);
    const row = (await screen.findByRole("heading", { name: "Recent" })).nextElementSibling as HTMLElement;
    await waitFor(() => expect(Array.from(row.querySelectorAll("b")).map((b) => b.textContent)).toEqual(["a", "b", "c", "d"]));
  });

  it("has no Recent row when nothing has been edited yet", async () => {
    mockApi([]);
    render(<Home />);
    await screen.findByRole("heading", { name: "Workspaces" });
    expect(screen.queryByRole("heading", { name: "Recent" })).toBeNull();
  });

  it("paints the start cards on a live motion-core canvas", () => {
    mockApi([]);
    render(<Home />);
    const starts = screen.getAllByRole("button").filter((b) => b.className === "start");
    expect(starts).toHaveLength(2);
    for (const start of starts) {
      expect(start.querySelector("canvas")).not.toBeNull();
    }
  });
});
