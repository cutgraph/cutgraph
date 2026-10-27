import { useEffect } from "react";
import { create } from "zustand";
import { onToast, type ToastAction } from "../toast";

// The message outlives `visible` so the toast can fade out with its text still in place.
const useToast = create<{ message: string; action: ToastAction | null; visible: boolean; seq: number }>(() => ({
  message: "",
  action: null,
  visible: false,
  seq: 0,
}));

export function toast(message: string, action?: ToastAction): void {
  useToast.setState((s) => ({ message, action: action ?? null, visible: true, seq: s.seq + 1 }));
}

const hide = () => useToast.setState({ visible: false });

export default function ToastHost() {
  const { message, action, visible, seq } = useToast();
  useEffect(() => onToast(toast), []);
  useEffect(() => {
    if (!seq) return;
    const h = window.setTimeout(hide, action ? 7000 : 3600);
    return () => window.clearTimeout(h);
  }, [seq, action]);
  return (
    <div className={`toast${visible ? " show" : ""}`} role="status" aria-live="polite">
      <span>{message}</span>
      {action && (
        <button
          type="button"
          onClick={() => {
            action.run();
            hide();
          }}
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
