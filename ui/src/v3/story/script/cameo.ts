// ✦ Character on a word selection and the drawer's Character tab create a word-anchored cameo.
// The scene holding the word becomes a cameo whose
// character springs in on it: the shot's "Lands on" word, which motion-core's cameo timing follows.
import { performer, type Performer } from "motion-core";
import type { TranscriptWord } from "../../../api";
import { useProjectStore } from "../../../store/useProjectStore";
import type { Project, Scene } from "../../../types/project";
import { castShot, characterFor, rigOf, type Cast } from "../../library/cast";
import { commitEdit } from "../history";
import { CHARACTER_REST_T } from "../shot/paint";
import { withShot } from "../shot/specs";

/** What ✦ Character offers for a selection: the scene's character, the word it lands on, and adding it. */
export interface CameoPick {
  /** The character's rig, e.g. "Lens". */
  name: string;
  word: string;
  /** The character as the cameo draws it, at rest. */
  performer: Performer;
  onPick: () => void;
}

/** The project with `sceneId` a cameo that springs in on word `at` (the rest of its look kept). */
export function withCameo(project: Project, sceneId: string, at: number): Project {
  return withShot(project, sceneId, { mode: "cameo", cueWord: at });
}

/** The scene's character as its cameo would draw it, cast from the video's cast. */
export function cameoCharacter(scene: Scene, cast: Cast): Pick<CameoPick, "name" | "performer"> {
  return {
    name: rigOf(characterFor(cast, scene.shot?.character)).name,
    performer: performer(castShot(scene.shot, scene.role, scene.title, cast), CHARACTER_REST_T),
  };
}

/**
 * The cameo on word `at` of `scene`, added as one undo step and then `onAdded`; null on a script video,
 * whose scenes are already animated, or where there is no kept word to land on.
 */
export function cameoPick(
  project: Project | null,
  scene: Scene | undefined,
  at: number | undefined,
  words: TranscriptWord[],
  cut: ReadonlySet<number>,
  cast: Cast,
  onAdded: (at: number) => void,
): CameoPick | null {
  const word = at === undefined ? undefined : words[at]?.w;
  if (!project || project.narration != null || !scene || at === undefined || word === undefined || cut.has(at)) return null;
  const character = cameoCharacter(scene, cast);
  return {
    ...character,
    word,
    onPick: () => {
      const p = useProjectStore.getState().project;
      if (!p) return;
      const next = withCameo(p, scene.id, at);
      if (next !== p) commitEdit(next, `Added ${character.name} cameo on “${word}”`);
      onAdded(at);
    },
  };
}
