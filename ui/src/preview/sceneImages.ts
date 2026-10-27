// Decoded images for motion-core in the browser (MotionEnv.image): a `screen` shot's library
// images, one per phone, each loaded once per URL. motion-core draws a phone's screen dark until
// its image is ready; the version bumps as each one arrives so a still canvas (a paused player, a
// thumbnail) repaints.
import { useSyncExternalStore } from "react";

const images = new Map<string, HTMLImageElement>();
const listeners = new Set<() => void>();
let version = 0;

function loaded(): void {
  version++;
  listeners.forEach((l) => l());
}

/** The decoded image at `src`, or null while it loads (starting the load on first ask). */
export function sceneImage(src: string): HTMLImageElement | null {
  let img = images.get(src);
  if (!img) {
    img = new Image();
    img.addEventListener("load", loaded);
    img.src = src;
    images.set(src, img);
  }
  return img.complete && img.naturalWidth > 0 ? img : null;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Changes whenever another scene image finishes loading: a dependency for canvases that paint once. */
export const useSceneImagesVersion = (): number => useSyncExternalStore(subscribe, () => version);
