// Dropping drawer tiles onto transcript words. The Script lens owns the words; the drawer only relies
// on the contract that every rendered word carries `data-word-idx={flatIdx}` inside #story-pane, so
// these listeners work whichever lens renders them.

import { useEffect, useRef } from "react";
import { graphicDefinition, type GraphicComponentId } from "../../../graphics/registry";

export const DRAWER_MIME = "application/x-cutgraph-drawer";
export const DROP_CLASS = "word-drop";

/** A cue a tile adds: a library asset or a graphic. */
export type CuePayload =
  | { type: "asset"; workspaceId: string; assetId: string }
  | { type: "graphic"; componentId: GraphicComponentId };

/** What a drawer tile carries: a cue, or the Character tab's cameo. */
export type DrawerPayload = CuePayload | { type: "cameo" };

export function encodePayload(payload: DrawerPayload): string {
  return JSON.stringify(payload);
}

export function decodePayload(raw: string): DrawerPayload | null {
  try {
    const value = JSON.parse(raw) as Record<string, unknown>;
    if (value?.type === "asset" && typeof value.workspaceId === "string" && typeof value.assetId === "string") {
      return { type: "asset", workspaceId: value.workspaceId, assetId: value.assetId };
    }
    if (value?.type === "graphic" && typeof value.componentId === "string" && graphicDefinition(value.componentId)) {
      return { type: "graphic", componentId: value.componentId as GraphicComponentId };
    }
    if (value?.type === "cameo") return { type: "cameo" };
  } catch {
    // not ours
  }
  return null;
}

export function wordElement(target: EventTarget | null): HTMLElement | null {
  return target instanceof Element ? target.closest<HTMLElement>("[data-word-idx]") : null;
}

export function dropWordIdx(target: EventTarget | null): number | null {
  const raw = wordElement(target)?.dataset.wordIdx;
  const idx = raw === undefined || raw === "" ? NaN : Number(raw);
  return Number.isInteger(idx) && idx >= 0 ? idx : null;
}

function carriesTile(event: DragEvent): boolean {
  return !!event.dataTransfer && Array.from(event.dataTransfer.types).includes(DRAWER_MIME);
}

// Installed once per Story view; `onDrop` may change every render.
export function useWordDrop(onDrop: (payload: DrawerPayload, wordIdx: number) => void): void {
  const latest = useRef(onDrop);
  latest.current = onDrop;
  useEffect(() => {
    const pane = document.getElementById("story-pane");
    if (!pane) return;
    let lit: HTMLElement | null = null;
    const light = (el: HTMLElement | null) => {
      if (lit === el) return;
      lit?.classList.remove(DROP_CLASS);
      lit = el;
      el?.classList.add(DROP_CLASS);
    };
    const over = (event: DragEvent) => {
      if (!carriesTile(event)) return;
      const word = dropWordIdx(event.target) === null ? null : wordElement(event.target);
      light(word);
      if (word) {
        event.preventDefault();
        event.dataTransfer!.dropEffect = "copy";
      }
    };
    const leave = (event: DragEvent) => {
      if (lit && !(event.relatedTarget instanceof Node && lit.contains(event.relatedTarget))) light(null);
    };
    const drop = (event: DragEvent) => {
      light(null);
      if (!carriesTile(event)) return;
      const wordIdx = dropWordIdx(event.target);
      const payload = decodePayload(event.dataTransfer!.getData(DRAWER_MIME));
      if (wordIdx === null || !payload) return;
      event.preventDefault();
      latest.current(payload, wordIdx);
    };
    const clear = () => light(null);
    pane.addEventListener("dragover", over);
    pane.addEventListener("dragleave", leave);
    pane.addEventListener("drop", drop);
    window.addEventListener("dragend", clear);
    return () => {
      pane.removeEventListener("dragover", over);
      pane.removeEventListener("dragleave", leave);
      pane.removeEventListener("drop", drop);
      window.removeEventListener("dragend", clear);
      clear();
    };
  }, []);
}
