// The browser preview and the Node export renderer must resolve the same font family for
// headline/eyebrow text, or line breaks and sizes drift between the player and the exported video.
import { describe, expect, it } from "vitest";
import { fontOf } from "../../motion-core/src/draw.ts";
import { nodeEnv } from "../../motion-core/scripts/node-env.ts";
import { domEnv } from "../src/preview/SceneLayer";

describe("preview/export font parity", () => {
  it("resolves the same font family string the player draws with and node-env registers for export", () => {
    expect(fontOf(domEnv())).toBe(fontOf(nodeEnv()));
    expect(fontOf(domEnv())).toBe('"Inter"');
  });
});
