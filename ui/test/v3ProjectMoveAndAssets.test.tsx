// @vitest-environment jsdom
// "Move to workspace…" for a video from Unsorted or another workspace, and a Workspace page
// asset tile's Rename/Tag/Delete (patched locally, not via a refetch — Library's own path is covered
// by v3LibraryMedia.test.tsx).
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Asset, Workspace, WorkspaceDetail } from "../src/v3/api";

function asset(id: string, name: string, extra: Partial<Asset> = {}): Asset {
  return { id: id.repeat(16), sha256: "", name, kind: "image", file: `${id.repeat(16)}.png`, bytes: 1, tags: [], createdAt: "", ...extra };
}

const WORKSPACES: Workspace[] = [
  { id: "brand", name: "Brand", createdAt: "", updatedAt: "", videoCount: 0, assetCount: 0 },
  { id: "garden", name: "Kitchen equipment", createdAt: "", updatedAt: "", videoCount: 0, assetCount: 1 },
  { id: "cafe", name: "Cafe Launch", createdAt: "", updatedAt: "", videoCount: 0, assetCount: 0 },
  { id: "unsorted", name: "Unsorted", createdAt: "", updatedAt: "", videoCount: 2, assetCount: 0 },
];

const assignVideo = vi.fn().mockResolvedValue({ project: {} });
const updateAsset = vi.fn();
const deleteAsset = vi.fn();
const assetUsage = vi.fn().mockResolvedValue({ videos: 0 });

function unsortedDetail(): WorkspaceDetail {
  return {
    workspace: { id: "unsorted", name: "Unsorted", createdAt: "", updatedAt: "", videoCount: 2, assetCount: 0 },
    videos: [{ slug: "clip-one", title: "Clip one", updatedAt: "" }, { slug: "clip-two", title: "Clip two", updatedAt: "" }],
    assets: [],
  };
}

function gardenDetail(): WorkspaceDetail {
  return {
    workspace: { id: "garden", name: "Kitchen equipment", createdAt: "", updatedAt: "", videoCount: 1, assetCount: 1 },
    videos: [{ slug: "herb-care", title: "herb care", updatedAt: "" }],
    assets: [asset("1", "seedling-plate.png")],
  };
}

vi.mock("../src/v3/api", async (importOriginal) => {
  const actual = await importOriginal<{ v3api: object }>();
  return {
    ...actual,
    v3api: {
      ...actual.v3api,
      workspaces: () => Promise.resolve(WORKSPACES),
      workspace: (id: string) => Promise.resolve(id === "garden" ? gardenDetail() : unsortedDetail()),
      assignVideo: (...args: [string, string]) => assignVideo(...args),
      updateAsset: (...args: [string, string, unknown]) => updateAsset(...args),
      deleteAsset: (...args: [string, string]) => deleteAsset(...args),
      assetUsage: (...args: [string, string]) => assetUsage(...args),
    },
  };
});

import Project from "../src/v3/Project";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Project page: move a video and manage its assets", () => {
  it("moves an Unsorted video into a picked project, dropping it from this list", async () => {
    render(<Project id="unsorted" />);
    const row = (await screen.findByText("Clip one")).closest(".vrow") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Move…" }));
    const menu = within(row).getByRole("menu");
    expect(within(menu).queryByRole("menuitem", { name: "Unsorted" })).toBeNull(); // already here
    await userEvent.click(within(menu).getByRole("menuitem", { name: "Cafe Launch" }));

    await waitFor(() => expect(assignVideo).toHaveBeenCalledWith("cafe", "clip-one"));
    await waitFor(() => expect(screen.queryByText("Clip one")).toBeNull());
    expect(screen.getByText("Clip two")).toBeTruthy();
  });

  it("offers Unsorted, and every other project, as a move destination from inside a project", async () => {
    render(<Project id="garden" />);
    const row = (await screen.findByText("herb care")).closest(".vrow") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Move…" }));
    const menu = within(row).getByRole("menu");
    expect(within(menu).getAllByRole("menuitem").map((b) => b.textContent)).toEqual(["Unsorted", "Cafe Launch"]);

    await userEvent.click(within(menu).getByRole("menuitem", { name: "Unsorted" }));
    await waitFor(() => expect(assignVideo).toHaveBeenCalledWith("unsorted", "herb-care"));
    await waitFor(() => expect(screen.queryByText("herb care")).toBeNull());
  });

  it("renames, tags and deletes an asset from the Project page, patching the list in place", async () => {
    updateAsset.mockImplementation((_ws: string, _id: string, patch: { name?: string; tags?: string[] }) =>
      Promise.resolve({ ...asset("1", "seedling-plate.png"), ...patch }));
    render(<Project id="garden" />);
    const tile = (await screen.findByText("seedling-plate.png")).closest("figure") as HTMLElement;

    await userEvent.click(within(tile).getByRole("button", { name: "Tag" }));
    await userEvent.type(within(tile).getByRole("textbox"), "garden, plates");
    await userEvent.click(within(tile).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(updateAsset).toHaveBeenCalledWith("garden", asset("1", "").id, { tags: ["garden", "plates"] }));
    expect(await within(tile).findByText("garden")).toBeTruthy();
    expect(within(tile).getByText("plates")).toBeTruthy();

    await userEvent.click(within(tile).getByRole("button", { name: "Delete" }));
    expect(within(tile).getByText(/Not used in any video/)).toBeTruthy();
    await userEvent.click(within(tile).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleteAsset).toHaveBeenCalledWith("garden", asset("1", "").id));
    await waitFor(() => expect(screen.queryByText("seedling-plate.png")).toBeNull());
  });
});
