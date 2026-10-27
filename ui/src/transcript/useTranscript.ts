// Shared transcript lifecycle per project.  The durable server lifecycle is
// authoritative; this module only deduplicates the actual transcript document
// fetch for Timeline and the transcript lens.

import { useCallback, useEffect, useState } from "react";
import { api, type TranscriptDoc, type TranscriptLifecycle } from "../api";

const cache = new Map<string, TranscriptDoc>();
const inflight = new Map<string, Promise<TranscriptDoc>>();
const lifecycleCache = new Map<string, TranscriptLifecycle>();
const lifecycleInflight = new Map<string, Promise<TranscriptLifecycle>>();
const EVENT = "cutgraph:transcript-reconcile";

function load(name: string): Promise<TranscriptDoc> {
  const cached = cache.get(name);
  if (cached) return Promise.resolve(cached);
  let pending = inflight.get(name);
  if (!pending) {
    pending = api.getTranscript(name).then((doc) => {
      cache.set(name, doc);
      inflight.delete(name);
      return doc;
    }).catch((error) => {
      inflight.delete(name);
      throw error;
    });
    inflight.set(name, pending);
  }
  return pending;
}

function loadLifecycle(name: string): Promise<TranscriptLifecycle> {
  const cached = lifecycleCache.get(name);
  if (cached) return Promise.resolve(cached);
  let pending = lifecycleInflight.get(name);
  if (!pending) {
    pending = api.transcription(name).then((lifecycle) => {
      lifecycleCache.set(name, lifecycle);
      lifecycleInflight.delete(name);
      return lifecycle;
    }).catch((error) => {
      lifecycleInflight.delete(name);
      throw error;
    });
    lifecycleInflight.set(name, pending);
  }
  return pending;
}

// Called by the SSE path after a job transition and on reconnect.  Deleting
// the old document prevents a completed/retried job from leaving stale words
// visible while every mounted consumer independently remains deduplicated.
export function reconcileTranscript(name: string): void {
  cache.delete(name);
  lifecycleCache.delete(name);
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent<string>(EVENT, { detail: name }));
  }
}

export interface TranscriptResource {
  doc: TranscriptDoc | null;
  lifecycle: TranscriptLifecycle | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useTranscript(projectName: string | null): TranscriptResource {
  const [doc, setDoc] = useState<TranscriptDoc | null>(null);
  const [lifecycle, setLifecycle] = useState<TranscriptLifecycle | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [generation, setGeneration] = useState(0);

  const refresh = useCallback(() => {
    if (projectName) {
      cache.delete(projectName);
      lifecycleCache.delete(projectName);
    }
    setGeneration((value) => value + 1);
  }, [projectName]);

  useEffect(() => {
    if (!projectName) {
      setDoc(null);
      setLifecycle(null);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setDoc(null); // project changes must never display the prior project's words.
    setLoading(true);
    setError(null);
    loadLifecycle(projectName).then(async (next) => {
      if (cancelled) return;
      setLifecycle(next);
      if (next.state !== "ready") {
        setLoading(false);
        return;
      }
      try {
        const nextDoc = await load(projectName);
        if (!cancelled) setDoc(nextDoc);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "Could not load transcript.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }).catch((cause) => {
      if (!cancelled) {
        setLifecycle(null);
        setError(cause instanceof Error ? cause.message : "Could not reach the transcript service.");
        setLoading(false);
      }
    });
    return () => { cancelled = true; };
  }, [projectName, generation]);

  useEffect(() => {
    const onReconcile = (event: Event) => {
      if ((event as CustomEvent<string>).detail === projectName) refresh();
    };
    window.addEventListener(EVENT, onReconcile);
    return () => window.removeEventListener(EVENT, onReconcile);
  }, [projectName, refresh]);

  return { doc, lifecycle, loading, error, refresh };
}
