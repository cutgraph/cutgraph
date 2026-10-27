// Three-way JSON rebase for the save safety net. A save conflict means another writer moved the
// server document on from the base this tab last synced. `changes` lists what each side did to that
// base; when the two lists touch disjoint paths, `fold` carries the server's changes into the local
// document (and its undo snapshots) so the save can retry without overwriting anyone.

export type Path = readonly (string | number)[];

/** One changed JSON path and its new value (`undefined`: the key was removed). */
export interface Change {
  path: Path;
  value: unknown;
}

type Obj = Record<string, unknown>;

const isObject = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);

const child = (node: unknown, key: string | number): unknown =>
  Array.isArray(node) ? node[key as number] : isObject(node) ? node[key as string] : undefined;

export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b || a === null || b === null) return false;
  if (typeof a !== "object") return false;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((v, i) => deepEqual(v, b[i]));
  }
  const ao = a as Obj;
  const bo = b as Obj;
  const ak = Object.keys(ao);
  const bk = Object.keys(bo);
  if (ak.length !== bk.length) return false;
  return ak.every((k) => k in bo && deepEqual(ao[k], bo[k]));
}

/** The deepest paths at which `next` differs from `base`. An array that changed length is one change. */
export function changes(base: unknown, next: unknown, path: Path = []): Change[] {
  if (deepEqual(base, next)) return [];
  if (isObject(base) && isObject(next)) {
    const keys = new Set([...Object.keys(base), ...Object.keys(next)]);
    return [...keys].flatMap((k) => changes(base[k], next[k], [...path, k]));
  }
  if (Array.isArray(base) && Array.isArray(next) && base.length === next.length) {
    return base.flatMap((v, i) => changes(v, next[i], [...path, i]));
  }
  return [{ path, value: next }];
}

// Two paths touch when one is a prefix of the other: the same value, or one nested inside the other.
function touches(a: Path, b: Path): boolean {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i += 1) if (a[i] !== b[i]) return false;
  return true;
}

export function overlaps(a: Change[], b: Change[]): boolean {
  return a.some((x) => b.some((y) => touches(x.path, y.path)));
}

// `doc` still holds `base`'s value at `path`, reached through the same containers. Objects that carry an
// `id` (scenes, tracks, clips) must be the same one, so a snapshot taken before a reorder never takes a
// change meant for the element that now sits at that index.
function agrees(doc: unknown, base: unknown, path: Path): boolean {
  let d = doc;
  let b = base;
  for (const key of path) {
    if (Array.isArray(b) ? !Array.isArray(d) : !isObject(b) || !isObject(d) || ("id" in b && d.id !== b.id)) return false;
    d = child(d, key);
    b = child(b, key);
  }
  return deepEqual(d, b);
}

function setIn(node: unknown, path: Path, value: unknown): unknown {
  if (path.length === 0) return value;
  const [key, ...rest] = path;
  if (Array.isArray(node)) {
    const copy = node.slice();
    copy[key as number] = setIn(node[key as number], rest, value);
    return copy;
  }
  const copy: Obj = { ...(node as Obj) };
  const next = setIn(copy[key as string], rest, value);
  if (next === undefined) delete copy[key as string];
  else copy[key as string] = next;
  return copy;
}

/** `doc` with every change applied wherever `doc` still agrees with `base` at that change's path. */
export function fold<T>(doc: T, list: readonly Change[], base: unknown): T {
  return list.reduce<T>((d, c) => (agrees(d, base, c.path) ? (setIn(d, c.path, c.value) as T) : d), doc);
}
