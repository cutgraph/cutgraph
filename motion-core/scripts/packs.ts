import { readdir, readFile, stat } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { selectPackManifests, registerPack } from '../src/characters.ts';

export async function loadPacks(dir: string): Promise<void> {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') console.warn(`Skipping character packs: ${String(error)}`);
    return;
  }
  const manifests: { id: string; manifest: unknown }[] = [];
  for (const entry of entries.sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0)) {
    if (!entry.isDirectory()) continue;
    const manifestPath = join(dir, entry.name, 'pack.json');
    const modulePath = resolve(dir, entry.name, 'index.ts');
    try {
      const present = await Promise.all([manifestPath, modulePath].map(async (path) => {
        try { return (await stat(path)).isFile(); }
        catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return false; throw error; }
      }));
      if (!present.every(Boolean)) continue;
      const manifest: unknown = JSON.parse(await readFile(manifestPath, 'utf8'));
      manifests.push({ id: entry.name, manifest });
    } catch (error) {
      console.warn(`Skipping character pack ${entry.name}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  for (const { id, rigs } of selectPackManifests(manifests)) {
    try {
      const module = await import(pathToFileURL(resolve(dir, id, 'index.ts')).href);
      registerPack({ characters: rigs }, module.characters, id);
    } catch (error) {
      console.warn(`Skipping character pack ${id}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
}

export function packArgs(argv: string[]): { args: string[]; dir?: string } {
  const args = [...argv];
  const index = args.indexOf('--packs');
  if (index < 0) return { args };
  const dir = args[index + 1];
  if (!dir || dir === '--packs') throw new Error('--packs requires a directory');
  args.splice(index, 2);
  return { args, dir };
}
