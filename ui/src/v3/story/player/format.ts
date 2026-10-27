// Pure format/crop mapping for the Story Player's format switch. Kept side-effect-free so
// the Export sheet can import it too. `crop` mirrors pipeline/compile.py's contract exactly
// an absent crop is the legacy 16:9 letterbox (compile.py skips the crop filter entirely when
// `proj.crop is None`), and a shorts format reframes only when reframe metadata is already known
// (crop.metaPath); otherwise it falls back to the always-available static center crop.
import { FORMATS, type Format } from "motion-core";
import type { Project } from "../../../types/project";

export type { Format };

const ENTRIES = Object.entries(FORMATS) as [Format, readonly [number, number]][];

/** Which named aspect a project's canvas currently is; '16:9' when the dims match no known format. */
export function formatOf(project: Pick<Project, "width" | "height">): Format {
  const found = ENTRIES.find(([, [w, h]]) => w === project.width && h === project.height);
  return found ? found[0] : "16:9";
}

/** Switch a project's canvas to `format`, keeping crop.metaPath (an explicit reframe sidecar) alive
 * across format switches so re-entering a shorts format doesn't lose already-computed reframe data. */
export function applyFormat(project: Project, format: Format): Project {
  const [width, height] = FORMATS[format];
  if (format === "16:9") return { ...project, width, height, crop: null };
  const metaPath = project.crop?.metaPath;
  return { ...project, width, height, crop: metaPath ? { mode: "reframe", metaPath } : { mode: "center" } };
}
