// Usage: node scripts/update-goldens.ts; writes recordings for process.platform only.
import { readFileSync, existsSync, writeFileSync } from 'node:fs';
import { FORMATS, type Format } from '../src/index.ts';
import { fontPrint, nodeEnv, registerFonts } from './node-env.ts';
import { SINGLE_FRAMES, singleFrameHash } from '../test/single-frames.ts';
import { FILM_SHOTS, filmShotHash } from '../test/film-frames.ts';
import { ROBUSTNESS_FRAMES } from '../test/robustness-frames.ts';
import { HEADLINE_FRAMES, headlineFrameHash } from '../test/headline-frames.ts';
import { LENS_FRAMES, lensFrameHash } from '../test/lens-frames.ts';
import { onePhoneHash } from '../test/one-phone-frames.ts';

registerFonts(true);
const env = nodeEnv();
const recordings = {
  'lens-frames': Object.fromEntries(LENS_FRAMES.map((f) => [f.name, lensFrameHash(f, env)])),
  'single-frames': Object.fromEntries(SINGLE_FRAMES.map((f) => [f.name, singleFrameHash(f, env)])),
  'film-frames': Object.fromEntries(FILM_SHOTS.flatMap((f) => (Object.keys(FORMATS) as Format[])
    .map((format) => [`${f.name} ${format}`, filmShotHash(f, format, env)]))),
  'robustness-frames': Object.fromEntries(ROBUSTNESS_FRAMES.flatMap((f) => (Object.keys(FORMATS) as Format[])
    .map((format) => [`${f.name} ${format}`, filmShotHash(f, format, env)]))),
  'headline-frames': { fonts: fontPrint(), frames: Object.fromEntries(HEADLINE_FRAMES.map((f) => [f.name, headlineFrameHash(f)])) },
  'one-phone-frames': { frames: onePhoneHash(env) },
};
for (const [name, recording] of Object.entries(recordings)) {
  const path = new URL(`../test/${name}.golden.${process.platform}.json`, import.meta.url);
  const content = JSON.stringify(recording, null, 2) + '\n';
  if (!existsSync(path) || readFileSync(path, 'utf8') !== content) {
    writeFileSync(path, content);
    console.log(`Regenerated ${name}.golden.${process.platform}.json`);
  } else console.log(`Current ${name}.golden.${process.platform}.json`);
}
