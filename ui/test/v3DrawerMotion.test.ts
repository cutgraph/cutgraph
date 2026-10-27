import { describe, expect, it } from "vitest";
import { graphicMotion, graphicMotionAt } from "../src/graphics/registry";
import { GRAPHIC_REGISTRY_MOTION } from "../src/graphics/registry.data";

// The same numbers pipeline/compile.py writes into the filtergraph: time-based fades give frame k
// alpha k/inF on the way in and (durF-k)/outF on the way out, and the overlay y is
// slidePx·((1-in)² + out²). See tests/test_workspace_media.py for the ffmpeg side.
describe("registry graphic motion", () => {
  it("enters and exits over ~⅓ s and slides a fixed share of the frame height", () => {
    const motion = graphicMotion(180, 1080);
    expect(motion).toEqual({ inF: GRAPHIC_REGISTRY_MOTION.inF, outF: GRAPHIC_REGISTRY_MOTION.outF, slidePx: 27 });
    expect(motion.inF / 30).toBeGreaterThanOrEqual(0.3);
    expect(motion.inF / 30).toBeLessThanOrEqual(0.4);
    expect(graphicMotion(180, 1920).slidePx).toBe(48);
  });

  it("fades in while rising, holds, then fades out while sinking", () => {
    const { inF, outF, slidePx } = graphicMotion(180, 1080);
    expect(graphicMotionAt(0, 180, 1080)).toEqual({ opacity: 0, dy: slidePx });
    const entering = graphicMotionAt(3, 180, 1080);
    expect(entering.opacity).toBeCloseTo(3 / inF);
    expect(entering.dy).toBeCloseTo(slidePx * (1 - 3 / inF) ** 2);
    expect(graphicMotionAt(inF, 180, 1080)).toEqual({ opacity: 1, dy: 0 });
    expect(graphicMotionAt(180 - outF, 180, 1080)).toEqual({ opacity: 1, dy: 0 });
    const last = graphicMotionAt(179, 180, 1080);
    expect(last.opacity).toBeCloseTo(1 / outF);
    expect(last.dy).toBeCloseTo(slidePx * ((outF - 1) / outF) ** 2);
  });

  it("splits a span too short for both moves", () => {
    expect(graphicMotion(8, 1080)).toMatchObject({ inF: 4, outF: 4 });
    expect(graphicMotion(1, 1080)).toMatchObject({ inF: 0, outF: 0 });
    expect(graphicMotionAt(0, 1, 1080)).toEqual({ opacity: 1, dy: 0 });
  });
});
