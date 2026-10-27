// @vitest-environment jsdom
// The context bar identifies the current Workspace on every screen.
// Its breadcrumb opens that Workspace, and its menu switches Workspaces.
// Archived Workspaces stay out of the menu unless one is currently open.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Workspace } from "../src/v3/api";

vi.mock("../src/v3/story/StoryView", () => ({ default: ({ video }: { video: string }) => <div>story of {video}</div> }));

import V3App from "../src/v3/V3App";
import { switchRoute } from "../src/v3/ProjectSwitcher";
import { resetCast } from "../src/v3/library/castStore";
import { useProjectStore } from "../src/store/useProjectStore";
import type { Project } from "../src/types/project";

const NOW = "2026-09-20T10:00:00Z";
const workspace = (id: string, name: string, videoCount = 0): Workspace => ({ id, name, createdAt: NOW, updatedAt: NOW, videoCount, assetCount: 0 });
const WORKSPACES = [
  workspace("garden-1", "Weeknight Pasta", 1), workspace("garden", "Kitchen equipment", 2), { ...workspace("old", "Old series", 3), archived: true },
  workspace("brand", "Brand"), workspace("unsorted", "Unsorted", 4),
];
const DEFAULT_RIG = { id: "default-character", name: "Lens", rig: "lens", art: [], palette: "periwinkle", approved: true, expressions: [], workspaceId: "brand", createdAt: "" };
const GUEST = { ...DEFAULT_RIG, id: "notes-guest", name: "Notes Guest", rig: "lens", palette: "teal", workspaceId: "garden-1" };

function mockApi(extra: Record<string, unknown> = {}) {
  const routes: Record<string, unknown> = {
    "GET /api/workspaces": WORKSPACES,
    "GET /api/cast": { characters: [DEFAULT_RIG], default: DEFAULT_RIG.id, workspace: null },
    "GET /api/cast?workspace=garden-1": { characters: [DEFAULT_RIG, GUEST], default: DEFAULT_RIG.id, workspace: { id: "garden-1", name: "Weeknight Pasta" } },
    "GET /api/workspaces/garden-1": { workspace: WORKSPACES[0], videos: [], assets: [] },
    "GET /api/workspaces/garden": { workspace: WORKSPACES[1], videos: [], assets: [] },
    ...extra,
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    const route = routes[key];
    return key in routes
      ? new Response(JSON.stringify(typeof route === "function" ? route(init) : route), { status: 200, headers: { "Content-Type": "application/json" } })
      : new Response(JSON.stringify({ error: `no route ${key}` }), { status: 404 });
  }));
}

function openAt(search: string) {
  window.history.replaceState(null, "", `/editor/${search}`);
  return render(<V3App />);
}

const bar = () => screen.getByRole("banner", { name: "Workspace context" });
const crumbs = () => within(bar()).getByRole("navigation", { name: "Breadcrumb" });
const trail = () => Array.from(crumbs().querySelectorAll(".crumb")).map((c) => c.textContent);
const here = () => crumbs().querySelector('[aria-current="page"]')?.textContent;
const switcher = () => within(bar()).getByRole("button", { name: "Switch workspace" });
const rail = () => within(screen.getByRole("navigation", { name: "Spaces" }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  resetCast();
  useProjectStore.setState({ project: null });
  window.history.replaceState(null, "", "/");
});

describe("workspace context", () => {
  it("is Home on Home, and Home › the workspace on its page, keeping it on the rail's Library link", async () => {
    mockApi();
    openAt("");
    expect(trail()).toEqual(["Home"]);
    expect(here()).toBe("Home");
    expect(switcher().getAttribute("aria-haspopup")).toBe("menu");
    cleanup();
    openAt("?view=project&ws=garden-1");
    await waitFor(() => expect(trail()).toEqual(["Home", "Weeknight Pasta"]));
    expect(here()).toBe("Weeknight Pasta");
    expect(within(bar()).getByRole("link", { name: "Home" }).getAttribute("href")).toBe("/editor/");
    expect(rail().getByRole("link", { name: "Library" }).getAttribute("href")).toBe("/editor/?view=library&ws=garden-1");
    expect(rail().getByRole("link", { name: "Home" }).getAttribute("href")).toBe("/editor/");
  });

  it("switches from the ▾ to another workspace, or back to all of them, from any screen; the Library stays the Library", async () => {
    mockApi();
    openAt("?view=project&ws=garden-1");
    await userEvent.click(switcher());
    const menu = await screen.findByRole("menu", { name: "Workspaces" });
    await waitFor(() => expect(within(menu).getAllByRole("menuitemradio").map((b) => b.querySelector("b")?.textContent)).toEqual(["All workspaces", "Weeknight Pasta", "Kitchen equipment"]));
    expect(within(menu).getByRole("menuitemradio", { name: /Weeknight Pasta/ }).getAttribute("aria-checked")).toBe("true");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu", { name: "Workspaces" })).toBeNull();
    expect(document.activeElement).toBe(switcher());
    await userEvent.click(switcher());
    await userEvent.click(await screen.findByRole("menuitemradio", { name: /Kitchen equipment/ }));
    expect(window.location.search).toBe("?view=project&ws=garden");
    expect(switchRoute({ view: "library", ws: "garden" }, "garden-1")).toEqual({ view: "library", ws: "garden-1" });
    expect(switchRoute({ view: "library", ws: "garden" }, undefined)).toEqual({ view: "library" });
    expect(switchRoute({ view: "story", ws: "garden", video: "herb-care" }, "garden-1")).toEqual({ view: "project", ws: "garden-1" });
    expect(switchRoute({ view: "project", ws: "garden" }, undefined)).toEqual({ view: "home" });
  });

  it("keeps an archived workspace out of the switch menu unless it is the one you are in", async () => {
    mockApi();
    openAt("?view=project&ws=old");
    await waitFor(() => expect(trail()).toEqual(["Home", "Old series"]));
    await userEvent.click(switcher());
    const menu = await screen.findByRole("menu", { name: "Workspaces" });
    await waitFor(() => expect(within(menu).getByRole("menuitemradio", { name: /Old series/ }).textContent).toContain("Archived · 3 videos"));
    cleanup();
    openAt("?view=project&ws=garden");
    await userEvent.click(switcher());
    await screen.findByRole("menuitemradio", { name: /Kitchen equipment/ });
    expect(screen.queryByRole("menuitemradio", { name: /Old series/ })).toBeNull();
  });

  it("names a Story video's own workspace in the route and the breadcrumb, whose crumb goes back to that workspace", async () => {
    mockApi();
    const video: Project = { fps: 30, width: 1920, height: 1080, name: "herb-care", title: "herb care basics", revision: 1, schemaVersion: 3, tracks: [], workspaceId: "garden" };
    useProjectStore.setState({ project: video });
    openAt("?view=story&video=herb-care");
    await waitFor(() => expect(window.location.search).toBe("?view=story&ws=garden&video=herb-care"));
    await waitFor(() => expect(trail()).toEqual(["Home", "Kitchen equipment", "herb care basics"]));
    expect(here()).toBe("herb care basics");
    expect(rail().getByRole("link", { name: "Library" }).getAttribute("href")).toBe("/editor/?view=library&ws=garden&video=herb-care");
    const back = within(bar()).getByRole("link", { name: "Kitchen equipment" });
    expect(back.getAttribute("href")).toBe("/editor/?view=project&ws=garden");
    await userEvent.click(back);
    expect(window.location.search).toBe("?view=project&ws=garden");
    await waitFor(() => expect(trail()).toEqual(["Home", "Kitchen equipment"]));
    cleanup();
    useProjectStore.setState({ project: { ...video, workspaceId: undefined } });
    openAt("?view=story&video=herb-care");
    await waitFor(() => expect(window.location.search).toBe("?view=story&ws=unsorted&video=herb-care"));
    await waitFor(() => expect(trail()).toEqual(["Home", "Unsorted", "herb care basics"]));
  });

  it("follows a rename on the Workspace page", async () => {
    let name = "Weeknight Pasta";
    mockApi({
      "GET /api/workspaces": () => WORKSPACES.map((w) => (w.id === "garden-1" ? { ...w, name } : w)),
      "PATCH /api/workspaces/garden-1": (init?: RequestInit) => {
        name = (JSON.parse(String(init?.body)) as { name: string }).name;
        return { ...WORKSPACES[0], name };
      },
    });
    openAt("?view=project&ws=garden-1");
    const input = await screen.findByDisplayValue("Weeknight Pasta");
    fireEvent.change(input, { target: { value: "Garden" } });
    fireEvent.blur(input);
    await waitFor(() => expect(trail()).toEqual(["Home", "Garden"]));
  });

  it("tells the Library's two states apart: the brand cast from Home, the brand's and the workspace's from inside it", async () => {
    mockApi();
    HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
    openAt("?view=library");
    const scope = await screen.findByRole("note", { name: "Characters shown" });
    await waitFor(() => expect(scope.textContent).toContain("Showing: Brand only · pick a workspace to see its characters"));
    expect(trail()).toEqual(["Home", "Library"]);
    expect(within(scope).getByRole("button", { name: "Pick a workspace: All workspaces" })).toBeTruthy();
    expect(screen.queryByText("Notes Guest")).toBeNull();
    await userEvent.click(within(scope).getByRole("button", { name: /Pick a workspace/ }));
    await userEvent.click(within(scope).getByRole("menuitemradio", { name: /Weeknight Pasta/ }));
    expect(window.location.search).toBe("?view=library&ws=garden-1");
    await waitFor(() => expect(screen.getByRole("note", { name: "Characters shown" }).textContent).toContain("Showing: Brand + Weeknight Pasta"));
    await waitFor(() => expect(trail()).toEqual(["Home", "Weeknight Pasta", "Library"]));
    const cards = within(screen.getByRole("group", { name: "Characters" })).getAllByRole("button").filter((b) => b.hasAttribute("data-character"));
    expect(cards.map((b) => b.textContent)).toEqual(["LensBrand · Default", "Notes GuestWeeknight Pasta"]);
  });
});
