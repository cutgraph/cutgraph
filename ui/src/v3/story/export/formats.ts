// Export format catalogue. Dimensions come from motion-core's FORMATS — the single
// source of truth shared with the storyboard/shot renderer — never re-declared here. 4K is the
// 16:9 frame at twice its size: the same design, drawn with twice the pixels (motion-core designScale).
import { FORMATS } from "motion-core";
import type { Format } from "motion-core";
import type { Project } from "../../../types/project";

export type { Format };
export type ExportFormat = Format | "4K";

export const FORMAT_ORDER: readonly ExportFormat[] = ["16:9", "4K", "9:16", "1:1"];

export const FORMAT_LABEL: Readonly<Record<ExportFormat, { name: string; detail: string }>> = {
  "16:9": { name: "YouTube", detail: "Landscape" },
  "4K": { name: "4K", detail: "For YouTube" },
  "9:16": { name: "Shorts & Reels", detail: "Vertical" },
  "1:1": { name: "Square", detail: "Feed" },
};

/** Each export format's frame: an aspect's 1080-class size, at a scale. */
const FRAME: Readonly<Record<ExportFormat, readonly [Format, number]>> = {
  "16:9": ["16:9", 1],
  "4K": ["16:9", 2],
  "9:16": ["9:16", 1],
  "1:1": ["1:1", 1],
};

export const formatAspect = (format: ExportFormat): Format => FRAME[format][0];

export function formatSize(format: ExportFormat): readonly [number, number] {
  const [aspect, scale] = FRAME[format];
  const [width, height] = FORMATS[aspect];
  return [width * scale, height * scale];
}

/** `9:16` → `9x16`, for a derived document name; a
 * scaled format is named by its size (`3840x2160`). */
export function formatSlug(format: ExportFormat): string {
  return FRAME[format][1] === 1 ? format.replace(":", "x") : formatSize(format).join("x");
}

export function derivedProjectName(video: string, format: ExportFormat): string {
  return `${video}__${formatSlug(format)}`;
}

export function isWorkingFormat(project: Pick<Project, "width" | "height">, format: ExportFormat): boolean {
  const [width, height] = formatSize(format);
  return project.width === width && project.height === height;
}

/** The format whose dimensions the working doc already has, if any — the Export sheet's preselection. */
export function matchingFormat(project: Pick<Project, "width" | "height">): ExportFormat | null {
  return FORMAT_ORDER.find((format) => isWorkingFormat(project, format)) ?? null;
}

/** The project name a format will render from: the working doc itself, or its derived render doc. */
export function renderProjectName(video: string, project: Pick<Project, "width" | "height">, format: ExportFormat): string {
  return isWorkingFormat(project, format) ? video : derivedProjectName(video, format);
}
