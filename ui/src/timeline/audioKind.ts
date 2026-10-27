import type { Clip, Track } from "../types/project";

export type AudioKind = "music" | "sfx";

// Music or a sound effect: the one rule behind every place an audio clip is named (the script chip,
// the cue card, the Source view and the Timeline lane). The drawer and the co-pilot duck music under
// speech and never a sound effect (v3/story/drawer/insert.ts, pipeline/story_insert.py), so the duck
// is what marks a music bed.
export function audioKind(clip: Pick<Clip, "duckDb">): AudioKind {
  return clip.duckDb != null ? "music" : "sfx";
}

// An audio lane is named for its clips when they agree, else it is just audio.
export function audioLaneKind(track: Track): AudioKind | "audio" {
  const kinds = new Set((track.clips ?? []).map(audioKind));
  return kinds.size === 1 ? [...kinds][0] : "audio";
}
