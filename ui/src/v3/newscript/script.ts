// The script -> scenes rule for a new video from a script, for the sheet's live preview.
// pipeline/script_video.py parse_script is the same rule on the server; both are pinned to
// tests/fixtures/script_video_golden.json, and the timing is motion-core's own `timeline`.
import { timeline } from "motion-core";
import type { Scene } from "../../types/project";

export type Role = Scene["role"];

export interface ScriptScene {
  title: string;
  role: Role;
  words: string[];
}

const ROLES: Role[] = ["Hook", "Problem", "Approach", "Build", "Example", "Check", "Payoff", "Wrap", "Beat"];
const TITLE_MAX = 120;
const TITLE_WORDS = 5;
const BOM = String.fromCharCode(0xfeff);
const HEADING = /^ {0,3}#{1,6}\s+(.*)$/;
const ROLE_PREFIX = /^([A-Za-z]+)\s*:\s*(.*)$/;
// Markdown emphasis/code marks, plus the ASS metacharacters a caption word is burned in with verbatim.
const STRIP = /[*_`{}\\]/g;
const SPEAKABLE = /[\p{L}\p{N}]/u;

const words = (text: string): string[] => text.split(/\s+/).filter(Boolean);

function tokens(line: string): string[] {
  return words(line).map((raw) => raw.replace(STRIP, "")).filter((w) => SPEAKABLE.test(w));
}

function titleFrom(scene: string[]): string {
  const head = scene.slice(0, TITLE_WORDS).join(" ").replace(/[,;:.!?]+$/, "");
  return scene.length > TITLE_WORDS ? `${head}…` : head;
}

/** `## Title` headings start scenes (paragraphs do when there are none). A heading may name its role
 * (`## Problem: An empty pantry`); otherwise the first scene is the Hook, the last the Wrap, the rest
 * Beats. Text before the first heading is a scene of its own; a heading with no words is dropped. */
export function parseScript(text: string): ScriptScene[] {
  const lines = text.split(BOM).join("").split(/\r\n|\r|\n/);
  const hasHeading = lines.some((line) => HEADING.test(line));
  const sections: { heading: string | null; words: string[] }[] = [];
  let current: { heading: string | null; words: string[] } | null = null;
  for (const line of lines) {
    const heading = HEADING.exec(line);
    if (heading) {
      current = { heading: heading[1], words: [] };
      sections.push(current);
    } else if (!line.trim()) {
      if (!hasHeading) current = null;
    } else {
      if (!current) {
        current = { heading: null, words: [] };
        sections.push(current);
      }
      current.words.push(...tokens(line));
    }
  }
  const kept = sections.filter((s) => s.words.length > 0);
  return kept.map(({ heading, words: said }, i) => {
    let title = words((heading ?? "").replace(STRIP, "")).join(" ");
    let role: Role | undefined;
    const prefix = ROLE_PREFIX.exec(title);
    const named = prefix ? ROLES.find((r) => r.toLowerCase() === prefix[1].toLowerCase()) : undefined;
    if (prefix && named) {
      role = named;
      title = prefix[2].trim();
    }
    role ??= i === 0 ? "Hook" : i === kept.length - 1 ? "Wrap" : "Beat";
    return { title: (title || titleFrom(said)).slice(0, TITLE_MAX), role, words: said };
  });
}

export interface Estimate {
  /** Seconds for the whole video, as the words sidecar will time it. */
  total: number;
  /** Seconds per scene, lead-in and tail included. */
  scenes: number[];
}

export function estimate(scenes: ScriptScene[]): Estimate {
  const TL = timeline({ scenes: scenes.map((s) => ({ words: s.words.map((w) => ({ w })), cues: [] })) });
  return { total: TL.dur, scenes: TL.scenes.map((s) => s.end - s.start) };
}

export const SAMPLE_TITLE = "A small balcony garden";

// Clearly labelled starter text for a gardening story.
export const SAMPLE_SCRIPT = `## Hook: A little green space
A sunny balcony can become a small garden, even with just a few pots.

## Problem: Too much choice
Choosing plants is easier when you know how much sun reaches the space.

## Approach: Start small
Pick two herbs you enjoy cooking with and give each one a roomy pot.

## Build: Soil and water
Add fresh soil, plant gently, and water until the soil feels evenly damp.

## Check: Watch the leaves
Check the leaves and the soil each morning, then adjust the water as needed.

## Payoff: Fresh herbs
Soon a handful of fresh herbs can turn an ordinary meal into something special.
`;
