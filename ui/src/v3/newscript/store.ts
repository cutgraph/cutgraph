// The write-a-script sheet. Mounted once in V3App's Shell, like the footage flow, so
// Home's "From an idea" and a workspace's "New video -> From a script" open the same sheet. Closing it
// keeps the draft (a stray click outside must not lose a script); a created video clears it.
// `openSheet(wsId, from, draft)` takes an optional draft: the hook for a co-pilot that writes one.
import { create } from "zustand";
import { errorText } from "../common";
import { liveDeps } from "../newvideo/runFlow";
import { navigate } from "../router";
import { scriptApi } from "./api";

export type Origin = "idea" | "script";

interface NewScriptStore {
  open: boolean;
  wsId: string;
  from: Origin;
  title: string;
  script: string;
  busy: boolean;
  error: string | null;
  /** "From an idea": the idea box's own text and its "✦ Draft it" state, independent of `busy`/`error`. */
  idea: string;
  drafting: boolean;
  draftError: string | null;
  /** True once the user types their own title, so a re-draft leaves it alone; a drafted title (not the user's) is fair game. */
  titleEdited: boolean;
  openSheet: (wsId: string, from: Origin, draft?: { title?: string; script?: string }) => void;
  setTitle: (title: string) => void;
  setScript: (script: string) => void;
  setIdea: (idea: string) => void;
  draftIt: () => Promise<void>;
  close: () => void;
  create: () => Promise<void>;
}

const EMPTY = {
  open: false, wsId: "", from: "script" as Origin, title: "", script: "", busy: false, error: null,
  idea: "", drafting: false, draftError: null, titleEdited: false,
};

export const useNewScript = create<NewScriptStore>((set, get) => ({
  ...EMPTY,

  openSheet: (wsId, from, draft) =>
    set({
      open: true, wsId, from, busy: false, error: null, idea: "", drafting: false, draftError: null, titleEdited: false,
      ...(draft && { title: draft.title ?? "", script: draft.script ?? "" }),
    }),

  setTitle: (title) => set({ title, titleEdited: true, error: null }),

  setScript: (script) => set({ script, error: null }),

  setIdea: (idea) => set({ idea, draftError: null }),

  draftIt: async () => {
    const { idea, drafting, title, titleEdited } = get();
    if (drafting || !idea.trim()) return;
    set({ drafting: true, draftError: null });
    try {
      const draft = await scriptApi.draft(idea.trim());
      set({ drafting: false, script: draft.script, title: titleEdited ? title : draft.title, titleEdited: false });
    } catch (e) {
      set({ drafting: false, draftError: errorText(e) });
    }
  },

  close: () => {
    if (!get().busy && !get().drafting) set({ open: false, error: null });
  },

  create: async () => {
    const { wsId, title, script, busy } = get();
    if (busy || !title.trim()) return;
    set({ busy: true, error: null });
    try {
      const workspaceId = wsId && wsId !== "unsorted" ? wsId : undefined;
      const { video } = await scriptApi.create({ title: title.trim(), script, workspaceId });
      liveDeps.setProxyWhenReady(video);
      navigate({ view: "story", ...(workspaceId && { ws: workspaceId }), video });
      set(EMPTY);
    } catch (e) {
      set({ busy: false, error: errorText(e) });
    }
  },
}));
