import { create } from "zustand";

export interface ToastAction {
  label: string;
  run: () => void;
}

interface ToastState {
  message: string | null;
  action: ToastAction | null;
  seq: number;
  show: (message: string, action?: ToastAction) => void;
  dismiss: () => void;
}

// A tiny imperative bus so the store, SSE client, and save path can raise a toast
// without threading a callback through React. Toast() subscribes; toast() publishes.
export const useToastStore = create<ToastState>((set) => ({
  message: null,
  action: null,
  seq: 0,
  show: (message, action) => set((s) => ({ message, action: action ?? null, seq: s.seq + 1 })),
  dismiss: () => set({ message: null, action: null }),
}));

export function toast(message: string, action?: ToastAction): void {
  useToastStore.getState().show(message, action);
}

// The v3 shell renders its own toast; it listens here so toasts raised by the shared store, SSE and
// save paths reach it too. `seq` makes a repeated identical message fire again.
export function onToast(listener: (message: string, action?: ToastAction) => void): () => void {
  return useToastStore.subscribe((s, prev) => {
    if (s.seq !== prev.seq && s.message) listener(s.message, s.action ?? undefined);
  });
}

