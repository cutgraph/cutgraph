// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { StockItem, StockProviders, StockSearchResult } from "../src/v3/api";

const stockProviders = vi.fn<() => Promise<StockProviders>>();
const searchStock = vi.fn<(query: string, provider: string, page: number) => Promise<StockSearchResult>>();
const importStock = vi.fn<(workspace: string, item: StockItem) => Promise<{ asset: { name: string }; deduped: boolean }>>();

vi.mock("../src/v3/api", () => ({
  v3api: {
    stockProviders: (...args: unknown[]) => stockProviders(...(args as [])),
    searchStock: (...args: unknown[]) => searchStock(...(args as [string, string, number])),
    importStock: (...args: unknown[]) => importStock(...(args as [string, StockItem])),
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

import StockPanel from "../src/v3/story/drawer/stock/StockPanel";
import ToastHost from "../src/v3/Toast";

function video(id: string, over: Partial<StockItem> = {}): StockItem {
  return {
    id, provider: "pexels", kind: "video", thumbUrl: `https://images.pexels.com/${id}.jpg`,
    previewUrl: `https://videos.pexels.com/${id}-preview.mp4`, downloadUrl: `https://videos.pexels.com/${id}.mp4`,
    width: 1920, height: 1080, durationS: 12, author: "Jane Doe", authorUrl: "https://www.pexels.com/@jane",
    sourceUrl: `https://www.pexels.com/video/${id}/`, license: "Pexels License", ...over,
  };
}

function Panel(props: Partial<Parameters<typeof StockPanel>[0]> = {}) {
  return (
    <div className="v3 story">
      <ToastHost />
      <StockPanel workspaceId="garden-1a2b3c" onAdded={() => {}} onClose={() => {}} {...props} />
    </div>
  );
}

beforeEach(() => {
  stockProviders.mockReset();
  searchStock.mockReset();
  importStock.mockReset();
});
afterEach(cleanup);

describe("StockPanel: no key configured", () => {
  it("shows a clear call to action pointing at Settings, and never renders a search box", async () => {
    stockProviders.mockResolvedValue({ pexels: { configured: false }, pixabay: { configured: false } });
    render(<Panel />);
    expect(await screen.findByText(/Add your Pexels or Pixabay key in/, { selector: "p" })).toBeTruthy();
    const link = screen.getByRole("link", { name: "Settings" });
    expect(link.getAttribute("href")).toContain("view=settings");
    expect(screen.queryByPlaceholderText("Search stock video & photos…")).toBeNull();
    expect(searchStock).not.toHaveBeenCalled();
  });

  it("works with just one provider configured", async () => {
    stockProviders.mockResolvedValue({ pexels: { configured: false }, pixabay: { configured: true } });
    render(<Panel />);
    expect(await screen.findByPlaceholderText("Search stock video & photos…")).toBeTruthy();
  });
});

describe("StockPanel: search results grid", () => {
  beforeEach(() => {
    stockProviders.mockResolvedValue({ pexels: { configured: true }, pixabay: { configured: false } });
  });

  it("renders a merged results grid and pages with More", async () => {
    searchStock.mockResolvedValueOnce({
      results: [video("pexels:video:1"), video("pexels:video:2", { kind: "photo", durationS: null, author: "Ann" })],
      page: 1, hasMore: true, errors: {},
    });
    render(<Panel />);
    fireEvent.change(await screen.findByPlaceholderText("Search stock video & photos…"), { target: { value: "garden" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => expect(searchStock).toHaveBeenCalledWith("garden", "all", 1));
    expect(await screen.findByRole("link", { name: "Jane Doe" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Ann" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Jane Doe" }).getAttribute("href")).toBe("https://www.pexels.com/@jane");
    const itemLinks = screen.getAllByRole("link", { name: "Pexels" });
    expect(itemLinks.map((link) => link.getAttribute("href"))).toEqual([
      "https://www.pexels.com/video/pexels:video:1/", "https://www.pexels.com/video/pexels:video:2/",
    ]);
    expect(screen.getByRole("link", { name: "Photos and videos provided by Pexels" }).getAttribute("href")).toBe("https://www.pexels.com");
    expect(screen.getByRole("link", { name: "Pixabay" }).getAttribute("href")).toBe("https://pixabay.com");
    for (const link of screen.getAllByRole("link")) {
      expect(link.getAttribute("target")).toBe("_blank");
      expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    }
    expect(screen.getByText("0:12")).toBeTruthy(); // the video tile's duration badge

    searchStock.mockResolvedValueOnce({ results: [video("pexels:video:3")], page: 2, hasMore: false, errors: {} });
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    await waitFor(() => expect(searchStock).toHaveBeenCalledWith("garden", "all", 2));
    await waitFor(() => expect(screen.getAllByText("1920×1080")).toHaveLength(3)); // 2 kept + 1 appended, deduped by id
    expect(screen.queryByRole("button", { name: "More" })).toBeNull();
  });

  it("shows a friendly empty state and surfaces per-provider errors as toasts", async () => {
    searchStock.mockResolvedValue({ results: [], page: 1, hasMore: false, errors: { pixabay: "That API key was rejected. Check it in Settings." } });
    render(<Panel />);
    fireEvent.change(await screen.findByPlaceholderText("Search stock video & photos…"), { target: { value: "xyzzy" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText('No results for "xyzzy".')).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("That API key was rejected. Check it in Settings.");
  });

  it("links a Pixabay result to its item page", async () => {
    searchStock.mockResolvedValue({
      results: [video("example-photo", { provider: "pixabay", kind: "photo", author: "Sample Photographer",
        authorUrl: "https://pixabay.com/users/example-900101/", sourceUrl: "https://pixabay.com/photos/example-900001/" })],
      page: 1, hasMore: false, errors: {},
    });
    render(<Panel />);
    fireEvent.change(await screen.findByRole("textbox", { name: "Search stock" }), { target: { value: "sample" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    const author = await screen.findByRole("link", { name: "Sample Photographer" });
    expect(author.parentElement?.textContent).toBe("Photo by Sample Photographer on Pixabay");
    const sources = screen.getAllByRole("link", { name: "Pixabay" });
    expect(sources.some((link) => link.getAttribute("href") === "https://pixabay.com/photos/example-900001/")).toBe(true);
  });
});

describe("StockPanel: Add to library", () => {
  beforeEach(() => {
    stockProviders.mockResolvedValue({ pexels: { configured: true }, pixabay: { configured: false } });
    searchStock.mockResolvedValue({ results: [video("pexels:video:1")], page: 1, hasMore: false, errors: {} });
  });

  async function searched(): Promise<void> {
    render(<Panel />);
    fireEvent.change(await screen.findByPlaceholderText("Search stock video & photos…"), { target: { value: "garden" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await screen.findByRole("link", { name: "Jane Doe" });
  }

  it("downloads into the library, notifies the caller, and marks the tile added", async () => {
    const onAdded = vi.fn();
    importStock.mockResolvedValue({ asset: { name: "Pexels - Jane Doe.mp4" }, deduped: false });
    render(
      <div className="v3 story">
        <ToastHost />
        <StockPanel workspaceId="garden-1a2b3c" onAdded={onAdded} onClose={() => {}} />
      </div>,
    );
    fireEvent.change(await screen.findByPlaceholderText("Search stock video & photos…"), { target: { value: "garden" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await screen.findByRole("link", { name: "Jane Doe" });

    fireEvent.click(screen.getByRole("button", { name: "Add to library" }));
    await waitFor(() => expect(importStock).toHaveBeenCalledWith("garden-1a2b3c", expect.objectContaining({ id: "pexels:video:1" })));
    expect(await screen.findByRole("button", { name: "Added" })).toBeTruthy();
    expect(onAdded).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("status").textContent).toBe("Added to library: Pexels - Jane Doe.mp4");
  });

  it("opens a preview on click and can add from there too", async () => {
    importStock.mockResolvedValue({ asset: { name: "Pexels - Jane Doe.mp4" }, deduped: true });
    await searched();
    fireEvent.click(screen.getByRole("button", { name: "Preview by Jane Doe" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("link", { name: "Jane Doe" })).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Add to library" }));
    await waitFor(() => expect(importStock).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("status").textContent).toBe("Already in your library: Pexels - Jane Doe.mp4");
  });

  it("surfaces an import failure as a toast without marking the tile added", async () => {
    const { ApiError } = await import("../src/v3/api");
    importStock.mockRejectedValue(new ApiError(413, "413 That file is too large to import."));
    await searched();
    fireEvent.click(screen.getByRole("button", { name: "Add to library" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("413 That file is too large to import."));
    expect(screen.queryByRole("button", { name: "Added" })).toBeNull();
  });
});
