// What the drawer can add: the video's own workspace library plus the built-in brand kit, sorted into
// media tabs. Graphics come from the trusted registry rather than the library.

import { useEffect, useRef, useState } from "react";
import { create } from "zustand";
import { workspaceMediaRef } from "../../../preview/resolveMedia";
import { v3api, type Asset } from "../../api";
import type { StoryState } from "../storyStore";

/** A drawer tab of library media or graphics; the Character tab ("anim") holds the cameo instead. */
export type DrawerTab = Exclude<NonNullable<StoryState["drawerTab"]>, "anim">;

export const DRAWER_TABS: readonly [DrawerTab, string][] = [
  ["broll", "B-roll"],
  ["graphics", "Graphics"],
  ["music", "Music"],
  ["sfx", "Sounds"],
];

export const EMPTY_TAB: Record<DrawerTab, string> = { broll: "No B-roll", graphics: "No graphics", music: "No music", sfx: "No sounds" };

export interface LibraryAsset {
  workspaceId: string;
  workspaceName: string;
  asset: Asset;
}

// Audio without a "music"/"sfx" tag is sorted by length: a sound effect is a short sting.
export const SFX_MAX_S = 10;
const SFX_TAG = /^(sfx|sounds?|sound effects?)$/i;
const MUSIC_TAG = /^music$/i;

export function assetTab(asset: Asset): Exclude<DrawerTab, "graphics"> {
  if (asset.kind !== "audio") return "broll";
  if (asset.tags.some((t) => SFX_TAG.test(t))) return "sfx";
  if (asset.tags.some((t) => MUSIC_TAG.test(t))) return "music";
  return (asset.durationS ?? Infinity) < SFX_MAX_S ? "sfx" : "music";
}

export function assetsForTab(assets: LibraryAsset[], tab: DrawerTab): LibraryAsset[] {
  return tab === "graphics" ? [] : assets.filter((entry) => assetTab(entry.asset) === tab);
}

// Library clips are stored under their content-addressed file name; this maps each stored media ref
// back to the name the user imported, so cue chips read "garden-plate" rather than a hash.
export function assetLabels(assets: LibraryAsset[]): Map<string, string> {
  return new Map(assets.map((e) => [workspaceMediaRef(e.workspaceId, e.asset.file), e.asset.name]));
}

export function findAsset(assets: LibraryAsset[], workspaceId: string, assetId: string): LibraryAsset | null {
  return assets.find((e) => e.workspaceId === workspaceId && e.asset.id === assetId) ?? null;
}

export interface DrawerLibrary {
  assets: LibraryAsset[];
  loading: boolean;
  error: string | null;
  /** Re-fetch after a write elsewhere (e.g. a stock "Add to library") adds a new asset. */
  refresh: () => void;
}

const BRAND = "brand";

// Drawer.tsx and ScriptLens.tsx (cue-chip labels) each call useDrawerLibrary independently; without
// a shared signal, adding an asset from one leaves the other showing a stale list until its own next
// remount. A version bump (from any instance's `refresh`) makes every mounted instance re-fetch.
const useLibraryVersion = create<{ version: number }>(() => ({ version: 0 }));
const bumpLibraryVersion = (): void => useLibraryVersion.setState((s) => ({ version: s.version + 1 }));

// The last library each workspace fetched, so a reader mounted later (the Source lens) starts from it
// instead of showing file names until its own fetch lands.
const lastFetched = new Map<string, LibraryAsset[]>();

function cachedLibrary(workspaceId: string | null | undefined): Omit<DrawerLibrary, "refresh"> {
  const assets = lastFetched.get(workspaceId ?? "");
  return assets ? { assets, loading: false, error: null } : { assets: [], loading: true, error: null };
}

export function useDrawerLibrary(workspaceId: string | null | undefined): DrawerLibrary {
  const version = useLibraryVersion((s) => s.version);
  const [state, setState] = useState<Omit<DrawerLibrary, "refresh">>(() => cachedLibrary(workspaceId));
  const prevWorkspace = useRef(workspaceId);
  useEffect(() => {
    let cancelled = false;
    if (prevWorkspace.current !== workspaceId) {
      prevWorkspace.current = workspaceId;
      setState(cachedLibrary(workspaceId));
    }
    const ids = [...new Set([workspaceId, BRAND].filter((id): id is string => !!id && id !== "unsorted"))];
    Promise.allSettled(ids.map((id) => v3api.workspace(id))).then((results) => {
      if (cancelled) return;
      const assets = results.flatMap((r) => r.status === "fulfilled"
        ? r.value.assets.map((asset) => ({ workspaceId: r.value.workspace.id, workspaceName: r.value.workspace.name, asset }))
        : []);
      const failed = results.find((r): r is PromiseRejectedResult => r.status === "rejected");
      if (!failed) lastFetched.set(workspaceId ?? "", assets);
      setState({ assets, loading: false, error: failed && !assets.length ? String(failed.reason?.message ?? failed.reason) : null });
    });
    return () => { cancelled = true; };
  }, [workspaceId, version]);
  return { ...state, refresh: bumpLibraryVersion };
}
