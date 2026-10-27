// @vitest-environment jsdom
// the Library's B-roll/Music/Sounds tabs show the open project's own media alongside the
// brand workspace's, clearly grouped, and each tile's Rename/Tag/Delete persist and (for delete) warn
// when a video uses the asset.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Asset, WorkspaceDetail } from "../src/v3/api";

function asset(id: string, name: string, extra: Partial<Asset> = {}): Asset {
  return { id: id.repeat(16), sha256: "", name, kind: "image", file: `${id.repeat(16)}.png`, bytes: 1, tags: [], createdAt: "", ...extra };
}

let gardenAssets: Asset[];
let brandAssets: Asset[];
const assetUsage = vi.fn();

function detail(id: string, name: string, assets: Asset[]): WorkspaceDetail {
  return { workspace: { id, name, createdAt: "", updatedAt: "", videoCount: 0, assetCount: assets.length }, videos: [], assets };
}

function findAndPatch(list: Asset[], id: string, patch: { name?: string; tags?: string[] }): Asset {
  const i = list.findIndex((a) => a.id === id);
  list[i] = { ...list[i], ...patch };
  return list[i];
}

vi.mock("../src/v3/api", async (importOriginal) => {
  const actual = await importOriginal<{ v3api: object }>();
  return {
    ...actual,
    v3api: {
      ...actual.v3api,
      workspaces: () => Promise.resolve([]),
      workspace: (id: string) => Promise.resolve(id === "brand" ? detail("brand", "Brand", brandAssets) : detail(id, "garden", gardenAssets)),
      assetThumbUrl: (ws: string, id: string) => `/api/workspaces/${ws}/assets/${id}/thumb`,
      updateAsset: (ws: string, id: string, patch: { name?: string; tags?: string[] }) =>
        Promise.resolve(findAndPatch(ws === "brand" ? brandAssets : gardenAssets, id, patch)),
      deleteAsset: (ws: string, id: string) => {
        const list = ws === "brand" ? brandAssets : gardenAssets;
        const i = list.findIndex((a) => a.id === id);
        if (i >= 0) list.splice(i, 1);
        return Promise.resolve();
      },
      assetUsage: (...args: [string, string]) => assetUsage(...args),
      cast: () => Promise.resolve({ characters: [], default: "default-character", workspace: null }),
    },
  };
});

import Library from "../src/v3/Library";

HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;

beforeEach(() => {
  gardenAssets = [asset("1", "seedling-plate.png")];
  brandAssets = [asset("2", "logo.png"), asset("3", "sting.wav", { kind: "audio", durationS: 3, tags: ["sfx"] })];
  assetUsage.mockReset();
});

afterEach(cleanup);

describe("Library media tabs", () => {
  it("shows imported stock attribution with author and source links", async () => {
    gardenAssets = [asset("1", "sample-photo.png", { attribution: {
      provider: "pexels", author: "Sample Photographer", authorUrl: "https://www.pexels.com/@example",
      sourceUrl: "https://www.pexels.com/photo/example-900001/", license: "Pexels License",
    } })];
    render(<Library ws="garden" />);
    await userEvent.click(screen.getByRole("button", { name: "B-roll" }));
    const author = await screen.findByRole("link", { name: "Sample Photographer" });
    expect(author.parentElement?.textContent).toBe("Photo by Sample Photographer on Pexels");
    expect(author.getAttribute("href")).toBe("https://www.pexels.com/@example");
    const source = screen.getByRole("link", { name: "Pexels" });
    expect(source.getAttribute("href")).toBe("https://www.pexels.com/photo/example-900001/");
    expect(source.getAttribute("rel")).toBe("noopener noreferrer");
    expect(source.getAttribute("target")).toBe("_blank");
  });

  it("groups the open project's B-roll with the brand's, each under its own heading", async () => {
    render(<Library ws="garden" />);
    await userEvent.click(screen.getByRole("button", { name: "B-roll" }));
    const groups = await screen.findAllByRole("heading", { level: 3 });
    expect(groups.map((h) => h.textContent)).toEqual(["garden", "Brand"]);
    expect(screen.getByText("seedling-plate.png")).toBeTruthy();
    expect(screen.getByText("logo.png")).toBeTruthy();
  });

  it("plays a video clip's tile while hovered, as the drawer's do", async () => {
    gardenAssets.push(asset("4", "seedling.mp4", { kind: "video", file: `${"4".repeat(16)}.mp4`, durationS: 3 }));
    render(<Library ws="garden" />);
    await userEvent.click(screen.getByRole("button", { name: "B-roll" }));
    const tile = (await screen.findByText("seedling.mp4")).closest("figure") as HTMLElement;
    const photo = screen.getByText("seedling-plate.png").closest("figure") as HTMLElement;
    await userEvent.hover(tile);
    expect(tile.querySelector("video")?.getAttribute("src")).toBe(`/api/workspaces/garden/assets/${"4".repeat(16)}/file`);
    await userEvent.unhover(tile);
    expect(tile.querySelector("video")).toBeNull();
    await userEvent.hover(photo);
    expect(photo.querySelector("video")).toBeNull();
  });

  it("shows only the brand's media with no project open", async () => {
    render(<Library />);
    await userEvent.click(screen.getByRole("button", { name: "B-roll" }));
    const groups = await screen.findAllByRole("heading", { level: 3 });
    expect(groups.map((h) => h.textContent)).toEqual(["Brand"]);
    expect(screen.queryByText("seedling-plate.png")).toBeNull();
  });

  it("sorts an audio asset tagged sfx into Sounds, not Music", async () => {
    render(<Library ws="garden" />);
    await userEvent.click(screen.getByRole("button", { name: "Sounds" }));
    expect(await screen.findByText("sting.wav")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Music" }));
    expect(screen.queryByText("sting.wav")).toBeNull();
  });

  it("renames, tags and deletes an asset from its tile, with a confirm that names videos in use", async () => {
    assetUsage.mockResolvedValue({ videos: 2 });
    render(<Library ws="garden" />);
    await userEvent.click(screen.getByRole("button", { name: "B-roll" }));
    let tile = (await screen.findByText("logo.png")).closest("figure") as HTMLElement;

    await userEvent.click(within(tile).getByRole("button", { name: "Rename" }));
    const nameInput = within(tile).getByRole("textbox");
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, "Brand mark");
    await userEvent.click(within(tile).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByText("Brand mark")).toBeTruthy());
    tile = screen.getByText("Brand mark").closest("figure") as HTMLElement;

    await userEvent.click(within(tile).getByRole("button", { name: "Tag" }));
    await userEvent.type(within(tile).getByRole("textbox"), "sfx");
    await userEvent.click(within(tile).getByRole("button", { name: "Save" }));
    await screen.findByText("Photo · sfx");
    tile = screen.getByText("Brand mark").closest("figure") as HTMLElement;

    await userEvent.click(within(tile).getByRole("button", { name: "Delete" }));
    expect(await within(tile).findByText(/Used in 2 videos/)).toBeTruthy();
    await userEvent.click(within(tile).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByText("Brand mark")).toBeNull());
  });
});
