// Numbered takes in the Shot view: every look a scene's shot
// settles on while the Shot view is open is a take, and clicking one puts the shot back to it as one
// undo step. Takes are session-local, like undo history: a record of what was tried in this sitting,
// never written into the project. A look already among the takes (an undo, a redo, a take clicked
// back) lights that take instead of numbering a new one.
import { create } from "zustand";
import { deepEqual } from "../../../store/rebase";
import type { Shot } from "../../../types/project";
import type { ShotPatch } from "./specs";

export interface Take {
  n: number;
  /** The scene's stored shot for this take; null is a scene with no shot (plain footage). */
  shot: Shot | null;
}

const NONE: Take[] = [];
const useTakes = create<Record<string, Take[]>>(() => ({}));

export const takesKey = (video: string, sceneId: string): string => `${video}/${sceneId}`;

export function useSceneTakes(key: string): Take[] {
  return useTakes((s) => s[key]) ?? NONE;
}

/** Record `shot` as the scene's next take unless one of its takes already has that look. */
export function noteTake(key: string, shot: Shot | undefined): void {
  const takes = useTakes.getState()[key] ?? NONE;
  const look = shot ?? null;
  if (takes.some((t) => deepEqual(t.shot, look))) return;
  useTakes.setState({ [key]: [...takes, { n: takes.length + 1, shot: look }] });
}

export function isTake(take: Take, shot: Shot | undefined): boolean {
  return deepEqual(take.shot, shot ?? null);
}

/** The patch that turns `current` into the take's shot: every current field cleared, then the take's set. */
export function takePatch(current: Shot | undefined, take: Take): ShotPatch {
  const cleared = Object.fromEntries(Object.keys(current ?? {}).map((k) => [k, null]));
  return { ...cleared, ...take.shot } as ShotPatch;
}

export const resetTakes = (): void => useTakes.setState({}, true);
