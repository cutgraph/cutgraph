// A `screen` shot's library images, the preview's half of pipeline/staging.py: each phone
// (`screen.asset`, then each of `screen.more`) names a library asset id, which resolves against what the
// video can see, its own workspace's library and then the brand kit's (image assets only), to the URL the
// player decodes. The export resolves the same ids to the same files for the Node renderer;
// tests/fixtures/scene_specs_parity_staging.json holds the two together. The libraries are fetched once
// per video workspace, and only while a scene needs them.
import { useEffect } from "react";
import { create } from "zustand";
import type { Shot as MotionShot, PhoneImage } from "motion-core";
import { resolveMedia, workspaceMediaRef } from "../../../preview/resolveMedia";
import type { Project } from "../../../types/project";
import { v3api } from "../../api";
import type { LibraryAsset } from "../drawer/library";

const BRAND = "brand";
const NONE: readonly LibraryAsset[] = Object.freeze([]);

/** The libraries a video can see, in the order a screen's asset is looked up in: its workspace's, then the brand kit's. */
export const librariesOf = (workspaceId: string | null | undefined): string[] =>
  [...new Set([workspaceId, BRAND].filter((id): id is string => !!id && id !== "unsorted"))];

/** Where an image imported for a screen goes: the video's workspace library, or the brand kit's for an unsorted video. */
export const importTarget = (workspaceId: string | null | undefined): string => librariesOf(workspaceId)[0];

/**
 * The shot with each of its screen's phones resolved to an image URL: the first image asset with the
 * phone's id; a phone with none draws the generated app screen.
 */
export function withScreenImage(shot: MotionShot, assets: readonly LibraryAsset[]): MotionShot {
  const screen = shot.screen;
  if (!screen?.asset) return shot;
  const resolve = <P extends PhoneImage>(phone: P): P => {
    const hit = assets.find((e) => e.asset.id === phone.asset && e.asset.kind === "image");
    const image = hit ? resolveMedia({ source: workspaceMediaRef(hit.workspaceId, hit.asset.file) }) : null;
    return image ? { ...phone, image } : phone;
  };
  return { ...shot, screen: { ...resolve(screen), ...(screen.more && { more: screen.more.map(resolve) }) } };
}

interface Libraries {
  byKey: Readonly<Record<string, readonly LibraryAsset[]>>;
  version: number;
}

const useLibraries = create<Libraries>(() => ({ byKey: {}, version: 0 }));
const loading = new Map<string, number>();

function load(key: string, version: number): void {
  if (loading.get(key) === version) return;
  loading.set(key, version);
  void Promise.allSettled(key.split("+").map((id) => v3api.workspace(id))).then((results) => {
    const assets = results.flatMap((r) => r.status === "fulfilled"
      ? r.value.assets.map((asset) => ({ workspaceId: r.value.workspace.id, workspaceName: r.value.workspace.name, asset }))
      : []);
    useLibraries.setState((s) => ({ byKey: { ...s.byKey, [key]: assets } }));
  });
}

/** The assets of the libraries a video can see, in look-up order; none until loaded, or while `enabled` is false. */
export function useLibraryAssets(workspaceId: string | null | undefined, enabled = true): readonly LibraryAsset[] {
  const key = enabled ? librariesOf(workspaceId).join("+") : null;
  const version = useLibraries((s) => s.version);
  const assets = useLibraries((s) => (key ? s.byKey[key] : undefined));
  useEffect(() => {
    if (key) load(key, version);
  }, [key, version]);
  return assets ?? NONE;
}

/** What a video's screen shots can show: its libraries, fetched only once one of its scenes names an image. */
export const useScreenAssets = (project: Project | null | undefined): readonly LibraryAsset[] =>
  useLibraryAssets(project?.workspaceId, !!project?.scenes?.some((s) => s.shot?.screen?.asset));

/** Read the libraries again, after an import lands a new image. */
export const refreshLibraries = (): void => useLibraries.setState((s) => ({ version: s.version + 1 }));

/** Forget every loaded library (tests). */
export function resetLibraries(): void {
  loading.clear();
  useLibraries.setState({ byKey: {}, version: 0 });
}
