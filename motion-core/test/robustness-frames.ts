// Placeholder and no-mark frames are independent of the reference film.
import type { FilmShot } from './film-frames.ts';

export const ROBUSTNESS_FRAMES: FilmShot[] = [
  { name: 'missing-character', role: 'Beat', dur: 2, cue: null,
    shot: { mode: 'animation', character: 'unregistered-rig', hideHeadline: true, diagram: 'none' } },
  { name: 'missing-costar', role: 'Beat', dur: 3, cue: null,
    shot: { mode: 'animation', hideHeadline: true, diagram: 'none',
      costar: { character: 'unregistered-rig', move: 'idle' }, interaction: 'face' } },
  { name: 'missing-ensemble-entrance', role: 'Beat', dur: 3, cue: null,
    shot: { mode: 'animation', hideHeadline: true, diagram: 'none',
      ensemble: [{ character: 'unregistered-rig', move: 'enter' }] } },
  { name: 'missing-cameo', role: 'Beat', dur: 3, cue: null,
    shot: { mode: 'cameo', character: 'unregistered-rig' } },
  { name: 'unbranded-signoff', role: 'Wrap', dur: 3.2, cue: null,
    shot: { mode: 'animation', layout: 'signoff', backdrop: 'vision', character: 'lens', headline: 'End of sample' } },
  ...(['grid', 'chain', 'radial', 'tree', 'list'] as const).map((diagram): FilmShot => ({
    name: `${diagram}-diagram`, role: 'Beat', dur: 2.4, cue: 0.2,
    shot: { mode: 'animation', diagram, backdrop: 'work', hideHeadline: true, pose: 'rest',
      boardLabels: ['First', '', 'Third', 'Fourth', 'Next', 'Last'] },
  })),
  { name: 'neutral-pair', role: 'Beat', dur: 2.1, cue: 0.2,
    shot: { mode: 'animation', diagram: 'none', backdrop: 'black', bubble: 'exclaims', hideHeadline: true } },
  { name: 'independent-push', role: 'Beat', dur: 0.6, cue: null,
    shot: { mode: 'animation', character: 'unregistered-rig', camera: 'push-in', diagram: 'none', backdrop: 'work' } },
  { name: 'independent-pull', role: 'Beat', dur: 0.5, cue: null,
    shot: { mode: 'animation', camera: 'pull-out', diagram: 'none', backdrop: 'black', headline: 'Framing' } },
  { name: 'neutral-badges', role: 'Beat', dur: 3, cue: null,
    shot: { mode: 'animation', layout: 'title', backdrop: 'black', headline: 'Labels',
      badges: [{ text: 'INK', tone: 'ink' }, { text: 'PAPER', tone: 'paper' }, { text: 'COLOR', tone: 'accent' }], accent: '#2bb3a3' } },
];
