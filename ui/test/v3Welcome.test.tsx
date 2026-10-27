// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import Home from "../src/v3/Home";
import type { Workspace } from "../src/v3/api";

let workspaces: Workspace[];
const builtin = (id: string): Workspace => ({ id, name: id, createdAt: "", updatedAt: "", videoCount: 0, assetCount: 0 });
const addons = ["transcription", "assistant"].map((name) => ({ name,
  title: name === "transcription" ? "Transcription" : "Assistant", state: "not installed", available: false,
  restartRequired: false, installedBytes: 0, sourceInstallation: false, tier: null, jobId: null,
  tiers: [{ tier: name === "transcription" ? "cpu" : "standard", label: name === "transcription" ? "CPU" : "Standard",
    supported: true, approximateBytes: name === "transcription" ? 4e9 : 94e6, acceptance: "terms",
    declaration: { licenses: [{ label: "Applicable terms", url: "https://example.org/terms" }] } }],
}));

beforeEach(() => {
  workspaces = [builtin("brand"), builtin("unsorted")];
  localStorage.clear();
  window.history.replaceState(null, "", "/editor/");
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  vi.stubGlobal("EventSource", vi.fn());
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const data = url === "/api/workspaces" ? init?.method === "POST" ? { ...builtin("new"), name: "My Workspace" } : workspaces
      : url === "/api/health" ? { capabilities: { packaged: true, transcription: addons[0], assistant: addons[1] } }
      : url === "/api/addons" ? { addons, recommendedTier: "cpu", gpus: [] } : [];
    return new Response(JSON.stringify(data), { status: 200 });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); localStorage.clear(); });

it("welcomes empty built-ins, persists independent dismissals and creates a Workspace without setup", async () => {
  const view = render(<Home />);
  await screen.findByRole("heading", { name: "Welcome to Cutgraph" });
  const transcription = await screen.findByRole("article", { name: "Transcription" });
  expect(within(transcription).getByText(/CPU · detected tier · about 4.00 GB/)).toBeTruthy();
  expect(within(transcription).getByRole("button", { name: "Install transcription" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Dismiss Transcription setup" }));
  expect(screen.queryByRole("article", { name: "Transcription" })).toBeNull();
  expect(screen.getByRole("article", { name: "Assistant" })).toBeTruthy();
  expect(EventSource).not.toHaveBeenCalled();
  view.unmount();
  render(<Home />);
  await screen.findByRole("article", { name: "Assistant" });
  expect(screen.queryByRole("article", { name: "Transcription" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Create your first Workspace/ }));
  fireEvent.change(screen.getByLabelText("Workspace name"), { target: { value: "My Workspace" } });
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await vi.waitFor(() => expect(window.location.search).toBe("?view=project&ws=new"));
});

it("offers the existing licence acceptance before a welcome download", async () => {
  render(<Home />);
  fireEvent.click(await screen.findByRole("button", { name: "Install transcription" }));
  const card = screen.getByRole("article", { name: "Transcription" });
  expect(within(card).getByRole("link", { name: "Applicable terms" })).toBeTruthy();
  expect(within(card).getByRole("button", { name: "Install add-on" })).toHaveProperty("disabled", true);
});

it.each(["created", "content"])("replaces the welcome for %s Workspaces", async (kind) => {
  if (kind === "created") workspaces.push(builtin("my-workspace"));
  else workspaces[0].videoCount = 1;
  render(<Home />);
  await screen.findByRole("button", { name: /New workspace/ });
  expect(screen.queryByRole("heading", { name: "Welcome to Cutgraph" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Optional setup" })).toBeNull();
});
