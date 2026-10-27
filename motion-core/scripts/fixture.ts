// A synthetic gardening story, with cue and shot anchors resolved to word indices.
import { bare } from '../src/index.ts';
import type { CueType, ScriptProject, Shot } from '../src/index.ts';

interface SampleScene {
  text: string;
  shot: Omit<Shot, 'cueAt'> & { cue: string };
  cues: { type: CueType; at: string; item: string }[];
}

const SCENES: SampleScene[] = [
  { text: 'A sunny balcony can become a small garden with just a few pots.',
    shot: { pose: 'shrug', expression: 'curious', move: 'idle', backdrop: 'chaos', diagram: 'scatter', cue: 'garden', headline: 'A little green space.' },
    cues: [{ type: 'music', at: 'sunny', item: 'curious-plucks' }, { type: 'sfx', at: 'pots.', item: 'pop' }] },
  { text: 'Choosing plants is easier when you know how much sun reaches the space.',
    shot: { pose: 'think', expression: 'focused', move: 'think', backdrop: 'work', diagram: 'blank', cue: 'sun', headline: 'Find the sunny spot.' },
    cues: [] },
  { text: 'Pick two herbs you enjoy cooking with and give each one a roomy pot.',
    shot: { pose: 'hold', expression: 'idea', move: 'hop', backdrop: 'work', diagram: 'brief', cue: 'herbs', headline: 'Start with two herbs.' },
    cues: [{ type: 'sfx', at: 'herbs', item: 'chime' }] },
  { text: 'Add fresh soil, plant gently, and water until the soil feels evenly damp.',
    shot: { pose: 'point', expression: 'happy', move: 'enter', backdrop: 'route', diagram: 'route', cue: 'water', headline: 'Soil, plants and water.' },
    cues: [] },
  { text: 'Check the leaves and the soil each morning, then adjust the water as needed.',
    shot: { pose: 'rest', expression: 'curious', move: 'nod', backdrop: 'proof', diagram: 'checks', cue: 'morning,', headline: 'Watch the leaves.' },
    cues: [] },
  { text: 'Soon a handful of fresh herbs can turn an ordinary meal into something special.',
    shot: { pose: 'cheer', expression: 'excited', move: 'celebrate', backdrop: 'result', diagram: 'glow', cue: 'fresh', headline: 'Fresh herbs for dinner.' },
    cues: [{ type: 'music', at: 'Soon', item: 'warm-resolve' }] },
];

export function sampleProject(): ScriptProject {
  return {
    scenes: SCENES.map((sc) => {
      const words = sc.text.split(/\s+/).filter(Boolean).map((w) => ({ w, cut: false }));
      const find = (tok: string): number => {
        let i = words.findIndex((x) => x.w === tok);
        if (i < 0) i = words.findIndex((x) => bare(x.w) === bare(tok));
        return Math.max(0, i);
      };
      const { cue, ...shot } = sc.shot;
      return { words, cues: sc.cues.map((c) => ({ ...c, at: find(c.at) })), shot: { ...shot, cueAt: find(cue) } };
    }),
  };
}
