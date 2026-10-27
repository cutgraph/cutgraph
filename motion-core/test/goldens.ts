import { existsSync, readFileSync } from 'node:fs';
import { registerFonts } from '../scripts/node-env.ts';

export function loadGolden<T>(name: string): { value: T | null; skip: string | false } {
  const path = new URL(`./${name}.golden.${process.platform}.json`, import.meta.url);
  if (!existsSync(path)) {
    const message = `Missing platform golden: ${path.pathname}; run node scripts/update-goldens.ts`;
    if (process.platform === 'win32') throw new Error(message);
    // Other platforms skip until their fonts and pixels are qualified.
    return { value: null, skip: message };
  }
  // This opt-out is for hosts intentionally lacking the recorded fonts; it must be visible in TAP.
  if (process.env.CUTGRAPH_ALLOW_MISSING_TEST_FONTS === '1' && !registerFonts()) {
    return { value: null, skip: 'Required fonts unavailable; CUTGRAPH_ALLOW_MISSING_TEST_FONTS=1' };
  }
  registerFonts(true);
  return { value: JSON.parse(readFileSync(path, 'utf8')) as T, skip: false };
}
