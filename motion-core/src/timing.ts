import type { SceneTiming, ScriptProject, TimedCue, TimedWord, Timeline } from './types.ts';

export const LEAD = 0.45;
export const TAIL = 0.55;

export const bare = (w: string): string => w.toLowerCase().replace(/[^a-z']/g, '');

/** Estimated spoken duration of one word, used until real narration timings exist. */
export function wordDur(w: string): number {
  const n = bare(w);
  if (n === 'um' || n === 'uh') return 0.42;
  let d = 0.17 + 0.047 * n.length;
  if (/[,;:]$/.test(w)) d += 0.22;
  if (/[.?!]$/.test(w)) d += 0.38;
  return d;
}

export function timeline(P: ScriptProject): Timeline {
  const scenes: SceneTiming[] = [], words: TimedWord[] = [];
  let t = 0;
  P.scenes.forEach((sc, si) => {
    const start = t, ws: TimedWord[] = [];
    let seg = 0, cutSince = false;
    t += LEAD;
    sc.words.forEach((wd, i) => {
      if (wd.cut) {
        ws.push({ si, i, w: wd.w, start: 0, end: 0, seg, cut: true });
        cutSince = true;
        return;
      }
      if (cutSince && ws.some((x) => !x.cut)) seg++;
      cutSince = false;
      const r: TimedWord = { si, i, w: wd.w, start: t, end: t + wordDur(wd.w), seg };
      ws.push(r);
      words.push(r);
      t = r.end;
    });
    t += TAIL;
    // A cut word collapses onto the start of the next kept word.
    let next = t - TAIL;
    for (let k = ws.length - 1; k >= 0; k--) {
      if (ws[k].cut) ws[k].start = ws[k].end = next;
      else next = ws[k].start;
    }
    scenes.push({ si, start, end: t, words: ws, cues: [] });
  });
  const dur = t;
  for (const S of scenes) {
    S.cues = P.scenes[S.si].cues.map((c, ci): TimedCue => {
      const a = S.words[Math.min(c.at ?? 0, S.words.length - 1)], st = a ? a.start : S.start + LEAD;
      let en = st + 3.4;
      if (c.type === 'broll') {
        const kept = S.words.filter((w) => !w.cut && w.i >= (c.at ?? 0)).slice(0, c.len || 8);
        en = Math.min(Math.max(kept.length ? kept[kept.length - 1].end : st + 3, st + 1.6), S.end);
      } else if (c.type === 'graphic') en = Math.min(st + (c.item === 'steps' ? 4.6 : 3.6), S.end);
      else if (c.type === 'anim') en = Math.min(st + 3.8, dur);
      else if (c.type === 'sfx') en = st + 0.9;
      return { ...c, ci, si: S.si, start: st, end: en };
    });
    const sh = P.scenes[S.si].shot;
    if (sh) {
      const w = S.words[Math.min(sh.cueAt ?? 0, S.words.length - 1)];
      S.cueT = (w ? w.start : S.start + LEAD) - S.start;
      S.firstT = (S.words.find((x) => !x.cut)?.start ?? S.start + LEAD) - S.start;
    }
  }
  const music = scenes.flatMap((S) => S.cues.filter((c) => c.type === 'music')).sort((a, b) => a.start - b.start);
  music.forEach((m, k) => { m.end = k + 1 < music.length ? music[k + 1].start : dur; });
  return { dur, scenes, words, music };
}

export const sceneAt = (TL: Timeline, t: number): SceneTiming | undefined =>
  TL.scenes.find((S) => t < S.end) ?? TL.scenes[TL.scenes.length - 1];

export const wordAt = (TL: Timeline, t: number): TimedWord | null =>
  TL.words.find((w) => t >= w.start && t < w.end) ?? null;
