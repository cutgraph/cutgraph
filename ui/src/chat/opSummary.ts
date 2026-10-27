// Pure RFC-6902 → human-readable op summary for a proposal diff card. Resolves each
// op's JSON-Pointer path against the CURRENT project doc (clip/item by index → id, frames → mm:ss) so
// the card reads "cut 0:41–0:47 · trim clip c4 · move b-roll b1". No React/DOM — node-testable.

import type { Project } from "../types/project";
import { mmss } from "../timeline/geometry";

interface Op {
  op: string;
  path: string;
  value?: unknown;
}

function segments(path: string): string[] {
  return path.split("/").filter(Boolean);
}

// video track → "b-roll"; the rest read literally (speech clips are just "clip").
function trackLabel(kind: string | undefined): string {
  if (kind === "video") return "b-roll";
  if (kind === "audio") return "audio";
  return "clip";
}

function describeGeneric(op: Op): string {
  const top = segments(op.path)[0] ?? "";
  return top ? `edit ${top}` : "edit";
}

export function summarizeOps(ops: unknown[], project: Project | null, fps: number): string {
  const phrases: string[] = [];
  const seen = new Set<string>();
  const push = (s: string): void => {
    if (!seen.has(s)) {
      seen.add(s);
      phrases.push(s);
    }
  };

  for (const raw of ops ?? []) {
    const op = raw as Op;
    if (!op || typeof op.path !== "string") continue;
    if (op.op === "test") continue; // an identity/value guard, not a user-visible change

    const parts = segments(op.path); // e.g. ["tracks","0","clips","3","srcOutF"]
    if (parts[0] !== "tracks") {
      push(describeGeneric(op));
      continue;
    }
    const t = Number(parts[1]);
    const kind = parts[2]; // "clips" | "items"
    const idx = Number(parts[3]);
    const field = parts[4]; // undefined for a whole clip/item add/remove
    const track = project?.tracks?.[t];

    if (kind === "clips") {
      const clip = track?.clips?.[idx];
      if (op.op === "remove" && field === undefined) {
        if (clip) {
          const end = clip.tlStartF + (clip.srcOutF - clip.srcInF);
          push(`cut ${mmss(clip.tlStartF, fps)}–${mmss(end, fps)}`);
        } else {
          push("cut clip");
        }
      } else if (op.op === "add" && field === undefined) {
        push("add clip");
      } else if (field === "srcInF" || field === "srcOutF") {
        push(`trim ${trackLabel(track?.kind)} ${clip?.id ?? parts[3]}`);
      } else if (field === "tlStartF") {
        push(`move ${trackLabel(track?.kind)} ${clip?.id ?? parts[3]}`);
      } else {
        push(`edit ${trackLabel(track?.kind)} ${clip?.id ?? parts[3]}`);
      }
    } else if (kind === "items") {
      const item = track?.items?.[idx];
      if (op.op === "add") {
        push("add text");
      } else if (op.op === "remove" && field === undefined) {
        push(`remove text ${item?.id ?? parts[3]}`);
      } else if (field === "tlStartF") {
        push(`move text ${item?.id ?? parts[3]}`);
      } else {
        push(`edit text ${item?.id ?? parts[3]}`);
      }
    } else {
      push(describeGeneric(op));
    }
  }

  return phrases.join(" · ") || "no changes";
}
