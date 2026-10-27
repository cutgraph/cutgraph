// Store for the new-video-from-footage flow. Mounted once at the Shell level (see
// NewVideoFlow.tsx) so opening it from Project's "New video" menu or Home's "From footage" card
// is the same overlay, and it keeps running if the person switches spaces while it's in flight.
import { create } from "zustand";
import { api } from "../../api";
import { navigate } from "../router";
import { IDLE_FLOW, transition, type FlowEvent, type FlowState } from "./flow";
import { liveDeps, retryFlow, runNewVideoFlow, type FlowMeta, type SourceTarget } from "./runFlow";

const STORAGE_KEY = "cutgraph:newvideo";

interface Persisted {
  wsId: string;
  project: string;
  title: string;
  fileName: string;
}

function loadPersisted(): Persisted | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Persisted) : null;
  } catch {
    return null;
  }
}

function savePersisted(p: Persisted | null): void {
  try {
    if (p) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(p));
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // best-effort only — private browsing / storage quota must not break the flow
  }
}

interface NewVideoStore {
  open: boolean;
  wsId: string;
  source: SourceTarget | null;
  flow: FlowState;
  meta: FlowMeta;
  reconciled: boolean;
  openPicker: (wsId: string) => void;
  pickSource: (source: SourceTarget, defaultTitle: string) => void;
  setTitle: (title: string) => void;
  confirm: () => void;
  retry: () => void;
  close: () => void;
  reconcile: () => void;
}

function storeHandle() {
  return {
    getFlow: () => useNewVideoFlow.getState().flow,
    // Commits the transition first (a plain object set, not a functional updater), then runs
    // side effects against the now-current state — never nests a setState call inside another
    // one's updater, which would notify listeners with a not-yet-fully-applied intermediate state.
    dispatch: (event: FlowEvent) => {
      const before = useNewVideoFlow.getState();
      const flow = transition(before.flow, event);
      useNewVideoFlow.setState({ flow });
      afterTransition(before, flow);
    },
    getMeta: () => useNewVideoFlow.getState().meta,
    setMeta: (patch: Partial<FlowMeta>) => useNewVideoFlow.setState((s) => ({ meta: { ...s.meta, ...patch } })),
  };
}

function afterTransition(before: NewVideoStore, flow: FlowState): void {
  if (flow.phase === "ready" && before.flow.phase !== "ready") {
    savePersisted(null);
    navigate({ view: "story", ws: flow.ws, video: flow.video });
    // The overlay itself closes once Story has taken over; a lingering "ready" card would just
    // sit behind the new view.
    setTimeout(() => useNewVideoFlow.setState({ open: false, flow: IDLE_FLOW, meta: {}, source: null }), 300);
  } else if (flow.phase === "creating" || flow.phase === "transcribing" || flow.phase === "scenes") {
    const meta = useNewVideoFlow.getState().meta;
    if (meta.project) savePersisted({ wsId: flow.wsId, project: meta.project, title: flow.title, fileName: flow.fileName });
  }
}

export const useNewVideoFlow = create<NewVideoStore>((set, get) => ({
  open: false,
  wsId: "",
  source: null,
  flow: IDLE_FLOW,
  meta: {},
  reconciled: false,

  openPicker: (wsId) => set({ open: true, wsId, source: null, flow: { ...IDLE_FLOW, wsId }, meta: {} }),

  pickSource: (source, defaultTitle) => set({ source, flow: { ...get().flow, title: defaultTitle } }),

  setTitle: (title) => set((s) => ({ flow: { ...s.flow, title } })),

  confirm: () => {
    const { wsId, source, flow } = get();
    if (!source) return;
    void runNewVideoFlow(storeHandle(), { wsId, title: flow.title, source }, liveDeps);
  },

  retry: () => {
    void retryFlow(storeHandle(), get().source, liveDeps);
  },

  close: () => {
    const phase = get().flow.phase;
    if (phase === "uploading" || phase === "creating" || phase === "transcribing" || phase === "scenes") return;
    savePersisted(null);
    set({ open: false, source: null, flow: IDLE_FLOW, meta: {} });
  },

  // Re-derives the card from the server rather than trusting in-memory state that may predate a
  // full page reload (the flow persists across an SPA navigation on its own, since this store
  // lives at module scope — this path only matters after the JS runtime itself restarted).
  reconcile: () => {
    if (get().reconciled) return;
    set({ reconciled: true });
    const persisted = loadPersisted();
    if (!persisted) return;
    void reconcileFromServer(persisted);
  },
}));

async function reconcileFromServer(p: Persisted): Promise<void> {
  let sceneCount: number;
  try {
    const { project } = await api.getProject(p.project);
    sceneCount = Array.isArray(project.scenes) ? project.scenes.length : 0;
  } catch {
    savePersisted(null);
    return; // the project no longer exists (or the server is unreachable) — nothing to resume
  }
  const flow: FlowState = { ...IDLE_FLOW, wsId: p.wsId, title: p.title, fileName: p.fileName, phase: "creating" };
  useNewVideoFlow.setState({ open: true, wsId: p.wsId, source: null, flow, meta: { project: p.project } });
  const handle = storeHandle();
  handle.dispatch({ type: "CREATED" }); // routes through afterTransition like any other step
  if (sceneCount > 0) {
    handle.dispatch({ type: "TRANSCRIBED" });
    handle.dispatch({ type: "READY", ws: p.wsId, video: p.project, sceneCount });
    return;
  }
  await runFromTranscribing(p.project);
}

async function runFromTranscribing(project: string): Promise<void> {
  const handle = storeHandle();
  try {
    await liveDeps.waitForTranscription(project, (message) => handle.dispatch({ type: "TRANSCRIBING", message }));
    handle.dispatch({ type: "TRANSCRIBED" });
    const result = await liveDeps.applyScenes(project);
    handle.dispatch({ type: "READY", ws: handle.getFlow().wsId, video: project, sceneCount: result.sceneCount });
  } catch (e) {
    handle.dispatch({ type: "FAILED", step: "transcribe", message: e instanceof Error ? e.message : String(e) });
  }
}
