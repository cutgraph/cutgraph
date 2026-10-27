// @vitest-environment jsdom
import { CHARACTERS as testRegistry } from "motion-core";
import { SYNTHETIC_RIG as SAMPLE_RIG } from "../../motion-core/test/synthetic-rig.ts";
const previousRig = testRegistry[SAMPLE_RIG.id];
Object.assign(testRegistry, { [SAMPLE_RIG.id]: SAMPLE_RIG });
afterAll(() => {
  if (previousRig) Object.assign(testRegistry, { [SAMPLE_RIG.id]: previousRig });
  else Reflect.deleteProperty(testRegistry, SAMPLE_RIG.id);
});
// Library → Cast against a fake studio that keeps its cast: the character switcher, each character's
// art, tiles and home, "New character" (name, every rig, where it lives among every client workspace or a
// new one, approved art), each card's actions (rename, make default, delete after its usage), the
// default mark, and the expression composer: Describe (a fake proposal, never a model), the part rows,
// Save, a refused name, and editing or deleting.
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { CHARACTERS, type PartSpec } from "motion-core";
import Library from "../src/v3/Library";
import type { CastExpression, CharacterProfile } from "../src/v3/api";
import { resetCast, scopeKey, useCastStore } from "../src/v3/library/castStore";

const SMUG: PartSpec = { eyes: "side", mouth: "smile" };
const ART = "0123456789abcdef";
const GARDEN = { id: "garden-1", name: "Garden" };

type Reply = { status?: number; body: unknown };
type Override = (body: unknown, url: URL) => Reply;

interface Studio {
  calls: { key: string; url: URL; body: unknown }[];
  characters: CharacterProfile[];
  defaults: Record<string, string>;
  workspaces: { id: string; name: string }[];
}

const profile = (id: string, name: string, workspaceId: string, expressions: CastExpression[] = []): CharacterProfile => ({
  approved: true,
  id, name, rig: "lens", art: [], palette: "periwinkle", expressions, workspaceId, createdAt: "t",
});

// A studio that keeps characters, expressions and defaults, so the reload after every write sees them.
function fakeStudio(characters: CharacterProfile[] = [profile("default-character", "Lens", "brand")], overrides: Record<string, Override> = {}): Studio {
  const studio: Studio = { calls: [], characters, defaults: {}, workspaces: [{ id: "brand", name: "Brand" }, GARDEN, { id: "northwind-9", name: "Northwind" }, { id: "unsorted", name: "Unsorted" }] };
  const find = (id: string) => studio.characters.find((c) => c.id === id)!;
  const castFor = (ws: string | null) => {
    const visible = studio.characters.filter((c) => c.workspaceId === "brand" || c.workspaceId === ws);
    const def = [ws && studio.defaults[ws], studio.defaults.brand].find((d) => d && visible.some((c) => c.id === d));
    return { characters: visible, default: def ?? "default-character", workspace: ws === GARDEN.id ? GARDEN : null };
  };
  const route = (key: string, url: URL, body: any): Reply => {
    if (overrides[key]) return overrides[key](body, url);
    const [method, path] = key.split(" ");
    const id = decodeURIComponent(path.split("/").pop() ?? "");
    const character = url.searchParams.get("character") ?? "default-character";
    if (key === "GET /api/cast") return { body: castFor(url.searchParams.get("workspace")) };
    if (key === "GET /api/workspaces") return { body: studio.workspaces };
    if (key === "POST /api/workspaces") {
      const made = { id: `${body.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-1a2b3c`, name: body.name };
      studio.workspaces.push(made);
      return { status: 201, body: made };
    }
    if (method === "GET" && path.endsWith("/usage")) return { body: { scenes: 3, videos: 2 } };
    if (method === "DELETE" && path.startsWith("/api/cast/characters/")) {
      studio.characters = studio.characters.filter((c) => c.id !== path.split("/")[4]);
      return { body: { ok: true } };
    }
    if (key === "POST /api/cast/characters") {
      const made = profile(body.name.toLowerCase().replace(/[^a-z0-9]+/g, "-"), body.name, body.workspace);
      made.rig = body.rig ?? made.rig;
      made.palette = body.palette ?? made.palette;
      made.approved = CHARACTERS[made.rig].owner === "brand";
      studio.characters.push(made);
      return { status: 201, body: made };
    }
    if (method === "PATCH" && path.startsWith("/api/cast/characters/")) return { body: Object.assign(find(id), body) };
    if (key === "PUT /api/cast/default") {
      studio.defaults[body.workspace] = body.character;
      return { body: castFor(body.workspace === "brand" ? null : body.workspace) };
    }
    if (key === "POST /api/cast/describe") {
      return { body: body.rig === "sample-rig"
        ? { name: "Curious", spec: { brows: "raised", eyes: "open", mouth: "closed" }, matched: ["curious"], source: "keywords" }
        : { name: "Smug", spec: SMUG, matched: ["eyebrow", "smug"], source: "keywords" } };
    }
    if (key === "POST /api/cast/expressions") {
      const made = { id: body.name.toLowerCase(), name: body.name, spec: body.spec, createdAt: "t", ...(body.draft && { draft: true }) };
      find(body.character).expressions.push(made);
      return { status: 201, body: made };
    }
    if (method === "PATCH" && path.startsWith("/api/cast/expressions/")) {
      const e = find(character).expressions.find((x) => x.id === id)!;
      const { approved, ...patch } = body;
      if (approved) delete e.draft;
      return { body: Object.assign(e, patch) };
    }
    if (method === "DELETE" && path.startsWith("/api/cast/expressions/")) {
      find(character).expressions = find(character).expressions.filter((x) => x.id !== id);
      return { body: { ok: true } };
    }
    if (method === "POST" && /^\/api\/workspaces\/[^/]+\/assets$/.test(path)) {
      return { status: 201, body: { assets: [{ id: ART, kind: "image", name: "sheet.png" }], deduped: [] } };
    }
    return { status: 404, body: { error: `no route ${key}` } };
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://studio.test");
    const key = `${init?.method ?? "GET"} ${url.pathname}`;
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
    studio.calls.push({ key, url, body });
    const reply = route(key, url, body);
    return new Response(JSON.stringify(reply.body), { status: reply.status ?? 200, headers: { "Content-Type": "application/json" } });
  }));
  return studio;
}

const section = (name: string) => screen.getByRole("region", { name });
const tileNames = (name: string) => within(section(name)).queryAllByRole("button")
  .filter((b) => b.classList.contains("big")).map((b) => b.querySelector("span")?.textContent);
const part = (row: string, name: string) => within(screen.getByRole("group", { name: row })).getByRole("button", { name });
const cards = () => within(screen.getByRole("group", { name: "Characters" })).getAllByRole("button")
  .filter((b) => b.classList.contains("char-card")).map((b) => b.textContent);
const call = (studio: Studio, key: string) => studio.calls.find((c) => c.key === key);
const ready = (scope: Parameters<typeof scopeKey>[0] = {}) =>
  waitFor(() => expect(useCastStore.getState().scopes[scopeKey(scope)]?.status).toBe("ready"));

beforeEach(() => {
  resetCast();
  HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("Library → Cast", () => {
  it("shows an incomplete unavailable profile without crashing", async () => {
    const stored = { id: "stored-character", rig: "retired-rig", workspaceId: "brand" } as CharacterProfile;
    const studio = fakeStudio([stored]);
    studio.defaults.brand = stored.id;
    render(<Library />);
    await ready();
    expect(screen.getByRole("img", { name: /^stored-character:/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete character" })).toBeTruthy();
  });
  it("uses the selected default for the badge and protects only the built-in from deletion", async () => {
    const studio = fakeStudio([profile("default-character", "Lens", "brand"), profile("other", "Other", "brand")]);
    studio.defaults.brand = "other";
    render(<Library />);
    await ready();
    expect(screen.getByText("Default character", { selector: ".char-actions .badge" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Make default" })).toBeNull();
    expect(screen.getByRole("button", { name: "Delete character" })).toBeTruthy();
    fireEvent.click(within(screen.getByRole("group", { name: "Characters" })).getByRole("button", { name: /^Lens/ }));
    expect(screen.queryByText("Default character", { selector: ".char-actions .badge" })).toBeNull();
    expect(screen.getByRole("button", { name: "Make default" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Delete character" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Make default" }));
    await waitFor(() => expect(screen.getByText("Default character", { selector: ".char-actions .badge" })).toBeTruthy());
    expect(call(studio, "PUT /api/cast/default")?.body).toEqual({ workspace: "brand", character: "default-character" });
  });
  it("creates the sample rig in the shared cast without replacing the default", async () => {
    const studio = fakeStudio();
    render(<Library />);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: /New character/ }));
    const dialog = await screen.findByRole("dialog", { name: "New character" });
    fireEvent.click(part("Rig", "Lens"));
    expect(part("Where it lives", "Brand").getAttribute("aria-pressed")).toBe("true");
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Name" }), { target: { value: "Sample" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(call(studio, "POST /api/cast/characters")?.body)
      .toEqual({ workspace: "brand", name: "Sample", rig: "lens", palette: "periwinkle" });
    expect(within(screen.getByRole("group", { name: "Characters" })).getByRole("button", { name: /^Sample/ }).getAttribute("aria-pressed")).toBe("true");
    expect(tileNames("Expressions")).toEqual(Object.keys(CHARACTERS.lens.expressions).map((id) => id[0].toUpperCase() + id.slice(1)));
    expect(studio.characters.find((character) => character.id === "sample")?.approved).toBe(true);
    expect(call(studio, "PUT /api/cast/default")).toBeUndefined();
  });

  it("shows the Lens's approved art and a tile for every expression, pose and move", async () => {
    fakeStudio([profile("default-character", "Lens", "brand", [{ id: "smug", name: "Smug", spec: SMUG, createdAt: "t" }])]);
    render(<Library />);
    expect(screen.getByRole("button", { name: "Cast", pressed: true })).toBeTruthy();


    await waitFor(() => expect(tileNames("Expressions")).toContain("Smug"));
    expect(cards()).toEqual(["LensBrand · Default", "＋New character"]);
    expect(tileNames("Expressions")).toEqual(["Neutral", "Happy", "Excited", "Surprised", "Curious", "Focused", "Sleepy", "Idea", "Confused", "Determined", "Proud", "Smug"]);
    expect(within(section("Expressions")).getByText("12")).toBeTruthy();
    expect(tileNames("Poses")).toEqual(["Rest", "Point", "Hold", "Cheer", "Shrug", "Think", "Wave", "Hands on hips", "Flex"]);
    expect(tileNames("Moves")).toEqual(["Idle", "Hop in", "Hop", "Walk", "Nod", "Ponder", "Wave", "Celebrate", "Sway"]);
    expect(screen.getByRole("img", { name: "Lens: Point · Happy · Idle" })).toBeTruthy();

    fireEvent.click(within(section("Expressions")).getByRole("button", { name: /Smug/ }));
    fireEvent.click(within(section("Moves")).getByRole("button", { name: "Hop in" }));
    expect(screen.getByRole("img", { name: "Lens: Point · Smug · Hop in" })).toBeTruthy();
    expect(screen.getByText('<Character pose="point" expression="smug" move="enter" />')).toBeTruthy();
  });

  it("drafts a described expression, keeps it out of scenes, then approves it", async () => {
    const studio = fakeStudio();
    render(<Library />);
    await ready();
    const describeBox = screen.getByRole("textbox", { name: "Describe a new expression" });
    fireEvent.change(describeBox, { target: { value: "smug, one eyebrow up" } });
    fireEvent.submit(describeBox);

    const draft = await screen.findByRole("group", { name: "Smug, a draft" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(call(studio, "POST /api/cast/describe")?.body).toEqual({ text: "smug, one eyebrow up", rig: "lens" });
    expect(call(studio, "POST /api/cast/expressions")?.body).toEqual({ character: "default-character", name: "Smug", spec: SMUG, draft: true });
    expect(within(draft).getByText("Draft")).toBeTruthy();
    expect(within(draft).getByRole("button", { name: "Smug" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("img", { name: "Lens: Point · Smug · Idle" })).toBeTruthy();
    let character = useCastStore.getState().scopes.brand.cast.characters[0];
    expect(character.expressions).toEqual([{ id: "smug", name: "Smug", spec: SMUG, createdAt: "t", draft: true }]);

    fireEvent.click(within(draft).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Smug, a draft" })).toBeNull());
    const patch = call(studio, "PATCH /api/cast/expressions/smug");
    expect([patch?.url.searchParams.get("character"), patch?.body]).toEqual(["default-character", { approved: true }]);
    expect(within(section("Expressions")).getByRole("button", { name: /Smug/ }).textContent).toContain("Custom");
    character = useCastStore.getState().scopes.brand.cast.characters[0];
    expect(character.expressions).toEqual([{ id: "smug", name: "Smug", spec: SMUG, createdAt: "t" }]);
  });

  it("discards a draft", async () => {
    const studio = fakeStudio([profile("default-character", "Lens", "brand", [{ id: "bold", name: "Bold", spec: SMUG, createdAt: "t", draft: true }])]);
    render(<Library />);
    const draft = await screen.findByRole("group", { name: "Bold, a draft" });
    fireEvent.click(within(draft).getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Bold, a draft" })).toBeNull());
    expect(call(studio, "DELETE /api/cast/expressions/bold")).toBeTruthy();
    expect(within(section("Expressions")).queryByText("Bold")).toBeNull();
  });

  it("suggests from a description inside the composer, then saves what the user built", async () => {
    const studio = fakeStudio();
    render(<Library />);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "New expression" }));
    const dialog = await screen.findByRole("dialog", { name: "New expression" });
    const describeBox = within(dialog).getByRole("textbox", { name: "Describe the expression" });
    fireEvent.change(describeBox, { target: { value: "smug, one eyebrow up" } });
    fireEvent.submit(describeBox);
    await within(dialog).findByText("Suggested from the keywords “eyebrow”, “smug”. Adjust any part below.");
    expect((within(dialog).getByRole("textbox", { name: "Name" }) as HTMLInputElement).value).toBe("Smug");
    expect(part("Eyes", "Sideways").getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(part("Mouth", "Flat"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(call(studio, "POST /api/cast/expressions")?.body).toEqual({ character: "default-character", name: "Smug", spec: { ...SMUG, mouth: "flat" } });
    expect(within(section("Expressions")).getByRole("button", { name: /Smug/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("builds one from the parts alone and shows a refused name in place", async () => {
    fakeStudio(undefined, {
      "POST /api/cast/expressions": () => ({ status: 409, body: { error: "“Happy” is a built-in expression" } }),
    });
    render(<Library />);
    fireEvent.click(screen.getByRole("button", { name: "New expression" }));
    const dialog = await screen.findByRole("dialog", { name: "New expression" });
    const save = within(dialog).getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.click(part("Eyes", "Smile"));
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Name" }), { target: { value: "Happy" } });
    fireEvent.click(save);
    expect((await within(dialog).findByRole("alert")).textContent).toBe("“Happy” is a built-in expression");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("edits and deletes a custom expression", async () => {
    const studio = fakeStudio([profile("default-character", "Lens", "brand", [{ id: "smug", name: "Smug", spec: SMUG, createdAt: "t" }])]);
    render(<Library />);
    fireEvent.click(await within(section("Expressions")).findByRole("button", { name: /Smug/ }));
    fireEvent.click(screen.getByRole("button", { name: "Edit “Smug”" }));
    let dialog = await screen.findByRole("dialog", { name: "Edit “Smug”" });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Name" }), { target: { value: "Smirk" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const patch = call(studio, "PATCH /api/cast/expressions/smug");
    expect([patch?.url.searchParams.get("character"), patch?.body]).toEqual(["default-character", { name: "Smirk", spec: SMUG }]);
    expect(tileNames("Expressions")).toContain("Smirk");

    fireEvent.click(screen.getByRole("button", { name: "Edit “Smirk”" }));
    dialog = await screen.findByRole("dialog", { name: "Edit “Smirk”" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete it" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(tileNames("Expressions")).not.toContain("Smirk");
    expect(screen.getByRole("img", { name: "Lens: Point · Happy · Idle" })).toBeTruthy();
  });
});

describe("Library → Cast characters", () => {
  const gardenStudio = () => fakeStudio([
    profile("default-character", "Lens", "brand"),
    profile("buddy", "Buddy", "brand"),
    { ...profile("notes-sample", "Notes Sample", GARDEN.id, [{ id: "grin", name: "Grin", spec: SMUG, createdAt: "t" }]), art: [ART] },
    profile("rival", "Rival", "northwind-9"),
  ]);

  it("switches between the workspace's characters, each with its own home, art and expressions", async () => {
    gardenStudio();
    render(<Library ws={GARDEN.id} />);
    await ready({ workspace: GARDEN.id });
    expect(cards()).toEqual(["LensBrand · Default", "BuddyBrand", "Notes SampleGarden", "＋New character"]);
    expect(screen.queryByText("Rival")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^Notes Sample/ }));
    expect(screen.getByRole("button", { name: /^Notes Sample/ }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("img", { name: "Notes Sample, approved art 1" }).getAttribute("src"))
      .toBe(`/api/workspaces/${GARDEN.id}/assets/${ART}/file`);
    expect(screen.queryByRole("img", { name: "Approved Character, guide pose" })).toBeNull();
    expect(tileNames("Expressions").slice(-1)).toEqual(["Grin"]);
    expect(screen.getByText(/Drawn with the Lens rig as a placeholder/)).toBeTruthy();
    expect(screen.getByText('<Character character="notes-sample" pose="point" expression="happy" move="idle" />')).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /^Buddy/ }));
    expect(screen.getByText("No approved art yet. Upload the model sheet this character is drawn from.")).toBeTruthy();
    expect(tileNames("Expressions")).not.toContain("Grin");
  });

  it("lists brand characters only outside a workspace, and New character can still place one in any client workspace", async () => {
    gardenStudio();
    render(<Library />);
    await ready();
    expect(cards()).toEqual(["LensBrand · Default", "BuddyBrand", "＋New character"]);
    fireEvent.click(screen.getByRole("button", { name: /New character/ }));
    const dialog = await screen.findByRole("dialog", { name: "New character" });
    const homes = () => within(within(dialog).getByRole("group", { name: "Where it lives" })).getAllByRole("button").map((b) => b.textContent);
    await waitFor(() => expect(homes()).toEqual(["Brand", "Garden", "Northwind", "+ New client workspace…"]));
    expect(part("Where it lives", "Brand").getAttribute("aria-pressed")).toBe("true");
  });

  it("shows every rig from the rail: a client's rig is unavailable under Brand, and picking it moves the character to a client workspace", async () => {
    const studio = gardenStudio();
    render(<Library />);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: /New character/ }));
    const dialog = await screen.findByRole("dialog", { name: "New character" });
    await waitFor(() => expect(part("Where it lives", "Garden")).toBeTruthy());
    expect(within(within(dialog).getByRole("group", { name: "Rig" })).getAllByRole("button").map((b) => b.textContent)).toEqual(Object.values(CHARACTERS).map((rig) => rig.name));
    expect(part("Rig", "Sample").getAttribute("aria-disabled")).toBe("true");
    expect(within(dialog).getByText(/The Sample is a client character; it lives in a client workspace\./)).toBeTruthy();

    fireEvent.click(part("Rig", "Sample"));
    expect(part("Where it lives", "Garden").getAttribute("aria-pressed")).toBe("true");
    expect(part("Rig", "Sample").getAttribute("aria-pressed")).toBe("true");
    expect(within(dialog).getByRole("group", { name: "Palette" })).toBeTruthy();
    fireEvent.click(part("Palette", "Fern"));
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Name" }), { target: { value: "Notes Sample Fern" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(call(studio, "POST /api/cast/characters")?.body).toEqual({ workspace: GARDEN.id, name: "Notes Sample Fern", rig: "sample-rig", palette: "fern" });
    expect(new URLSearchParams(window.location.search).get("ws")).toBe(GARDEN.id);
  });

  it("names a new client workspace inline when there is none yet", async () => {
    const studio = fakeStudio();
    studio.workspaces = [{ id: "brand", name: "Brand" }];
    render(<Library />);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: /New character/ }));
    const dialog = await screen.findByRole("dialog", { name: "New character" });
    fireEvent.click(part("Rig", "Sample"));
    const input = within(dialog).getByRole("textbox", { name: "New client workspace" });
    fireEvent.change(input, { target: { value: "Museum Visit" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add workspace" }));
    await waitFor(() => expect(part("Where it lives", "Museum Visit").getAttribute("aria-pressed")).toBe("true"));
    expect(call(studio, "POST /api/workspaces")?.body).toEqual({ name: "Museum Visit" });
    expect(part("Rig", "Sample").getAttribute("aria-pressed")).toBe("true");
    expect(within(dialog).getByText("Only videos in Museum Visit can cast it. It never appears in another workspace.")).toBeTruthy();
  });

  it("creates a character in the current workspace, uploading its approved art there", async () => {
    const studio = gardenStudio();
    render(<Library ws={GARDEN.id} />);
    await ready({ workspace: GARDEN.id });
    fireEvent.click(screen.getByRole("button", { name: /New character/ }));
    const dialog = await screen.findByRole("dialog", { name: "New character" });
    expect(within(within(dialog).getByRole("group", { name: "Rig" })).getAllByRole("button").map((b) => b.textContent)).toEqual(Object.values(CHARACTERS).map((rig) => rig.name));
    expect(within(dialog).getByText("The rig draws the character: its expressions, poses and moves.")).toBeTruthy();
    expect(within(dialog).queryByRole("group", { name: "Palette" })).toBeNull();
    expect(part("Where it lives", "Garden").getAttribute("aria-pressed")).toBe("true");
    expect(within(dialog).getByText("Only videos in Garden can cast it. It never appears in another workspace.")).toBeTruthy();
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Name" }), { target: { value: "Garden Calf" } });
    const sheet = new File(["png"], "calf-sheet.png", { type: "image/png" });
    fireEvent.change(within(dialog).getByLabelText("Approved art"), { target: { files: [sheet] } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(call(studio, "POST /api/cast/characters")?.body).toEqual({ workspace: GARDEN.id, name: "Garden Calf", rig: "lens", palette: "periwinkle" });
    expect(call(studio, `POST /api/workspaces/${GARDEN.id}/assets`)?.body).toBeInstanceOf(FormData);
    expect(call(studio, "PATCH /api/cast/characters/garden-calf")?.body).toEqual({ art: [ART] });
    expect(screen.getByRole("button", { name: /^Garden Calf/ }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("img", { name: "Garden Calf, approved art 1" })).toBeTruthy();
  });

  it("creates a brand character when Brand is picked", async () => {
    const studio = gardenStudio();
    render(<Library ws={GARDEN.id} />);
    await ready({ workspace: GARDEN.id });
    fireEvent.click(screen.getByRole("button", { name: /New character/ }));
    const dialog = await screen.findByRole("dialog", { name: "New character" });
    fireEvent.click(part("Rig", "Sample"));
    fireEvent.click(part("Where it lives", "Brand"));
    expect(within(dialog).getByText("A brand character appears in every workspace's videos.")).toBeTruthy();
    expect(part("Rig", "Lens").getAttribute("aria-pressed")).toBe("true");
    expect(part("Rig", "Sample").getAttribute("aria-disabled")).toBe("true");
    expect(within(dialog).getByText(/Brand characters use the Lens rig/)).toBeTruthy();
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Name" }), { target: { value: "Scout" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(call(studio, "POST /api/cast/characters")?.body).toEqual({ workspace: "brand", name: "Scout", rig: "lens", palette: "periwinkle" });
    expect(cards()).toContain("ScoutBrand");
  });

  it("marks the workspace's default character", async () => {
    const studio = gardenStudio();
    render(<Library ws={GARDEN.id} />);
    await ready({ workspace: GARDEN.id });
    fireEvent.click(screen.getByRole("button", { name: /^Notes Sample/ }));
    fireEvent.click(screen.getByRole("button", { name: "Make default in Garden" }));
    await waitFor(() => expect(cards()).toContain("Notes SampleGarden · Default"));
    expect(call(studio, "PUT /api/cast/default")?.body).toEqual({ workspace: GARDEN.id, character: "notes-sample" });
    expect(cards()).toContain("Notes SampleGarden · Default");
  });

  it("shows the Sample's own sets and parts, restyles it and approves its draft look", async () => {
    const studio = fakeStudio([
      profile("default-character", "Lens", "brand"),
      { ...profile("notes-sample", "Notes Sample", GARDEN.id), rig: "sample-rig", palette: "fern", approved: false },
    ]);
    render(<Library ws={GARDEN.id} />);
    await ready({ workspace: GARDEN.id });
    expect(cards()).toContain("Notes SampleGarden · Draft");
    fireEvent.click(screen.getByRole("button", { name: /^Notes Sample/ }));
    expect(tileNames("Expressions")).toEqual(["Neutral", "Happy", "Curious"]);
    expect(tileNames("Poses")).toEqual(["Rest", "Wave", "Point"]);
    expect(tileNames("Moves")).toEqual(["Idle", "Nod", "Wave"]);
    expect(screen.getByText(/Drawn with the Sample rig/)).toBeTruthy();
    expect(part("Rig", "Sample").getAttribute("aria-pressed")).toBe("true");
    expect(part("Palette", "Fern").getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: "New expression" }));
    const dialog = await screen.findByRole("dialog", { name: "New expression" });
    expect(["Brows", "Eyes", "Mouth"].map((row) => within(within(dialog).getByRole("group", { name: row })).getAllByRole("button").length))
      .toEqual([2, 2, 2]);

    const describeBox = within(dialog).getByRole("textbox", { name: "Describe the expression" });
    fireEvent.change(describeBox, { target: { value: "raised brows and an open mouth" } });
    fireEvent.submit(describeBox);
    await within(dialog).findByText("Suggested from the keywords “curious”. Adjust any part below.");
    expect(call(studio, "POST /api/cast/describe")?.body).toEqual({ text: "raised brows and an open mouth", rig: "sample-rig" });
    expect(part("Brows", "Raised").getAttribute("aria-pressed")).toBe("true");
    expect(part("Eyes", "Open").getAttribute("aria-pressed")).toBe("true");
    expect(part("Mouth", "Closed").getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(part("Mouth", "Open"));
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Name" }), { target: { value: "Smile" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(call(studio, "POST /api/cast/expressions")?.body).toEqual({
      character: "notes-sample", name: "Smile", spec: { brows: "raised", eyes: "open", mouth: "open" },
    });

    fireEvent.click(part("Palette", "Fog"));
    await waitFor(() => expect(call(studio, "PATCH /api/cast/characters/notes-sample")?.body).toEqual({ palette: "fog" }));
    await waitFor(() => expect(part("Palette", "Fog").getAttribute("aria-pressed")).toBe("true"));
    fireEvent.click(screen.getByRole("button", { name: "Approve this look" }));
    await waitFor(() => expect(cards()).toContain("Notes SampleGarden"));
    expect(studio.calls.filter((c) => c.key === "PATCH /api/cast/characters/notes-sample").map((c) => c.body))
      .toEqual([{ palette: "fog" }, { approved: true }]);
    expect(screen.queryByRole("button", { name: "Approve this look" })).toBeNull();
  });

  it("acts on a character from its card: rename, make default, and delete after saying which scenes use it", async () => {
    const studio = gardenStudio();
    render(<Library ws={GARDEN.id} />);
    await ready({ workspace: GARDEN.id });
    const menuOf = async (name: string) => {
      fireEvent.click(screen.getByRole("button", { name: `Actions for ${name}` }));
      return screen.findByRole("menu", { name: `${name} actions` });
    };
    let menu = await menuOf("Lens");
    const del = within(menu).getByRole("menuitem", { name: /Delete/ }) as HTMLButtonElement;
    expect([del.disabled, del.textContent]).toEqual([true, "DeleteThe Lens is built in and can't be deleted."]);
    expect((within(menu).getByRole("menuitem", { name: /Default character/ }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();

    menu = await menuOf("Notes Sample");
    fireEvent.click(within(menu).getByRole("menuitem", { name: /Rename/ }));
    const input = await screen.findByRole("textbox", { name: "Character name" });
    fireEvent.change(input, { target: { value: "Notes Sample Fog" } });
    fireEvent.submit(input);
    await waitFor(() => expect(cards()).toContain("Notes Sample FogGarden"));
    expect(call(studio, "PATCH /api/cast/characters/notes-sample")?.body).toEqual({ name: "Notes Sample Fog" });

    menu = await menuOf("Notes Sample Fog");
    fireEvent.click(within(menu).getByRole("menuitem", { name: /Make default/ }));
    await waitFor(() => expect(cards()).toContain("Notes Sample FogGarden · Default"));
    expect(call(studio, "PUT /api/cast/default")?.body).toEqual({ workspace: GARDEN.id, character: "notes-sample" });

    menu = await menuOf("Notes Sample Fog");
    fireEvent.click(within(menu).getByRole("menuitem", { name: /Delete/ }));
    const confirm = await screen.findByRole("dialog", { name: "Notes Sample Fog actions" });
    expect(await within(confirm).findByText("3 scenes use Notes Sample Fog; they'll fall back to the default character and their role look.")).toBeTruthy();
    expect(call(studio, "GET /api/cast/characters/notes-sample/usage")).toBeTruthy();
    fireEvent.click(within(confirm).getByRole("button", { name: "Delete Notes Sample Fog" }));
    await waitFor(() => expect(cards()).toEqual(["LensBrand · Default", "BuddyBrand", "＋New character"]));
    expect(call(studio, "DELETE /api/cast/characters/notes-sample")).toBeTruthy();
  });

  it("shows an unavailable profile and renames it without rewriting its rig or extensions", async () => {
    const stored = { ...profile("stored-character", "Stored character", GARDEN.id), rig: "unregistered-rig",
      expressions: [{ id: "stored-look", name: "Stored look", spec: { storedPart: "stored-option" }, createdAt: "t" }], extension: { nested: [1, null] } };
    const studio = fakeStudio([profile("default-character", "Lens", "brand"), stored]);
    render(<Library ws={GARDEN.id} />);
    await ready({ workspace: GARDEN.id });
    expect(screen.getByRole("button", { name: /^Stored character Garden · Unavailable/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Actions for Stored character" }));
    fireEvent.click(within(screen.getByRole("menu", { name: "Stored character actions" })).getByRole("menuitem", { name: /Rename/ }));
    const input = await screen.findByRole("textbox", { name: "Character name" });
    fireEvent.change(input, { target: { value: "Renamed character" } });
    fireEvent.submit(input);
    await waitFor(() => expect(studio.characters[1]).toEqual({ ...stored, name: "Renamed character" }));
    expect(call(studio, "PATCH /api/cast/characters/stored-character")?.body).toEqual({ name: "Renamed character" });
  });

  it("moves a placeholder onto the Sample rig, never a brand character", async () => {
    const studio = fakeStudio([
      profile("default-character", "Lens", "brand"),
      profile("buddy", "Buddy", "brand"),
      profile("notes-sample", "Notes Sample", GARDEN.id),
    ]);
    render(<Library ws={GARDEN.id} />);
    await ready({ workspace: GARDEN.id });
    fireEvent.click(screen.getByRole("button", { name: /^Buddy/ }));
    expect(screen.queryByRole("group", { name: "Rig" })).toBeNull();
    expect(screen.getByText(/The Sample is a client character; it lives in a client workspace\. To draw Buddy with one, make it there with New character\./)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^Notes Sample/ }));
    fireEvent.click(part("Rig", "Sample"));
    await waitFor(() => expect(call(studio, "PATCH /api/cast/characters/notes-sample")?.body).toEqual({ rig: "sample-rig" }));
    await waitFor(() => expect(tileNames("Poses")[0]).toBe("Rest"));
  });
});
