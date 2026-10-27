import { describe, expect, it, vi } from "vitest";
import { characterOf, CHARACTERS, type Character } from "motion-core";
import { registerBuildPacks } from "../src/packRegistry";
import { optionName, partTitle } from "../src/v3/library/cast";
import { headCrop } from "../src/v3/library/paint";

const rig: Character = {
  id: "composer-pack-rig", name: "Synthetic", owner: "client", palettes: ["plain"],
  parts: { "eye-shape": ["calm", "wide"] }, expressions: { neutral: { "eye-shape": "calm" } },
  poses: ["rest"], moves: ["idle"], draw() {},
};
const entry = { id: rig.id, name: rig.name, owner: rig.owner, palettes: rig.palettes,
  parts: rig.parts, poses: rig.poses, moves: rig.moves, expressions: ["neutral"],
  labels: { titles: { "eye-shape": "Eye shape" }, options: { "eye-shape": { calm: "Relaxed" } } },
  head: { rows: 40, foot: 40, span: 20, cols: 22, centre: 12 },
};

describe("build-time character packs", () => {
  it("pairs manifests and modules and exposes labels and head crops to the composer", async () => {
    expect(await registerBuildPacks(
      { "../../packs/synthetic/pack.json": JSON.stringify({ characters: [entry] }) },
      { "../../packs/synthetic/index.ts": async () => ({ characters: [rig] }) },
    )).toEqual([entry]);
    const registered = characterOf(rig.id);
    expect(registered).toBe(rig);
    expect(partTitle(registered, "eye-shape")).toBe("Eye shape");
    expect(optionName(registered, "eye-shape", "calm")).toBe("Relaxed");
    expect(optionName(registered, "eye-shape", "wide")).toBe("Wide");
    expect(headCrop(rig.id)).toEqual(entry.head);
  });

  it("uses name and whole-character fallbacks without optional metadata", async () => {
    const plain = { ...rig, id: "plain-pack-rig" };
    await registerBuildPacks(
      { "../../packs/plain/pack.json": JSON.stringify({ characters: [{ ...plain, expressions: ["neutral"] }] }) },
      { "../../packs/plain/index.ts": async () => ({ characters: [plain] }) },
    );
    expect(partTitle(plain, "eye-shape")).toBe("Eye-shape");
    expect(optionName(plain, "eye-shape", "calm")).toBe("Calm");
    expect(headCrop(plain.id)).toBeUndefined();
  });

  it("uses own properties for rig, part and option names and head crops", async () => {
    const plain = { ...rig, id: "constructor", parts: { constructor: ["constructor"] },
      expressions: { neutral: { constructor: "constructor" } } };
    expect(await registerBuildPacks(
      { "../../packs/own/pack.json": JSON.stringify({ characters: [{ ...plain, expressions: ["neutral"] }] }) },
      { "../../packs/own/index.ts": async () => ({ characters: [plain] }) },
    )).toHaveLength(1);
    expect(partTitle(plain, "constructor")).toBe("Constructor");
    expect(optionName(plain, "constructor", "constructor")).toBe("Constructor");
    expect(headCrop(plain.id)).toBeUndefined();
    expect(headCrop("toString")).toBeUndefined();
    expect(partTitle(characterOf("lens"), "constructor")).toBe("Constructor");
    expect(optionName(characterOf("lens"), "toString", "name")).toBe("Name");
    expect(optionName(characterOf("lens"), "eyes", "constructor")).toBe("Constructor");
  });

  it("rejects empty expressions in both metadata and drawing modules", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const empty = { ...rig, id: "empty-pack-rig", expressions: {} };
      for (const expressions of [[], ["neutral"]]) {
        expect(await registerBuildPacks(
          { "../../packs/empty/pack.json": JSON.stringify({ characters: [{ ...empty, expressions }] }) },
          { "../../packs/empty/index.ts": async () => ({ characters: [empty] }) },
        )).toEqual([]);
      }
      expect(CHARACTERS[empty.id]).toBeUndefined();
      expect(warn).toHaveBeenCalledTimes(2);
    } finally {
      warn.mockRestore();
    }
  });

  it("sorts packs, skips clashes and malformed metadata and ignores incomplete pairs", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const first = { ...rig, id: "sorted-pack-rig", name: "First" };
      const second = { ...first, name: "Second" };
      const valid = { ...rig, id: "surviving-pack-rig" };
      const manifests = {
        "../../packs/b-later/pack.json": JSON.stringify({ characters: [{ ...second, expressions: ["neutral"] }] }),
        "../../packs/a-first/pack.json": JSON.stringify({ characters: [{ ...first, expressions: ["neutral"] }] }),
        "../../packs/c-invalid/pack.json": JSON.stringify(null),
        "../../packs/Bad_id/pack.json": JSON.stringify({ characters: [] }),
        "../../packs/missing/pack.json": JSON.stringify(null),
        "../../packs/builtin/pack.json": JSON.stringify({ characters: [{ ...rig, id: "lens", expressions: ["neutral"] }] }),
        "../../packs/empty/pack.json": JSON.stringify({ characters: [{ ...valid, expressions: [] }] }),
        "../../packs/valid/pack.json": JSON.stringify({ characters: [{ ...valid, expressions: ["neutral"] }] }),
      };
      const modules = {
        "../../packs/b-later/index.ts": vi.fn(async () => ({ characters: [second] })),
        "../../packs/a-first/index.ts": vi.fn(async () => ({ characters: [first] })),
        "../../packs/c-invalid/index.ts": vi.fn(async () => ({ characters: [] })),
        "../../packs/Bad_id/index.ts": vi.fn(async () => ({ characters: [] })),
        "../../packs/module-only/index.ts": vi.fn(async () => ({ characters: [rig] })),
        "../../packs/builtin/index.ts": vi.fn(async () => ({ characters: [] })),
        "../../packs/empty/index.ts": vi.fn(async () => ({ characters: [] })),
        "../../packs/valid/index.ts": vi.fn(async () => ({ characters: [valid] })),
      };
      expect(await registerBuildPacks(manifests, modules)).toHaveLength(1);
      expect(CHARACTERS[first.id]).toBeUndefined();
      expect(CHARACTERS[valid.id]).toBe(valid);
      expect(warn).toHaveBeenCalledTimes(6);
      for (const [path, loader] of Object.entries(modules)) {
        expect(loader).toHaveBeenCalledTimes(path === "../../packs/valid/index.ts" ? 1 : 0);
      }
    } finally {
      warn.mockRestore();
    }
  });

  it("contains a throwing loader and still registers the next selected pack", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const broken = { ...rig, id: "broken-import-rig" };
      const valid = { ...rig, id: "after-import-failure-rig" };
      const throwing = vi.fn(async () => { throw new Error("synthetic import failure"); });
      expect(await registerBuildPacks(
        {
          "../../packs/a-broken/pack.json": JSON.stringify({ characters: [{ ...broken, expressions: ["neutral"] }] }),
          "../../packs/b-valid/pack.json": JSON.stringify({ characters: [{ ...valid, expressions: ["neutral"] }] }),
        },
        {
          "../../packs/a-broken/index.ts": throwing,
          "../../packs/b-valid/index.ts": async () => ({ characters: [valid] }),
        },
      )).toHaveLength(1);
      expect(throwing).toHaveBeenCalledOnce();
      expect(CHARACTERS[broken.id]).toBeUndefined();
      expect(CHARACTERS[valid.id]).toBe(valid);
      expect(warn).toHaveBeenCalledOnce();
      expect(warn).toHaveBeenCalledWith("Skipping character pack a-broken: synthetic import failure");
    } finally {
      warn.mockRestore();
    }
  });

  it("skips malformed raw JSON before calling its loader", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const loader = vi.fn(async () => ({ characters: [rig] }));
      expect(await registerBuildPacks(
        { "../../packs/malformed/pack.json": "{" },
        { "../../packs/malformed/index.ts": loader },
      )).toEqual([]);
      expect(loader).not.toHaveBeenCalled();
      expect(warn).toHaveBeenCalledOnce();
      expect(warn.mock.calls[0][0]).toMatch(/^Skipping character pack malformed:/);
    } finally {
      warn.mockRestore();
    }
  });
});
