import { describe, it, expect, beforeEach } from "vitest";
import type { Project } from "../src/types/project";
import { useProjectStore, applyLocal } from "../src/store/useProjectStore";

// An Export render document carries optional channel, crop and caption fields.
// The first edit must preserve them through the store's full-document round trip.
function shortDoc(): Project {
  return {
    fps: 30,
    width: 1080,
    height: 1920,
    name: "vlog01__insight",
    revision: 0,
    schemaVersion: 1,
    channel: "studio",
    crop: { mode: "center" },
    captions: { enabled: true, emphasis: [2, 5] },
    tracks: [
      {
        id: "speech",
        kind: "speech",
        clips: [{ id: "s0", media: { source: "media/rec.mp4", proxy: null }, srcInF: 300, srcOutF: 1200, tlStartF: 0 }],
      },
    ],
  };
}

beforeEach(() => {
  useProjectStore.setState({ project: null, revision: 0 });
  useProjectStore.temporal.getState().clear();
});

describe("short doc preserves the optional fields through an edit cycle", () => {
  it("channel / crop / captions survive an applyLocal mutation", () => {
    useProjectStore.setState({ project: shortDoc(), revision: 0 });

    applyLocal(
      (s) => ({ project: { ...s.project!, tracks: s.project!.tracks.slice() } }),
      { origin: "human", label: "trim short" },
    );

    const p = useProjectStore.getState().project!;
    expect(p.channel).toBe("studio");
    expect(p.crop).toEqual({ mode: "center" });
    expect(p.captions).toEqual({ enabled: true, emphasis: [2, 5] });
  });
});
