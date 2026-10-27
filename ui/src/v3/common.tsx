import { useCallback, useEffect, useState, type RefObject } from "react";
import { ApiError, apiErrorText } from "./api";

export type Load<T> =
  | { state: "loading" }
  | { state: "ready"; data: T }
  | { state: "error"; error: ApiError };

const asApiError = (err: unknown) =>
  err instanceof ApiError ? err : new ApiError(0, err instanceof Error ? err.message : String(err));

// Loads once per key; `update` edits the loaded data in place after a successful write.
export function useLoad<T>(load: () => Promise<T>, key: string) {
  const [state, setState] = useState<Load<T>>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setState({ state: "loading" });
    load().then(
      (data) => live && setState({ state: "ready", data }),
      (err) => live && setState({ state: "error", error: asApiError(err) }),
    );
    return () => {
      live = false;
    };
    // `load` is a fresh closure every render; `key` names what it loads.
  }, [key, attempt]);
  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  const update = useCallback(
    (fn: (data: T) => T) => setState((s) => (s.state === "ready" ? { state: "ready", data: fn(s.data) } : s)),
    [],
  );
  return { load: state, reload, update };
}

/**
 * Close an open menu on a pointer down outside `wrap`, or on Escape, which hands focus back to its
 * trigger (the `[aria-haspopup]` inside `wrap`).
 */
export function useDismiss(open: boolean, setOpen: (open: boolean) => void, wrap: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent) {
        if (e.key !== "Escape") return;
        setOpen(false);
        wrap.current?.querySelector<HTMLElement>("[aria-haspopup]")?.focus();
      } else if (!wrap.current?.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open, setOpen, wrap]);
}

export function errorText(err: unknown): string {
  return apiErrorText(err);
}

export function ErrorState({ error, what, onRetry }: { error: ApiError; what: string; onRetry?: () => void }) {
  const headline =
    error.status === 0
      ? "Can't reach the studio."
      : error.status === 404
        ? `${what} aren't available on this studio yet.`
        : `Couldn't load ${what.toLowerCase()}.`;
  return (
    <div className="empty notice" role="alert">
      <p>
        <b>{headline}</b>
        <small>{error.message}</small>
      </p>
      {onRetry && (
        <button className="ghost" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

const AGO: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 31_536_000],
  ["month", 2_592_000],
  ["week", 604_800],
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
];

export function ago(iso: string, now = Date.now()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const secs = Math.round((t - now) / 1000);
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  for (const [unit, size] of AGO) {
    if (Math.abs(secs) >= size) return rtf.format(Math.round(secs / size), unit);
  }
  return "just now";
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}
