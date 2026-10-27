// @vitest-environment jsdom
// Home lists Workspaces first and remembers each viewer's sort order.
// Each card supports rename, archive and deletion of an empty Workspace.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Workspace } from "../src/v3/api";

import Home from "../src/v3/Home";
import ToastHost from "../src/v3/Toast";

const workspace = (id: string, name: string, videoCount: number, assetCount: number, lastUsedAt: string | null, archived = false): Workspace => ({
  id, name, createdAt: "2026-09-01T00:00:00Z", updatedAt: "2026-09-01T00:00:00Z", videoCount, assetCount, lastUsedAt, archived,
});

let list: Workspace[];
type Reply = { status: number; body: unknown };
type Handler = (init?: RequestInit) => Reply;

function mockApi(handlers: Record<string, Handler> = {}) {
  const routes: Record<string, Handler> = {
    "GET /api/workspaces": () => ({ status: 200, body: list }),
    "GET /api/videos/recent?limit=8": () => ({ status: 200, body: [] }),
    ...handlers,
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    const { status, body } = routes[key]?.(init) ?? { status: 404, body: { error: `no route ${key}` } };
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const grid = () => screen.getByRole("heading", { name: "Workspaces" }).closest("section")!.querySelector(".recent") as HTMLElement;
const cardNames = () => Array.from(grid().querySelectorAll(".ws-card b")).map((b) => b.textContent);
const sent = (fetchMock: ReturnType<typeof mockApi>, method: string) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === method).map(([url, init]) => [String(url), init?.body ? JSON.parse(String(init.body)) : null]);

function renderHome() {
  return render(
    <>
      <Home />
      <ToastHost />
    </>,
  );
}

HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;

beforeEach(() => {
  list = [
    workspace("supplies", "Kitchen equipment", 2, 3, "2026-09-20T10:00:00Z"),
    workspace("garden", "Weeknight Pasta", 5, 0, "2026-09-10T10:00:00Z"),
    workspace("cafe", "Cafe launch", 0, 0, "2026-09-22T10:00:00Z"),
    workspace("brand", "Brand", 0, 4, "2026-09-23T10:00:00Z"),
    workspace("unsorted", "Unsorted", 9, 0, "2026-09-24T10:00:00Z"),
  ];
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Home workspaces: sort", () => {
  it("sorts by Recently used by default, the built-ins after, and remembers another sort across a reload", async () => {
    mockApi();
    renderHome();
    await waitFor(() => expect(cardNames()).toEqual(["Cafe launch", "Kitchen equipment", "Weeknight Pasta", "Brand", "Unsorted"]));
    const sort = screen.getByRole("combobox", { name: "Sort workspaces" });
    await userEvent.selectOptions(sort, "Name A–Z");
    expect(cardNames()).toEqual(["Cafe launch", "Kitchen equipment", "Weeknight Pasta", "Brand", "Unsorted"]);
    await userEvent.selectOptions(sort, "Most videos");
    expect(cardNames()).toEqual(["Weeknight Pasta", "Kitchen equipment", "Cafe launch", "Brand", "Unsorted"]);
    expect(localStorage.getItem("cg3-workspace-sort")).toBe("videos");
    cleanup();
    renderHome();
    await waitFor(() => expect(cardNames()).toEqual(["Weeknight Pasta", "Kitchen equipment", "Cafe launch", "Brand", "Unsorted"]));
    expect((screen.getByRole("combobox", { name: "Sort workspaces" }) as HTMLSelectElement).value).toBe("videos");
  });

  it("falls back to Recently used when storage is unavailable", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    mockApi();
    renderHome();
    await waitFor(() => expect(cardNames()[0]).toBe("Cafe launch"));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Sort workspaces" }), "Name A–Z");
    expect(cardNames()[0]).toBe("Cafe launch");
  });
});

describe("Home workspaces: the ⋯ menu", () => {
  it("sits beside the card's link, never inside it, and only on client workspaces", async () => {
    mockApi();
    renderHome();
    const more = await screen.findByRole("button", { name: "Actions for Kitchen equipment" });
    expect(more.closest("a")).toBeNull();
    expect(screen.getByRole("link", { name: /Kitchen equipment/ }).getAttribute("href")).toBe("/?view=project&ws=supplies");
    expect(screen.queryByRole("button", { name: "Actions for Brand" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Actions for Unsorted" })).toBeNull();
    await userEvent.click(more);
    const menu = screen.getByRole("menu", { name: "Kitchen equipment" });
    expect(within(menu).getAllByRole("menuitem").map((b) => b.textContent)).toEqual(["Rename", "Archive", "Delete"]);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(more);
  });

  it("renames a workspace in place", async () => {
    const fetchMock = mockApi({
      "PATCH /api/workspaces/supplies": () => ({ status: 200, body: { ...list[0], name: "Kitchen kit", updatedAt: "2026-09-24T12:00:00Z" } }),
    });
    renderHome();
    await userEvent.click(await screen.findByRole("button", { name: "Actions for Kitchen equipment" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const input = screen.getByRole("textbox", { name: "Rename Kitchen equipment" });
    await userEvent.clear(input);
    await userEvent.type(input, "Kitchen kit{Enter}");
    await waitFor(() => expect(cardNames()[0], "renaming is a use, so it leads Recently used").toBe("Kitchen kit"));
    expect(sent(fetchMock, "PATCH")).toEqual([["/api/workspaces/supplies", { name: "Kitchen kit" }]]);
  });

  it("archives a workspace off Home, shows it under Show archived, and restores it", async () => {
    const fetchMock = mockApi({
      "PATCH /api/workspaces/cafe": (init) => ({ status: 200, body: { ...list[2], ...JSON.parse(String(init?.body)) } }),
    });
    renderHome();
    expect(screen.queryByRole("button", { name: /Show archived/ })).toBeNull();
    await userEvent.click(await screen.findByRole("button", { name: "Actions for Cafe launch" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    await waitFor(() => expect(cardNames()).not.toContain("Cafe launch"));
    expect(await screen.findByText("Archived “Cafe launch”")).toBeTruthy();

    const toggle = screen.getByRole("button", { name: /Show archived/ });
    expect(toggle.textContent).toBe("Show archived 1");
    await userEvent.click(toggle);
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    const archived = screen.getByRole("region", { name: "Archived" });
    expect(within(archived).getByRole("link", { name: /Cafe launch/ })).toBeTruthy();
    await userEvent.click(within(archived).getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(cardNames()).toContain("Cafe launch"));
    expect(screen.queryByRole("region", { name: "Archived" })).toBeNull();
    expect(sent(fetchMock, "PATCH")).toEqual([["/api/workspaces/cafe", { archived: true }], ["/api/workspaces/cafe", { archived: false }]]);
  });

  it("deletes an empty workspace after a confirm step", async () => {
    const fetchMock = mockApi({ "DELETE /api/workspaces/cafe": () => ({ status: 200, body: { ok: true } }) });
    renderHome();
    await userEvent.click(await screen.findByRole("button", { name: "Actions for Cafe launch" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    expect(screen.getByText("Delete “Cafe launch”? This can't be undone.")).toBeTruthy();
    expect(sent(fetchMock, "DELETE")).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(cardNames()).not.toContain("Cafe launch"));
    expect(sent(fetchMock, "DELETE")).toEqual([["/api/workspaces/cafe", null]]);
    expect(await screen.findByText("Deleted “Cafe launch”")).toBeTruthy();
  });

  it("explains that a workspace with videos or assets must be emptied or archived first", async () => {
    const fetchMock = mockApi();
    renderHome();
    await userEvent.click(await screen.findByRole("button", { name: "Actions for Kitchen equipment" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    expect(screen.getByText("“Kitchen equipment” still has 2 videos and 3 assets. Move or delete them first, or archive the workspace instead.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
    expect(screen.getByRole("button", { name: "Archive" })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(screen.getByRole("link", { name: /Kitchen equipment/ })).toBeTruthy();
    expect(sent(fetchMock, "DELETE")).toEqual([]);
  });

  it("shows the studio's reason when it refuses a delete, and keeps the workspace", async () => {
    const reason = "“Cafe launch” still has 1 character. Move or delete them first, or archive the workspace instead.";
    mockApi({ "DELETE /api/workspaces/cafe": () => ({ status: 409, body: { error: "workspace is not empty", reason } }) });
    renderHome();
    await userEvent.click(await screen.findByRole("button", { name: "Actions for Cafe launch" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText(reason)).toBeTruthy();
    expect(screen.getByRole("link", { name: /Cafe launch/ })).toBeTruthy();
    expect(cardNames()).toContain("Cafe launch");
  });
});
