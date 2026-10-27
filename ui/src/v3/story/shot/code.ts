// Illustrative pseudocode describing a shot; drawSceneFrame renders preview and export.
import { bare, type Shot as MotionShot, type ShotMode } from "motion-core";
import type { Shot } from "../../../types/project";

export interface SceneCodeInput {
  /** The scene's place in the story, from 1. */
  n: number;
  title: string;
  role: string;
  mode: ShotMode;
  /** The stored shot: which profile each performer names. */
  stored: Shot | undefined;
  /** The shot as drawn (castShot). */
  shot: MotionShot;
  cueWord: string | null;
  durS: number;
  captions: boolean;
  /** The phone layout's library image name; null draws the generated app screen. */
  screenName: string | null;
  /** Phones 2 and 3's library image names (`screen.more`), if any. */
  moreScreens?: string[];
}

/** An attribute: a quoted string, a bare flag (true), a `{expression}` ({raw}), or left out. */
type Attr = [string, string | true | { raw: string } | undefined];

function tag(name: string, attrs: Attr[], children?: string): string {
  const parts = attrs.flatMap(([k, v]) =>
    v === undefined || v === "" ? [] : [v === true ? k : typeof v === "string" ? `${k}="${v.replace(/"/g, "&quot;")}"` : `${k}={${v.raw}}`]);
  const open = `<${name}${parts.map((p) => ` ${p}`).join("")}`;
  return children === undefined ? `${open} />` : `${open}>${children}</${name}>`;
}

const look = (p: { pose: string; expression: string; move: string; bubble?: string }): Attr[] =>
  [["pose", p.pose], ["expression", p.expression], ["move", p.move], ["bubble", p.bubble]];

function performers(stored: Shot | undefined, shot: MotionShot): string[] {
  const out = [tag("Character", [["character", stored?.character], ...look(shot)])];
  if (shot.costar) {
    const interaction = shot.interaction === "none" ? undefined : shot.interaction;
    out.push(tag("Costar", [["character", stored?.costar?.character], ...look(shot.costar), ["interaction", interaction]]));
  }
  if (shot.ensemble?.length) {
    out.push("<Ensemble>", ...shot.ensemble.map((m, i) => `  ${tag("Character", [["character", stored?.ensemble?.[i]?.character], ...look(m)])}`), "</Ensemble>");
  }
  return out;
}

function picture({ mode, stored, shot, screenName, moreScreens }: SceneCodeInput, cue: Attr[1]): string[] {
  if (mode === "footage") return ["<Footage />"];
  if (mode === "cameo") return ["<Footage />", tag("Character", [["cameo", true], ["character", stored?.character], ...look(shot)])];
  const layout = shot.layout ?? "stage";
  const headline = tag("Headline", [["eyebrow", shot.eyebrow], ["over", layout === "stage" ? shot.headlineAt : undefined]], shot.headline);
  const out = [tag("Backdrop", [["kind", shot.backdrop]])];
  if (layout === "stage") {
    out.push(tag("Diagram", [["kind", shot.diagram], ["at", cue], ["labels", shot.boardLabels?.join(" | ")]]), ...performers(stored, shot), headline);
    if (shot.ensemble?.length) out.push(...(shot.badges ?? []).slice(0, shot.ensemble.length).map((b, i) => tag("Badge", [["tone", b.tone], ["under", String(i + 1)]], b.text)));
  }
  else if (layout === "screen") {
    out.push(tag("Phone", [["screen", screenName ?? "app screen"]]), ...(moreScreens ?? []).map((name) => tag("Phone", [["screen", name]])), headline);
  }
  else if (layout === "title") out.push("<TitleCard>", ...(shot.badges ?? []).map((b) => `  ${tag("Badge", [["tone", b.tone]], b.text)}`), "</TitleCard>", headline);
  else out.push(tag("SignOff", [["mark", shot.mark]]), tag("Character", [["character", stored?.character], ...look(shot)]), headline);
  if (shot.camera) out.push(tag("Camera", [["move", shot.camera]]));
  return out;
}

export function sceneCode(input: SceneCodeInput): string {
  const { n, title, role, mode, cueWord, durS, captions } = input;
  const nn = String(n).padStart(2, "0");
  const word = mode !== "footage" && cueWord ? bare(cueWord) : "";
  const body = picture(input, word ? { raw: "cue" } : undefined);
  if (captions) body.push("<Captions words={scene.words} />");
  const names = [...new Set(body.flatMap((l) => [...l.matchAll(/^\s*<([A-Z]\w*)/g)].map((m) => m[1])))];
  return [
    "// Illustrative pseudocode: these JSX tags and words helpers are not executable APIs.",
    `// scenes/scene-${nn}.tsx · “${title}” · ${role} · motion-core draws it in the preview and the export`,
    `import {${["Scene", ...names].join(", ")}} from '../studio';`,
    "import words from '../words.json';",
    "",
    `const scene = words.scene(${n});    // WhisperX timings · ${durS.toFixed(1)} s`,
    ...(word ? [`const cue = scene.word('${word.replace(/'/g, "\\'")}').start;    // “Lands on”: the beat lands here`] : []),
    "",
    `export const Scene${nn} = () => (`,
    `  <Scene mode="${mode}">`,
    ...body.map((l) => `    ${l}`),
    "  </Scene>",
    ");",
    "",
  ].join("\n");
}
