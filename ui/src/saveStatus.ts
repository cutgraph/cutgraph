import { create } from "zustand";

export type SaveState = "unsaved" | "saving" | "saved" | "conflict" | "failed";

export interface SaveStatus {
  state: SaveState;
  detail: string | null;
}

interface SaveStatusStore extends SaveStatus {
  markDirty: () => void;
  start: () => number;
  saved: (token: number) => void;
  conflict: (token: number) => void;
  failed: (token: number, detail: string) => void;
  markSaved: () => void;
}

let dirtyVersion = 0;
let nextToken = 0;
let latestToken = 0;
const saveVersions = new Map<number, number>();

// Persistence happens outside React, while the topbar still needs an honest, subscribable description
// of it. Tokens stop a late save response from overwriting a newer "Unsaved changes" state.
export const useSaveStatus = create<SaveStatusStore>((set) => ({
  state: "saved",
  detail: null,
  markDirty: () => {
    dirtyVersion += 1;
    set({ state: "unsaved", detail: null });
  },
  start: () => {
    const token = ++nextToken;
    latestToken = token;
    saveVersions.set(token, dirtyVersion);
    set({ state: "saving", detail: null });
    return token;
  },
  saved: (token) => {
    const version = saveVersions.get(token);
    saveVersions.delete(token);
    if (token !== latestToken) return;
    set(version === dirtyVersion ? { state: "saved", detail: null } : { state: "unsaved", detail: null });
  },
  conflict: (token) => {
    saveVersions.delete(token);
    if (token !== latestToken) return;
    set({ state: "conflict", detail: "Someone else saved a newer version. Your local edits are still here." });
  },
  failed: (token, detail) => {
    saveVersions.delete(token);
    if (token !== latestToken) return;
    set({ state: "failed", detail });
  },
  markSaved: () => set({ state: "saved", detail: null }),
}));

export function resetSaveStatusForTest(): void {
  dirtyVersion = 0;
  nextToken = 0;
  latestToken = 0;
  saveVersions.clear();
  useSaveStatus.setState({ state: "saved", detail: null });
}
