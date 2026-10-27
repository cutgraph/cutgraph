// script.mdx: a read-only text projection of the open video. Pure, so the Source lens and its golden
// test agree byte for byte: frontmatter (title, kind, source or narration, words, format
// and captions), one `## Title {Role}` section per scene in story order, the scene's words with cut runs
// struck as ~~…~~, and a tag for each cue at its anchor word, naming clips as the library does.

import { shotMode } from "motion-core";
import type { TranscriptWord } from "../../../api";
import type { Project, Shot } from "../../../types/project";
import type { SceneSpan } from "../model";
import { basename, clipName, wordCues, type Cue } from "./cues";

export interface MdxInput {
  project: Project;
  media: string | null;
  words: TranscriptWord[];
  cut: Set<number>;
  spans: SceneSpan[];
  /** Library media ref → imported name (drawer/library.ts assetLabels). */
  labels?: ReadonlyMap<string, string>;
}

const WRAP = 92;

function attr(name: string, value: string | number | boolean): string {
  if (value === true) return name;
  return typeof value === "string" ? `${name}="${value.replace(/"/g, "&quot;")}"` : `${name}={${String(value)}}`;
}

function tag(name: string, attrs: [string, unknown][]): string {
  const parts = attrs
    .filter((a): a is [string, string | number | boolean] => ["string", "number", "boolean"].includes(typeof a[1]))
    .map(([k, v]) => attr(k, v));
  return `<${name}${parts.map((p) => ` ${p}`).join("")} />`;
}

// Clip tags: <Broll clip="…" />, <Music track="…" />, <Sfx sound="…" />.
const CLIP_TAG = { broll: ["Broll", "clip"], music: ["Music", "track"], sfx: ["Sfx", "sound"] } as const;

function cueTag(cue: Cue, labels?: ReadonlyMap<string, string>): string {
  if (cue.kind === "graphic") return tag("Graphic", [["component", cue.item.componentId], ...Object.entries(cue.item.props)]);
  if (cue.kind === "text") return tag("Text", [["preset", cue.item.preset], ["text", cue.item.text]]);
  const [name, attr] = CLIP_TAG[cue.kind];
  return tag(name, [[attr, clipName(cue.clip, labels)]]);
}

const SHOT_KEYS = ["character", "pose", "expression", "move", "backdrop", "diagram", "headline"] as const;

// A footage scene keeps its last look for switching back, but plays no Character;
// a cameo uses the <Character cameo … /> tag.
function characterTag(shot: Shot, words: TranscriptWord[]): string | null {
  const mode = shotMode(shot);
  if (mode === "footage") return null;
  const cue = shot.cueWord !== undefined ? words[shot.cueWord]?.w : undefined;
  return tag("Character", [["cameo", mode === "cameo" || undefined], ...SHOT_KEYS.map((k): [string, unknown] => [k, shot[k]]), ["on", cue]]);
}

// Align a frontmatter line's note in a shared comment column.
function noted(line: string, note: string): string {
  return `${line.padEnd(26)} # ${note}`;
}

// Frontmatter describes the video, its picture and word sources, and how it exports.
function frontmatter(project: Project, media: string | null): string[] {
  const title = project.title?.trim() || project.name;
  const file = media ? basename(media) : "none";
  const script = project.narration != null;
  const estimated = project.narration === "estimated";
  return [
    "---",
    `title: ${JSON.stringify(title)}`,
    `kind: ${script ? "animation" : "footage"}`,
    !script ? `source: ${file}` : estimated ? noted("narration: none", "estimated timing until you record it") : noted(`narration: ${file}`, "your recording"),
    noted("words: words.json", `${estimated ? "estimated" : "WhisperX"} timings, one entry per word`),
    `format: "${aspect(project.width, project.height)}"`,
    `captions: ${project.captions?.enabled === true ? "burned-in" : "off"}`,
    "---",
  ];
}

function aspect(width: number, height: number): string {
  const gcd = (a: number, b: number): number => (b ? gcd(b, a % b) : a);
  const d = gcd(width, height) || 1;
  return `${width / d}:${height / d}`;
}

function wrap(tokens: string[]): string[] {
  const lines: string[] = [];
  let line = "";
  for (const t of tokens) {
    if (line && line.length + 1 + t.length > WRAP) {
      lines.push(line);
      line = t;
    } else {
      line = line ? `${line} ${t}` : t;
    }
  }
  if (line) lines.push(line);
  return lines;
}

function sceneTokens(span: SceneSpan, words: TranscriptWord[], cut: Set<number>, cuesAt: Map<number, Cue[]>, labels?: ReadonlyMap<string, string>): string[] {
  const tokens: string[] = [];
  let struck = false;
  const unstrike = () => {
    if (struck) tokens[tokens.length - 1] += "~~";
    struck = false;
  };
  for (let i = span.startWord; i < span.endWord; i++) {
    const here = cuesAt.get(i);
    if (here) {
      unstrike();
      tokens.push(...here.map((cue) => cueTag(cue, labels)));
    }
    const w = words[i].w;
    if (!w) continue;
    if (cut.has(i)) {
      tokens.push(struck ? w : `~~${w}`);
      struck = true;
    } else {
      unstrike();
      tokens.push(w);
    }
  }
  unstrike();
  return tokens;
}

export function scriptMdx({ project, media, words, cut, spans, labels }: MdxInput): string {
  const cuesAt = new Map<number, Cue[]>();
  for (const cue of wordCues(project, media, words.length)) cuesAt.set(cue.wordIdx, [...(cuesAt.get(cue.wordIdx) ?? []), cue]);
  const out = frontmatter(project, media);
  for (const span of spans) {
    out.push("", `## ${span.scene.title} {${span.scene.role}}`, "");
    const character = span.scene.shot && characterTag(span.scene.shot, words);
    if (character) out.push(character);
    out.push(...wrap(sceneTokens(span, words, cut, cuesAt, labels)));
  }
  return `${out.join("\n")}\n`;
}
