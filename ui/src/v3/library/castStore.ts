// The cast shared by the Library, the Shot view and the Story player: what each scope (a video, a
// workspace, or the brand alone) can cast, loaded from the studio once per scope. Every write reloads
// every loaded scope, since a brand character or expression shows in all of them.
import { useEffect } from "react";
import { create } from "zustand";
import { characterOf, type PartSpec } from "motion-core";
import { useProjectStore } from "../../store/useProjectStore";
import {
  v3api, type BrandPatch, type CastExpression, type CastScope, type CharacterPatch, type CharacterProfile, type NewCharacter, type Workspace,
} from "../api";
import { errorText } from "../common";
import { NO_CAST, parseCast, parseCharacter, parseExpression, type Cast } from "./cast";

export interface ScopeState {
  cast: Cast;
  status: "idle" | "loading" | "ready" | "error";
  error: string | null;
}

export interface CastStore {
  scopes: Readonly<Record<string, ScopeState>>;
}

const IDLE: ScopeState = { cast: NO_CAST, status: "idle", error: null };

export const useCastStore = create<CastStore>(() => ({ scopes: {} }));

export const resetCast = (): void => useCastStore.setState({ scopes: {} });

export const scopeKey = ({ workspace, video }: CastScope): string =>
  video ? `video:${video}` : workspace ? `ws:${workspace}` : "brand";

function scopeOf(key: string): CastScope {
  if (key.startsWith("video:")) return { video: key.slice(6) };
  if (key.startsWith("ws:")) return { workspace: key.slice(3) };
  return {};
}

function patchScope(key: string, next: Partial<ScopeState>): void {
  useCastStore.setState((s) => ({ scopes: { ...s.scopes, [key]: { ...(s.scopes[key] ?? IDLE), ...next } } }));
}

const loading = new Map<string, Promise<void>>();

export function loadCast(scope: CastScope = {}): Promise<void> {
  const key = scopeKey(scope);
  let pending = loading.get(key);
  if (!pending) {
    pending = (async () => {
      if (useCastStore.getState().scopes[key]?.status !== "ready") patchScope(key, { status: "loading" });
      try {
        const cast = parseCast(await v3api.cast(scope));
        if (!cast) throw new Error("The studio returned a cast with no character to draw.");
        patchScope(key, { cast, status: "ready", error: null });
      } catch (err) {
        patchScope(key, { status: "error", error: errorText(err) });
      } finally {
        loading.delete(key);
      }
    })();
    loading.set(key, pending);
  }
  return pending;
}

/** Reload every scope that has been asked for, after any in-flight load (which may predate a write). */
function reloadAll(): Promise<void> {
  return Promise.all(Object.keys(useCastStore.getState().scopes).map((key) => {
    const pending = loading.get(key);
    return pending ? pending.then(() => loadCast(scopeOf(key))) : loadCast(scopeOf(key));
  })).then(() => undefined);
}

/**
 * A scope's cast, loaded the first time a surface that draws it needs it (and retried after a failure).
 * A surface that only labels what others draw passes `load = false` and reads what is there.
 */
export function useCast(scope: CastScope = {}, load = true): ScopeState {
  const key = scopeKey(scope);
  const state = useCastStore((s) => s.scopes[key]) ?? IDLE;
  useEffect(() => {
    const status = useCastStore.getState().scopes[key]?.status ?? "idle";
    if (load && (status === "idle" || status === "error")) void loadCast(scopeOf(key));
  }, [key, load]);
  return state;
}

/** The cast of the video open in the Story: the brand's characters and its workspace's. */
export function useVideoCast(load = true): ScopeState {
  const workspace = useProjectStore((s) => s.project?.workspaceId ?? null);
  return useCast({ workspace }, load);
}

function accept<T>(entry: T | null, what: string): T {
  if (!entry) throw new Error(`The studio returned ${what} the rig can't draw.`);
  return entry;
}

function rigFor(characterId: string) {
  for (const { cast } of Object.values(useCastStore.getState().scopes)) {
    const found = cast.characters.find((c) => c.id === characterId);
    if (found) return characterOf(found.rig);
  }
  return characterOf(undefined);
}

export async function createCharacter(body: NewCharacter): Promise<CharacterProfile> {
  const entry = accept(parseCharacter(await v3api.createCharacter(body)), "a character");
  await reloadAll();
  return entry;
}

export async function updateCharacter(id: string, patch: CharacterPatch): Promise<CharacterProfile> {
  const entry = accept(parseCharacter(await v3api.updateCharacter(id, patch)), "a character");
  await reloadAll();
  return entry;
}

export async function deleteCharacter(id: string): Promise<void> {
  await v3api.deleteCharacter(id);
  await reloadAll();
}

/** Reload every cast that has been loaded: a video's cast carries its workspace's name and brand. */
export const reloadCasts = (): Promise<void> => reloadAll();

/** Set a workspace's brand (the eyebrow over its videos' animated headlines) and redraw with it. */
export async function setWorkspaceBrand(workspace: string, patch: BrandPatch): Promise<Workspace> {
  const saved = await v3api.setWorkspaceBrand(workspace, patch);
  await reloadAll();
  return saved;
}

export async function setDefaultCharacter(workspace: string, characterId: string | null): Promise<void> {
  await v3api.setDefaultCharacter(workspace, characterId);
  await reloadAll();
}

export async function createExpression(characterId: string, name: string, spec: PartSpec, draft = false): Promise<CastExpression> {
  const entry = accept(parseExpression(await v3api.createExpression(characterId, name, spec, draft), rigFor(characterId)), "an expression");
  await reloadAll();
  return entry;
}

export async function updateExpression(characterId: string, id: string, patch: { name?: string; spec?: PartSpec; approved?: true }): Promise<CastExpression> {
  const entry = accept(parseExpression(await v3api.updateExpression(characterId, id, patch), rigFor(characterId)), "an expression");
  await reloadAll();
  return entry;
}

export async function deleteExpression(characterId: string, id: string): Promise<void> {
  await v3api.deleteExpression(characterId, id);
  await reloadAll();
}
