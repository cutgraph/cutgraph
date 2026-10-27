// The Timeline overlays an animated scene's
// character in Picture, a cameo in Graphics, and the caption pages the player shows and the export burns.
import { shotMode } from "motion-core";
import type { TranscriptWord } from "../../../api";
import type { LaneBlock, TimelineLanes } from "./Timeline";
import { captionPages } from "../../../preview/captionPages";
import type { Project } from "../../../types/project";
import { characterFor, rigOf, type Cast } from "../../library/cast";
import { sceneRecipe, type SceneSpan } from "../model";
import { sceneSpec } from "../shot/specs";

export function laneBlocks(
  project: Project,
  words: TranscriptWord[],
  spans: SceneSpan[],
  media: string | null,
  cast: Cast,
): Omit<TimelineLanes, "label"> {
  const fps = project.fps;
  const picture: LaneBlock[] = [];
  const graphics: LaneBlock[] = [];
  for (const span of spans) {
    const mode = shotMode(span.scene.shot);
    const recipe = sceneRecipe(span.scene, cast);
    const spec = recipe && sceneSpec(project, words, span, media, cast);
    if (!recipe || !spec) continue;
    const endF = spec.startF + spec.durF;
    if (mode === "animation") {
      picture.push({ id: `pic-${span.scene.id}`, label: `${recipe.actor} · ${recipe.expression}`, kind: "anim", startF: spec.startF, endF });
    } else {
      const startF = Math.min(endF - 1, spec.startF + Math.round((spec.timing.inT ?? 0) * fps));
      const rig = rigOf(characterFor(cast, span.scene.shot?.character)).name;
      graphics.push({ id: `cameo-${span.scene.id}`, label: `${rig} cameo`, kind: "cameo", startF, endF });
    }
  }
  const frame = (cs: number): number => Math.round((cs * fps) / 100);
  const captions = captionPages(project, words, project.width, project.height).map((page, i) => ({
    id: `cap-${i}`,
    label: page.words.map((w) => w.w).join(" "),
    kind: "caption",
    startF: frame(page.on),
    endF: frame(page.off),
  }));
  return { over: { picture, graphics }, captions, captionsOn: project.captions?.enabled === true };
}
