import { useMemo } from "react";
import type { TranscriptDoc, TranscriptLifecycle, TranscriptWord } from "../../api";
import { useProjectStore } from "../../store/useProjectStore";
import { useTranscript } from "../../transcript/useTranscript";
import type { Project } from "../../types/project";
import { cutWordSet, sceneSpans, speechMedia, type SceneSpan } from "./model";

export interface StoryModel {
  project: Project | null;
  doc: TranscriptDoc | null;
  lifecycle: TranscriptLifecycle | null;
  words: TranscriptWord[];
  media: string | null;
  cut: Set<number>;
  spans: SceneSpan[];
  loading: boolean;
  error: string | null;
}

const NO_WORDS: TranscriptWord[] = [];

// One derivation per render of the open video; the transcript fetch is deduplicated by useTranscript.
export function useStoryModel(): StoryModel {
  const project = useProjectStore((s) => s.project);
  const { doc, lifecycle, loading, error } = useTranscript(project?.name ?? null);
  const words = doc?.words ?? NO_WORDS;
  const media = project ? speechMedia(project) : null;
  const cut = useMemo(() => (project ? cutWordSet(project, media, words) : new Set<number>()), [project, media, words]);
  const spans = useMemo(() => (project ? sceneSpans(project, media, words) : []), [project, media, words]);
  return { project, doc, lifecycle, words, media, cut, spans, loading, error };
}
