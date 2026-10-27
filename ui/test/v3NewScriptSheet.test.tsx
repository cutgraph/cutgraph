// @vitest-environment jsdom
// Home's "From an idea" and a workspace's "New video -> From a script" open the write
// sheet; its live preview counts scenes and time; Create posts the script and lands in Story.
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Workspace, WorkspaceDetail } from "../src/v3/api";

vi.mock("../src/v3/story/StoryView", () => ({ default: ({ video }: { video: string }) => <div>story of {video}</div> }));

import V3App from "../src/v3/V3App";
import { useNewScript } from "../src/v3/newscript/store";

const NOW = "2026-09-23T10:00:00Z";
const workspace = (id: string, name: string): Workspace => ({ id, name, createdAt: NOW, updatedAt: NOW, videoCount: 0, assetCount: 0 });
const EXPLAINERS: WorkspaceDetail = { workspace: workspace("explainers", "Explainers"), videos: [], assets: [] };

const SCRIPT = `## Hook: A quick supper
Have you ever cooked pasta with fresh garlic and a little lemon?

## Prepare the ingredients
Boil the water while someone chops garlic beside the kitchen sink.

## Serve the pasta
Fresh lemon adds brightness.`;

type Handler = unknown | ((init: RequestInit | undefined) => unknown);

function mockApi(routes: Record<string, Handler>) {
  routes = { "GET /api/health": { capabilities: { assistant: { available: true, verification: "ready", keySource: "saved" } } }, ...routes };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    if (!(key in routes)) return new Response(JSON.stringify({ error: `no route ${key}` }), { status: 404 });
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
  useNewScript.setState({
    open: false, wsId: "", title: "", script: "", busy: false, error: null,
    idea: "", drafting: false, draftError: null, titleEdited: false,
  });
});

describe("From an idea (Home) and From a script (Workspace page)", () => {
  it("Home asks for a workspace, then opens the write sheet for it", async () => {
    mockApi({ "GET /api/workspaces": [workspace("explainers", "Explainers"), workspace("unsorted", "Unsorted")] });
    openAt("");
    await userEvent.click(await screen.findByRole("button", { name: /From an idea/ }));
    const pick = await screen.findByRole("dialog", { name: "Which workspace is this video for?" });
    expect(within(pick).getByText("From an idea")).toBeTruthy();
    await userEvent.click(within(pick).getByRole("button", { name: "Explainers" }));
    const sheet = screen.getByRole("dialog", { name: "New video from a script" });
    expect(within(sheet).getByText("From an idea")).toBeTruthy();
    expect(useNewScript.getState().wsId).toBe("explainers");
  });

  it("the Workspace page's New video menu opens the same sheet", async () => {
    mockApi({ "GET /api/workspaces/explainers": EXPLAINERS });
    openAt("?view=project&ws=explainers");
    await userEvent.click(await screen.findByRole("button", { name: "New video" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /From a script/ }));
    const sheet = screen.getByRole("dialog", { name: "New video from a script" });
    expect(within(sheet).getByText("From a script")).toBeTruthy();
    expect(within(sheet).getByRole("heading", { name: "Write the script" })).toBeTruthy();
  });
});

describe("the write sheet", () => {
  it("previews scenes, roles and estimated length as you type, then creates the video and opens Story", async () => {
    const fetchMock = mockApi({
      "GET /api/workspaces/explainers": EXPLAINERS,
      "POST /api/script-video": { video: "Pasta", revision: 1, project: {} },
      "GET /api/project/Pasta/proxy": { state: "none", proxy: null },
    });
    useNewScript.getState().openSheet("explainers", "script");
    openAt("?view=project&ws=explainers");
    const sheet = screen.getByRole("dialog", { name: "New video from a script" });
    const create = within(sheet).getByRole("button", { name: "Create video" }) as HTMLButtonElement;
    expect(create.disabled).toBe(true);

    fireEvent.change(within(sheet).getByLabelText("Title"), { target: { value: "Pasta" } });
    fireEvent.change(within(sheet).getByLabelText("Script"), { target: { value: SCRIPT } });
    expect(within(sheet).getByText("3 scenes")).toBeTruthy();
    const scenes = within(within(sheet).getByRole("list", { name: "Scenes" })).getAllByRole("listitem");
    expect(scenes.map((li) => li.textContent)).toEqual([
      expect.stringMatching(/^HookA quick supper0:0\d$/),
      expect.stringMatching(/^BeatPrepare the ingredients0:0\d$/),
      expect.stringMatching(/^WrapServe the pasta0:0\d$/),
    ]);
    expect(within(sheet).getByText(/27 words · about 0:1\d/)).toBeTruthy();

    expect(create.disabled).toBe(false);
    await userEvent.click(create);
    await waitFor(() => expect(window.location.search).toBe("?view=story&ws=explainers&video=Pasta"));
    const post = fetchMock.mock.calls.find(([url, init]) => String(url) === "/api/script-video" && init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ title: "Pasta", script: SCRIPT, workspaceId: "explainers" });
    expect(screen.queryByRole("dialog", { name: "New video from a script" })).toBeNull();
    expect(await screen.findByText("story of Pasta")).toBeTruthy();
  });

  it("keeps the draft when it is closed, so a stray click outside loses nothing", async () => {
    mockApi({ "GET /api/workspaces/explainers": EXPLAINERS });
    useNewScript.getState().openSheet("explainers", "script");
    openAt("?view=project&ws=explainers");
    const sheet = screen.getByRole("dialog", { name: "New video from a script" });
    fireEvent.change(within(sheet).getByLabelText("Script"), { target: { value: SCRIPT } });
    fireEvent.mouseDown(sheet.parentElement as HTMLElement);
    expect(screen.queryByRole("dialog", { name: "New video from a script" })).toBeNull();
    act(() => useNewScript.getState().openSheet("explainers", "script"));
    expect((screen.getByLabelText("Script") as HTMLTextAreaElement).value).toBe(SCRIPT);
  });

  it("offers clearly-marked sample text and shows the server's refusal", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
      String(input) === "/api/script-video" && init?.method === "POST"
        ? new Response(JSON.stringify({ error: "Write at least one line of script." }), { status: 400 })
        : new Response(JSON.stringify(EXPLAINERS), { status: 200 })));
    useNewScript.getState().openSheet("explainers", "script");
    openAt("?view=project&ws=explainers");
    const sheet = screen.getByRole("dialog", { name: "New video from a script" });
    await userEvent.click(within(sheet).getByRole("button", { name: "Use the sample script" }));
    expect((within(sheet).getByLabelText("Title") as HTMLInputElement).value).toBe("A small balcony garden");
    expect(within(sheet).getByText(/Sample script: edit it/)).toBeTruthy();
    expect(within(sheet).getByText("6 scenes")).toBeTruthy();
    await userEvent.click(within(sheet).getByRole("button", { name: "Create video" }));
    expect((await within(sheet).findByRole("alert")).textContent).toContain("Write at least one line of script.");
    expect(window.location.search).toBe("?view=project&ws=explainers");
  });
});

describe("From an idea: ✦ Draft it", () => {
  it("explains a missing assistant while leaving manual script creation available", async () => {
    mockApi({ "GET /api/workspaces/explainers": EXPLAINERS,
      "GET /api/health": { capabilities: { packaged: true, assistant: { available: false, state: "not installed" } } } });
    useNewScript.getState().openSheet("explainers", "idea");
    openAt("?view=project&ws=explainers");
    expect(await screen.findByText("Assistant isn't installed.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "✦ Draft it" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Manual title" } });
    fireEvent.change(screen.getByLabelText("Script"), { target: { value: SCRIPT } });
    expect(screen.getByRole("button", { name: "Create video" })).toHaveProperty("disabled", false);
    fireEvent.click(screen.getByRole("link", { name: "Open Settings, Add-ons" }));
    expect(window.location.hash).toBe("#addons");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(useNewScript.getState().script).toBe(SCRIPT);
  });
  it("shows the idea box only for \"From an idea\", not for \"From a script\"", async () => {
    mockApi({ "GET /api/workspaces/explainers": EXPLAINERS });
    useNewScript.getState().openSheet("explainers", "script");
    openAt("?view=project&ws=explainers");
    const sheet = screen.getByRole("dialog", { name: "New video from a script" });
    expect(within(sheet).queryByLabelText("What's the video about?")).toBeNull();
  });

  it("drafts a script into the editor, showing the drafting state while the request is in flight", async () => {
    let resolveDraft!: (value: Response) => void;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      if (key === "GET /api/health") return new Response(JSON.stringify({ capabilities: { assistant: { available: true, verification: "ready", keySource: "saved" } } }), { status: 200 });
      if (key === "GET /api/workspaces/explainers") return new Response(JSON.stringify(EXPLAINERS), { status: 200 });
      if (key === "POST /api/script/draft") return new Promise<Response>((resolve) => { resolveDraft = resolve; });
      return new Response(JSON.stringify({ error: `no route ${key}` }), { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    useNewScript.getState().openSheet("explainers", "idea");
    openAt("?view=project&ws=explainers");
    const sheet = screen.getByRole("dialog", { name: "New video from a script" });
    const ideaBox = await within(sheet).findByLabelText("What's the video about?") as HTMLTextAreaElement;
    const draftIt = within(sheet).getByRole("button", { name: "✦ Draft it" }) as HTMLButtonElement;
    expect(draftIt.disabled).toBe(true);

    fireEvent.change(ideaBox, { target: { value: "how to cook pasta" } });
    expect(draftIt.disabled).toBe(false);
    await userEvent.click(draftIt);
    expect(within(sheet).getByText(/Drafting your script with Claude/)).toBeTruthy();
    expect(ideaBox.disabled).toBe(true);
    expect((within(sheet).getByRole("button", { name: "Drafting…" }) as HTMLButtonElement).disabled).toBe(true);

    const post = fetchMock.mock.calls.find(([url, init]) => String(url) === "/api/script/draft" && init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ idea: "how to cook pasta" });

    act(() => resolveDraft(new Response(JSON.stringify({ title: "A Simple Pasta Supper", script: SCRIPT, source: "copilot" }), { status: 200 })));
    await waitFor(() => expect((within(sheet).getByLabelText("Script") as HTMLTextAreaElement).value).toBe(SCRIPT));
    expect((within(sheet).getByLabelText("Title") as HTMLInputElement).value).toBe("A Simple Pasta Supper");
    expect(within(sheet).getByText("3 scenes")).toBeTruthy();
    expect(within(sheet).getByRole("button", { name: "✦ Redraft it" })).toBeTruthy();
  });

  it("shows a friendly offline message on a failed draft, and the blank editor still works", async () => {
    const message = "Claude can't draft a script right now. Check your connection or verify your key in Settings, Add-ons. Write it yourself below; nothing is lost.";
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
      String(input) === "/api/script/draft" && init?.method === "POST"
        ? new Response(JSON.stringify({ error: message }), { status: 503 })
        : new Response(JSON.stringify(String(input) === "/api/health"
          ? { capabilities: { assistant: { available: true, verification: "ready", keySource: "saved" } } }
          : EXPLAINERS), { status: 200 })));
    useNewScript.getState().openSheet("explainers", "idea");
    openAt("?view=project&ws=explainers");
    const sheet = screen.getByRole("dialog", { name: "New video from a script" });
    fireEvent.change(await within(sheet).findByLabelText("What's the video about?"), { target: { value: "an idea" } });
    await userEvent.click(within(sheet).getByRole("button", { name: "✦ Draft it" }));
    expect(await within(sheet).findByText(message, { exact: false })).toBeTruthy();
    expect(sheet.querySelector(".ns-draft-error")?.textContent).toBe(`${message} Open Settings`);
    expect(sheet.querySelector(".ns-draft-error")?.textContent?.match(/Settings, Add-ons/g)).toHaveLength(1);
    expect(within(sheet).getByRole("button", { name: "Open Settings" })).toBeTruthy();

    // the blank editor still works: no fake template stands in, the user writes it themselves
    expect((within(sheet).getByLabelText("Script") as HTMLTextAreaElement).value).toBe("");
    fireEvent.change(within(sheet).getByLabelText("Title"), { target: { value: "Manual title" } });
    fireEvent.change(within(sheet).getByLabelText("Script"), { target: { value: SCRIPT } });
    expect((within(sheet).getByRole("button", { name: "Create video" }) as HTMLButtonElement).disabled).toBe(false);
  });

  function mockRedraft() {
    let call = 0;
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      if (key === "GET /api/health") return new Response(JSON.stringify({ capabilities: { assistant: { available: true, verification: "ready", keySource: "saved" } } }), { status: 200 });
      if (key === "GET /api/workspaces/explainers") return new Response(JSON.stringify(EXPLAINERS), { status: 200 });
      if (key === "POST /api/script/draft") {
        call += 1;
        const body = call === 1
          ? { title: "First Draft", script: SCRIPT, source: "copilot" }
          : { title: "Second Draft", script: SCRIPT.replace("A quick supper", "A picnic supper"), source: "copilot" };
        return new Response(JSON.stringify(body), { status: 200 });
      }
      return new Response(JSON.stringify({ error: `no route ${key}` }), { status: 404 });
    });
  }

  it("allows a re-draft: an untouched (AI-authored) title refreshes along with the script", async () => {
    vi.stubGlobal("fetch", mockRedraft());
    useNewScript.getState().openSheet("explainers", "idea");
    openAt("?view=project&ws=explainers");
    const sheet = screen.getByRole("dialog", { name: "New video from a script" });
    fireEvent.change(await within(sheet).findByLabelText("What's the video about?"), { target: { value: "an idea" } });
    await userEvent.click(within(sheet).getByRole("button", { name: "✦ Draft it" }));
    await waitFor(() => expect((within(sheet).getByLabelText("Title") as HTMLInputElement).value).toBe("First Draft"));

    await userEvent.click(within(sheet).getByRole("button", { name: "✦ Redraft it" }));
    await waitFor(() => expect((within(sheet).getByLabelText("Script") as HTMLTextAreaElement).value).toContain("A picnic supper"));
    expect((within(sheet).getByLabelText("Title") as HTMLInputElement).value).toBe("Second Draft");
  });

  it("allows a re-draft: an owner-edited title survives, only the script is replaced", async () => {
    vi.stubGlobal("fetch", mockRedraft());
    useNewScript.getState().openSheet("explainers", "idea");
    openAt("?view=project&ws=explainers");
    const sheet = screen.getByRole("dialog", { name: "New video from a script" });
    fireEvent.change(await within(sheet).findByLabelText("What's the video about?"), { target: { value: "an idea" } });
    await userEvent.click(within(sheet).getByRole("button", { name: "✦ Draft it" }));
    await waitFor(() => expect((within(sheet).getByLabelText("Title") as HTMLInputElement).value).toBe("First Draft"));

    fireEvent.change(within(sheet).getByLabelText("Title"), { target: { value: "My Own Title" } });
    await userEvent.click(within(sheet).getByRole("button", { name: "✦ Redraft it" }));
    await waitFor(() => expect((within(sheet).getByLabelText("Script") as HTMLTextAreaElement).value).toContain("A picnic supper"));
    expect((within(sheet).getByLabelText("Title") as HTMLInputElement).value).toBe("My Own Title");
  });
});
