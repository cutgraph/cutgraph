// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Asset, Workspace, WorkspaceDetail } from "../src/v3/api";

import V3App from "../src/v3/V3App";

const NOW = "2026-09-20T10:00:00Z";

const workspace = (id: string, name: string, videoCount: number, assetCount: number): Workspace => ({
  id, name, createdAt: NOW, updatedAt: NOW, videoCount, assetCount,
});

const asset = (id: string, name: string, kind: Asset["kind"], tags: string[] = []): Asset => ({
  id, sha256: `sha-${id}`, name, kind, file: `${id}.bin`, bytes: 2048, tags, createdAt: NOW,
  ...(kind === "audio" ? { durationS: 95 } : { width: 1920, height: 1080 }),
  ...(kind === "video" && { durationS: 42 }),
});

const WORKSPACES = [
  workspace("garden", "Kitchen recipes", 2, 3),
  workspace("brand", "Brand", 0, 0),
  workspace("unsorted", "Unsorted", 4, 0),
];

const GARDEN: WorkspaceDetail = {
  workspace: WORKSPACES[0],
  videos: [
    { slug: "herb-care", title: "herb care basics", updatedAt: NOW, durationS: 312 },
    { slug: "watering", title: "watering tips", updatedAt: NOW },
  ],
  assets: [
    asset("a1", "seedling-rack.mp4", "video", ["rack", "seedling"]),
    asset("a2", "planters.jpg", "image", ["planters"]),
    asset("a3", "garden-beat.mp3", "audio"),
  ],
};

type Handler = unknown | ((init: RequestInit | undefined) => unknown);

function mockApi(routes: Record<string, Handler>) {
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
});

describe("v3 shell routing", () => {
  it("renders the v3 Home by default", async () => {
    mockApi({ "GET /api/workspaces": [] });
    const { container } = openAt("");
    expect(screen.getByRole("heading", { name: "What are we making?" })).toBeTruthy();
    expect(container.querySelector(".v3")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Home" }).getAttribute("aria-current")).toBe("page");
  });

  it("opens legacy video links in Story", async () => {
    vi.stubGlobal("EventSource", class { close() {} });
    mockApi({});
    const { container } = openAt("?project=talk-cam-a");
    expect(container.querySelector(".v3")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Story" }).getAttribute("aria-current")).toBe("page");
    await screen.findByText(/no route GET \/api\/project\/talk-cam-a/);
  });

  it("switches spaces from the rail without a reload", async () => {
    const fetchMock = mockApi({
      "GET /api/workspaces": [], "GET /api/chat/config?verify=0": { providers: [] },
      "GET /api/health": { capabilities: { localInference: false, cpuRender: true } },
    });
    openAt("");
    await userEvent.click(screen.getByRole("link", { name: "Settings" }));
    expect(screen.getByRole("heading", { name: "Settings" })).toBeTruthy();
    expect(window.location.search).toBe("?view=settings");
    expect(screen.getByRole("heading", { name: "Add-ons" })).toBeTruthy();
    expect(screen.queryByText("WhisperX")).toBeNull();
    expect(fetchMock.mock.calls.some(([input]) => String(input).startsWith("/api/compute/worker"))).toBe(false);
    await userEvent.click(screen.getByRole("link", { name: "Library" }));
    expect(screen.getByRole("heading", { name: "Library" })).toBeTruthy();
    await userEvent.click(screen.getByRole("link", { name: "Story" }));
    expect(screen.getByRole("heading", { name: "No video open" })).toBeTruthy();
  });
});

describe("v3 Home", () => {
  it("lists workspaces from the API and opens one", async () => {
    mockApi({ "GET /api/workspaces": WORKSPACES, "GET /api/workspaces/garden": GARDEN });
    openAt("");
    const card = await screen.findByRole("link", { name: /Kitchen recipes/ });
    expect(within(card).getByText("2 videos · 3 assets")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Brand/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Unsorted/ })).toBeTruthy();

    await userEvent.click(card);
    expect(window.location.search).toBe("?view=project&ws=garden");
    expect(await screen.findByText("seedling-rack.mp4")).toBeTruthy();
  });

  it("shows an empty state when the workspaces API is missing", async () => {
    mockApi({});
    openAt("");
    expect(await screen.findByText("Workspaces aren't available on this studio yet.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("shows an empty state when the studio is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    openAt("");
    expect(await screen.findByText("Can't reach the studio.")).toBeTruthy();
  });

  it("creates a workspace inline and opens it", async () => {
    const created = workspace("ws-new", "Pasta series", 0, 0);
    const fetchMock = mockApi({
      "GET /api/workspaces": WORKSPACES,
      "POST /api/workspaces": created,
      "GET /api/workspaces/ws-new": { workspace: created, videos: [], assets: [] },
    });
    openAt("");
    await userEvent.click(await screen.findByRole("button", { name: /New workspace/ }));
    await userEvent.type(screen.getByLabelText("Workspace name"), "Pasta series{Enter}");

    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/workspaces");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ name: "Pasta series" });
    await waitFor(() => expect(window.location.search).toBe("?view=project&ws=ws-new"));
    expect(await screen.findByText("The library is empty. Drop files above to import them.")).toBeTruthy();
  });
});

describe("v3 Workspace page", () => {
  it("renders videos, assets with kind badges and tags, and filters by kind", async () => {
    mockApi({ "GET /api/workspaces/garden": GARDEN });
    const { container } = openAt("?view=project&ws=garden");
    expect(await screen.findByDisplayValue("Kitchen recipes")).toBeTruthy();
    expect(screen.getByRole("link", { name: /herb care basics/ }).getAttribute("href")).toBe("/editor/?view=story&ws=garden&video=herb-care");
    expect(screen.getByText("watering tips")).toBeTruthy();

    const names = () => Array.from(container.querySelectorAll(".asset b")).map((b) => b.textContent);
    expect(names()).toEqual(["seedling-rack.mp4", "planters.jpg", "garden-beat.mp3"]);
    expect(Array.from(container.querySelectorAll(".asset em")).map((e) => e.textContent)).toEqual(["video", "image", "audio"]);
    expect(screen.getByText("seedling")).toBeTruthy();
    expect(screen.getByText("planters")).toBeTruthy();
    expect(container.querySelector(".asset img")?.getAttribute("src")).toBe("/api/workspaces/garden/assets/a1/thumb");

    await userEvent.click(screen.getByRole("button", { name: /^Image/ }));
    expect(names()).toEqual(["planters.jpg"]);
    await userEvent.click(screen.getByRole("button", { name: /^Audio/ }));
    expect(names()).toEqual(["garden-beat.mp3"]);
    await userEvent.click(screen.getByRole("button", { name: /^All/ }));
    expect(names()).toHaveLength(3);
  });

  it("uploads picked files as multipart FormData and adds the new assets", async () => {
    const fresh = asset("a4", "bench.png", "image");
    const fetchMock = mockApi({
      "GET /api/workspaces/garden": GARDEN,
      "POST /api/workspaces/garden/assets": { assets: [fresh, GARDEN.assets[0]], deduped: 1 },
    });
    const { container } = openAt("?view=project&ws=garden");
    await screen.findByText("seedling-rack.mp4");

    const files = [
      new File(["png"], "bench.png", { type: "image/png" }),
      new File(["mp4"], "seedling-rack.mp4", { type: "video/mp4" }),
    ];
    await userEvent.upload(screen.getByLabelText("Choose files"), files);

    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/workspaces/garden/assets");
    const body = post?.[1]?.body;
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).getAll("files").map((f) => (f as File).name)).toEqual(["bench.png", "seedling-rack.mp4"]);

    await waitFor(() => expect(container.querySelector(".asset b")?.textContent).toBe("bench.png"));
    expect(container.querySelectorAll(".asset")).toHaveLength(4);
    expect(await screen.findByText("Added 1 asset · 1 already in the library")).toBeTruthy();
  });

  it("renames the workspace on blur", async () => {
    const fetchMock = mockApi({
      "GET /api/workspaces/garden": GARDEN,
      "PATCH /api/workspaces/garden": { ...GARDEN.workspace, name: "Kitchen kit" },
    });
    openAt("?view=project&ws=garden");
    const input = await screen.findByDisplayValue("Kitchen recipes");
    fireEvent.change(input, { target: { value: "Kitchen kit" } });
    fireEvent.blur(input);
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(true));
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ name: "Kitchen kit" });
  });

  it("edits the workspace's brand, the eyebrow over its animated headlines: its name on blur, then its accent", async () => {
    const brand = { name: "Kitchen equipment", accent: "#f5c518" };
    const patches: unknown[] = [];
    const fetchMock = mockApi({
      "GET /api/workspaces/garden": { ...GARDEN, workspace: { ...GARDEN.workspace, brand } },
      "PATCH /api/workspaces/garden": (init?: RequestInit) => {
        const body = JSON.parse(String(init?.body)) as { brand: Record<string, string | null> };
        patches.push(body);
        Object.assign(brand, { name: body.brand.name === null ? "Kitchen equipment" : body.brand.name ?? brand.name, accent: body.brand.accent ?? brand.accent });
        return { ...GARDEN.workspace, brand: { ...brand } };
      },
    });
    openAt("?view=project&ws=garden");
    const name = (await screen.findByRole("textbox", { name: "Brand name" })) as HTMLInputElement;
    const sample = screen.getByLabelText("Headline preview").querySelector("b") as HTMLElement;
    expect([name.value, sample.textContent, sample.style.color]).toEqual(["Kitchen equipment", "Kitchen equipment", "rgb(245, 197, 24)"]);

    fireEvent.change(name, { target: { value: "  WEEKNIGHT PASTA " } });
    fireEvent.blur(name);
    await waitFor(() => expect(patches).toEqual([{ brand: { name: "WEEKNIGHT PASTA" } }]));
    fireEvent.change(screen.getByLabelText("Brand accent"), { target: { value: "#2bb3a3" } });
    expect(sample.style.color).toBe("rgb(43, 179, 163)");
    await waitFor(() => expect(patches).toHaveLength(2), { timeout: 2000 });
    expect(patches[1]).toEqual({ brand: { accent: "#2bb3a3" } });
    await waitFor(() => expect(sample.textContent).toBe("WEEKNIGHT PASTA"));

    fireEvent.change(name, { target: { value: "" } });
    fireEvent.blur(name);
    await waitFor(() => expect(patches[2]).toEqual({ brand: { name: null } }));
    await waitFor(() => expect(name.value, "an empty name restores the default").toBe("Kitchen equipment"));
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PATCH")).toHaveLength(3);
  });

  it("opens the New-video-from-footage overlay", async () => {
    mockApi({ "GET /api/workspaces/garden": GARDEN });
    openAt("?view=project&ws=garden");
    await userEvent.click(await screen.findByRole("button", { name: "New video" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /From footage/ }));
    expect(screen.getByRole("dialog", { name: "New video from footage" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Add your footage" })).toBeTruthy();
  });

  it("opens the New-video-from-a-script sheet", async () => {
    mockApi({ "GET /api/workspaces/garden": GARDEN });
    openAt("?view=project&ws=garden");
    await userEvent.click(await screen.findByRole("button", { name: "New video" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /From a script/ }));
    expect(screen.getByRole("dialog", { name: "New video from a script" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Write the script" })).toBeTruthy();
  });

  it("shows a not-found state for an unknown workspace", async () => {
    mockApi({});
    openAt("?view=project&ws=missing");
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to workspaces" }).getAttribute("href")).toBe("/editor/");
  });
});
