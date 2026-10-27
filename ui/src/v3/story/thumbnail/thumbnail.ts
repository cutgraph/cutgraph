// A video's thumbnail: the default rule, the poster the preview draws, and the edits the Thumbnail
// editor makes. `thumbnailOf` mirrors pipeline/thumbnail.py `thumbnail_of` and castPoster (cast.ts) its
// `render_request`, so the preview draws exactly the PNG the studio renders;
// tests/fixtures/thumbnail_parity.json holds the two together.
import { ENSEMBLE_MAX, resolvePoster, type Poster, type PosterFormat } from "motion-core";
import { persistCurrent } from "../../../persist";
import { useSaveStatus } from "../../../saveStatus";
import { applyLocal, useProjectStore } from "../../../store/useProjectStore";
import type { Project, Thumbnail, ThumbnailMember } from "../../../types/project";
import { v3api } from "../../api";
import { castPoster, characterFor, type Cast } from "../../library/cast";

const ANIMATED = new Set(["animation", "cameo"]);

/** The profile the video's first animated scene casts; none when it names none (the cast's default then). */
export function firstLead(project: Project): string | undefined {
  const shot = (project.scenes ?? []).find((s) => ANIMATED.has(s.shot?.mode ?? ""))?.shot;
  return shot?.character || undefined;
}

// The document keeps at most ENSEMBLE_MAX members; the generated type spells that as a tuple.
export const membersOf = (members: ThumbnailMember[]): Thumbnail["ensemble"] => members.slice(0, ENSEMBLE_MAX) as Thumbnail["ensemble"];

/**
 * The video's thumbnail with its defaults filled in: the headline is its title (else its slug), the
 * eyebrow is hidden, the lead is the first animated scene's, and without a stored ensemble the cast's
 * other characters stand behind it, in cast order, up to ENSEMBLE_MAX.
 */
export function thumbnailOf(project: Project, cast: Cast): Thumbnail {
  const stored = project.thumbnail ?? {};
  const out: Thumbnail = { headline: project.title || project.name, eyebrow: "", ...stored };
  const lead = stored.character || firstLead(project);
  if (lead) out.character = lead;
  if (stored.ensemble === undefined) {
    const leadId = characterFor(cast, lead).id;
    out.ensemble = membersOf(cast.characters.filter((c) => c.id !== leadId).map((c) => ({ character: c.id })));
  }
  return out;
}

/** The poster the preview draws for a video, its profiles cast. */
export const posterOf = (project: Project, cast: Cast): Poster => resolvePoster(castPoster(thumbnailOf(project, cast), cast));

/** A change to the thumbnail; `null` or an empty string removes the field. */
export type ThumbnailPatch = { [K in keyof Thumbnail]?: Thumbnail[K] | null };

/** The project with its thumbnail patched (it is then set, even when every field takes its default), or the same project when nothing changes. */
export function withThumbnail(project: Project, patch: ThumbnailPatch): Project {
  const before: Record<string, unknown> = { ...project.thumbnail };
  const next = { ...before };
  for (const [key, value] of Object.entries(patch)) {
    if (value === null || value === undefined || value === "") delete next[key];
    else next[key] = value;
  }
  const keys = Object.keys(next);
  const same = project.thumbnail && keys.length === Object.keys(before).length
    && keys.every((k) => JSON.stringify(next[k]) === JSON.stringify(before[k]));
  return same ? project : { ...project, thumbnail: next as Thumbnail };
}

function commit(next: Project, label: string): void {
  const project = useProjectStore.getState().project;
  if (!project || next === project) return;
  applyLocal(() => ({ project: next }), { origin: "human", label });
  void persistCurrent(project.name);
}

/** Patch the thumbnail as one undo step and save it. */
export function commitThumbnail(patch: ThumbnailPatch, label: string): void {
  const project = useProjectStore.getState().project;
  if (project) commit(withThumbnail(project, patch), label);
}

/** Remove the thumbnail as one undo step: the video goes back to its defaults and its footage cover. */
export function resetThumbnail(): void {
  const project = useProjectStore.getState().project;
  if (!project?.thumbnail) return;
  const { thumbnail: _gone, ...rest } = project;
  commit(rest as Project, "Reset thumbnail");
}

/** Resolves once the open video's edits are saved, so a render reads them; gives up after `ms`. */
function saved(video: string, ms = 10000): Promise<void> {
  const state = useSaveStatus.getState().state;
  if (state === "saved") return Promise.resolve();
  if (state === "unsaved") void persistCurrent(video);
  return new Promise((resolve) => {
    const done = (): void => {
      clearTimeout(timer);
      unsubscribe();
      resolve();
    };
    const timer = setTimeout(done, ms);
    const unsubscribe = useSaveStatus.subscribe((s) => {
      if (s.state !== "saving" && s.state !== "unsaved") done();
    });
  });
}

export const formatSlug = (format: PosterFormat): string => format.replace(":", "x");

/** Render the saved thumbnail at `format` in the studio and save the PNG through the browser. */
export async function downloadThumbnail(video: string, format: PosterFormat): Promise<void> {
  await saved(video);
  const { url } = await v3api.renderThumbnail(video, format);
  const link = Object.assign(document.createElement("a"), { href: url, download: `${video}-thumbnail-${formatSlug(format)}.png` });
  document.body.appendChild(link);
  link.click();
  link.remove();
}
