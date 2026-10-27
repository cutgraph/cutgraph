// Preview-only audio mix math. A player gain callback receives a frame relative to its enclosing
// <FrameWindow>; convert it back to the project timeline before checking the speech sidechain.

import type { Clip, Project, Track } from "../types/project";

export function dbToGain(db: number | null | undefined): number {
  return 10 ** ((typeof db === "number" && Number.isFinite(db) ? db : 0) / 20);
}

export function speechAtFrame(project: Project, frame: number): boolean {
  return project.tracks.some((track) => track.kind === "speech" && (track.clips ?? []).some((clip) =>
    frame >= clip.tlStartF && frame < clip.tlStartF + clip.srcOutF - clip.srcInF,
  ));
}

/**
 * Returns a clip-local gain callback for one non-image media clip. Gain is always
 * applied. A non-speech clip's duckDb applies only while its project-timeline frame overlaps
 * dialogue; no dialogue (or no overlap) leaves the clip at its base gain.
 */
export function clipGain(
  project: Project,
  trackKind: Track["kind"],
  clip: Clip,
): (clipFrame: number) => number {
  const base = dbToGain(clip.gainDb);
  const duck = trackKind === "speech" || clip.duckDb == null ? 1 : dbToGain(clip.duckDb);
  return (clipFrame) => {
    const timelineFrame = clip.tlStartF + clipFrame;
    return base * (duck !== 1 && speechAtFrame(project, timelineFrame) ? duck : 1);
  };
}
