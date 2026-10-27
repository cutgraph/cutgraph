// Copy the working document into a separate compound-named render document for each format.
// Refresh its content, dimensions and crop on every export so it tracks current edits.
import type { Project } from "../../../types/project";
import { derivedProjectName, formatSize, isWorkingFormat, type ExportFormat } from "./formats";

/** `reframe` (carrying the working doc's own crop.metaPath) when a reframe has already run for this
 * project, else a static `center` crop. A missing/invalid metaPath falls back to a center crop at render time too
 * (pipeline/compile.py), so this never blocks an export. */
export function cropForDerived(working: Project): NonNullable<Project["crop"]> {
  const metaPath = working.crop?.metaPath;
  return metaPath ? { mode: "reframe", metaPath } : { mode: "center" };
}

export interface RenderPlan {
  /** The project name to submit as the render_final job's `project`. */
  project: string;
  /** Non-null when this format needs a derived doc (re)persisted before rendering. */
  derived: Project | null;
}

export function buildDerivedProject(working: Project, format: ExportFormat, captionsOn: boolean): Project {
  const [width, height] = formatSize(format);
  return {
    ...working,
    name: derivedProjectName(working.name, format),
    width,
    height,
    // Another frame shape reframes the footage to it; the same shape at another size (4K) keeps the working framing.
    ...(width * working.height !== height * working.width && { crop: cropForDerived(working) }),
    captions: { ...working.captions, enabled: captionsOn },
  };
}

export function planRenderFor(working: Project, format: ExportFormat, captionsOn: boolean): RenderPlan {
  if (isWorkingFormat(working, format)) return { project: working.name, derived: null };
  return { project: derivedProjectName(working.name, format), derived: buildDerivedProject(working, format, captionsOn) };
}

/** The working doc's own `captions` field, next-value, when the toggle disagrees with it — null when
 * nothing needs to change (avoids a no-op save/undo-step on every export click). */
export function workingCaptionsPatch(working: Project, captionsOn: boolean): NonNullable<Project["captions"]> | null {
  if ((working.captions?.enabled ?? false) === captionsOn) return null;
  return { ...working.captions, enabled: captionsOn };
}
