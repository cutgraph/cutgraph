import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { resolveShot, shotTiming, type Ctx, type MotionEnv, type ShotMode } from "motion-core";
import { nodeEnv, surface } from "../../motion-core/scripts/node-env.ts";
import { paintScene } from "../src/preview/SceneLayer";
import { paintCharacterTile, paintLookTile } from "../src/v3/story/shot/paint";

const W = 480;
const H = 270;
const shot = resolveShot({ pose: "cheer", expression: "excited", backdrop: "route", headline: "Serve it" }, "Build", "A pasta supper");
const timing = shotTiming(0, 12, 45, 30);

function frame(paint: (ctx: Ctx) => void, w = W, h = H): { hash: string; alpha: Uint8ClampedArray } {
  const { canvas, ctx } = surface(w, h);
  paint(ctx);
  const { data } = ctx.getImageData(0, 0, w, h);
  return { hash: createHash("sha256").update(canvas.toBuffer("image/png")).digest("hex"), alpha: data.filter((_, i) => i % 4 === 3) };
}

const scene = (mode: ShotMode, lt: number, env: MotionEnv, dirty = false) => frame((ctx) => {
  if (dirty) {
    ctx.fillStyle = "#f0f";
    ctx.fillRect(0, 0, W, H);
  }
  paintScene(ctx, { mode, shot, timing }, lt, 1920, 1080, env);
});

describe("preview scene frame", () => {
  const env = nodeEnv();

  it("is a pure function of the shot and t", () => {
    const first = scene("animation", 1.5, env);
    scene("cameo", 0.2, env);
    expect(scene("animation", 1.5, env).hash).toBe(first.hash);
    expect(scene("animation", 1.5, nodeEnv()).hash).toBe(first.hash);
    expect(scene("animation", 2.5, env).hash).not.toBe(first.hash);
  });

  it("clears the previous frame before drawing, and scales the composition into the canvas", () => {
    const clean = scene("cameo", 2.2, env); // sprung in on its cue word, at 1.5 s
    expect(scene("cameo", 1.2, env, true).alpha.every((a) => a === 0)).toBe(true);
    expect(scene("cameo", 2.2, env, true).hash).toBe(clean.hash);
    expect(clean.alpha.slice(0, W * 20).every((a) => a === 0)).toBe(true);
    expect(clean.alpha.some((a) => a > 0)).toBe(true);
  });

  it("covers the whole frame for an animation and nothing for footage", () => {
    expect(scene("animation", 1.2, env).alpha.every((a) => a === 255)).toBe(true);
    expect(scene("footage", 1.2, env).alpha.every((a) => a === 0)).toBe(true);
  });

  it("paints picker tiles deterministically", () => {
    const character = () => frame((ctx) => paintCharacterTile(ctx, 192, 208, { pose: "wave", expression: "happy", move: "wave", t: 0.6 }, env), 192, 208).hash;
    const look = () => frame((ctx) => paintLookTile(ctx, 192, 108, shot, 2.2, env), 192, 108).hash;
    expect(character()).toBe(character());
    expect(look()).toBe(look());
  });
});
