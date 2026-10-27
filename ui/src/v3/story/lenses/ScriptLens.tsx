import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import type { TranscriptFiller, TranscriptLifecycle, TranscriptWord } from "../../../api";
import { resolveMedia } from "../../../preview/resolveMedia";
import { useSourceProxy } from "../../../preview/sourceProxy";
import { setPlayhead, useProjectStore, type RippleWarning } from "../../../store/useProjectStore";
import { acceptSourceCuts, restoreSourceRanges } from "../../../transcript/acceptCuts";
import { fillerBatch, resolveTimed, srcToTimeline, timedWords, timelineToSrc, wordSrcFrame } from "../../../transcript/cutmap";
import type { Project } from "../../../types/project";
import { commitEdit, editToast } from "../history";
import { isEditingText } from "../StoryView";
import { orderedRange, selectCue, setCaret, setRange, useStory, type WordRange } from "../storyStore";
import { useStoryModel } from "../useStoryModel";
import { assetLabels, useDrawerLibrary } from "../drawer/library";
import { cueTarget, placeCue } from "../drawer/place";
import SceneSection from "../script/SceneSection";
import { sceneRecipe } from "../model";
import { animatedSceneSpecs } from "../shot/specs";
import { useScreenAssets } from "../shot/screenLibrary";
import { useVideoCast } from "../../library/castStore";
import SelectionBar from "../script/SelectionBar";
import { cameoPick, type CameoPick } from "../script/cameo";
import {
  cuesByWord,
  fillersByWord,
  rangeAllCut,
  restoreRanges,
  scriptSections,
  selectionCuts,
  type FillerToken,
  type Section,
  type WordCue,
} from "../script/script";
import { usePlayheadWord } from "../script/usePlayheadWord";
import "../script/script.css";
import { AddonSettingsLink } from "../../Addons";

const NO_FILLERS: TranscriptFiller[] = [];
const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? "" : "s"}`;

function renameScene(sceneId: string, title: string): void {
  const { project } = useProjectStore.getState();
  if (!project?.scenes?.some((s) => s.id === sceneId)) return;
  const scenes = project.scenes.map((s) => (s.id === sceneId ? { ...s, title } : s));
  commitEdit({ ...project, scenes }, "Renamed scene");
}

function warningText(warnings: RippleWarning[]): string {
  const orphans = warnings.filter((w) => w.type === "orphaned-anchor").length;
  if (orphans) return `${plural(orphans, "cue")} lost ${orphans === 1 ? "its" : "their"} word`;
  return warnings.length ? `${plural(warnings.length, "cue")} to check on the timeline` : "";
}

// A ripple verb can succeed without editing (the words were already gone); only a real edit is
// announced with an Undo, which would otherwise step back over an unrelated edit.
async function rippleEdit(run: () => Promise<boolean>, message: () => string): Promise<void> {
  const before = useProjectStore.getState().project;
  if ((await run()) && useProjectStore.getState().project !== before) editToast(message());
}

function sectionOf(sections: Section[], idx: number): Section | null {
  return sections.find((s) => idx >= s.startWord && idx < s.endWord) ?? null;
}

function phraseSeconds(words: TranscriptWord[], r: WordRange): number {
  const s = words[r.a].start;
  const e = words[r.b].end;
  return Number.isFinite(s) && Number.isFinite(e) ? Math.max(0, e - s) : 0;
}

function speechTotalF(project: Project): number {
  return (project.tracks.find((t) => t.kind === "speech")?.clips ?? []).reduce((n, c) => n + (c.srcOutF - c.srcInF), 0);
}

// the script as scenes of words. Click a word to seek, drag across words to cut, restore or
// cue them, and accept Claude's filler-word suggestion. Every edit is one undo step through the
// ripple verbs the transcript panel uses.
export default function ScriptLens() {
  const { project, doc, lifecycle, words, media, cut, spans, loading, error } = useStoryModel();
  const range = useStory((s) => s.range);
  const caretWord = useStory((s) => s.caretWord);
  const cueId = useStory((s) => s.cueId);
  const proposalWords = useStory((s) => s.proposalWords);
  const rootRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ a: number; lo: number; hi: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [fillersDismissed, setFillersDismissed] = useState(false);

  usePlayheadWord(rootRef, project, media, words, cut);

  const fillers = doc?.fillers ?? NO_FILLERS;
  const sections = useMemo(() => scriptSections(spans, words.length), [spans, words.length]);
  const { assets, loading: libraryLoading } = useDrawerLibrary(project?.workspaceId);
  const labels = useMemo(() => assetLabels(assets), [assets]);
  const cues = useMemo(
    () => (project ? cuesByWord(project, media, labels) : new Map<number, WordCue[]>()),
    [project, media, labels],
  );
  const fillerTokens = useMemo(
    () => (project ? fillersByWord(project, media, words, fillers) : new Map<number, FillerToken[]>()),
    [project, media, words, fillers],
  );
  const suggestion = useMemo(() => {
    let count = 0;
    const scenes = new Set<string>();
    for (const [at, tokens] of fillerTokens) {
      const pending = tokens.filter((t) => t.state === "pending").length;
      if (!pending) continue;
      count += pending;
      scenes.add((sectionOf(sections, at) ?? sections[sections.length - 1])?.key ?? "");
    }
    return { count, scenes: scenes.size };
  }, [fillerTokens, sections]);
  const { cast } = useVideoCast(false);
  const screens = useScreenAssets(project);
  const pictures = useMemo(
    () => new Map((project ? animatedSceneSpecs(project, words, spans, media, cast, screens) : []).map((spec) => [spec.sceneId, spec])),
    [project, words, spans, media, cast, screens],
  );
  const frameW = project?.width ?? 1920;
  const frameH = project?.height ?? 1080;
  const frame = useMemo(() => ({ width: frameW, height: frameH }), [frameW, frameH]);
  const proxiesLanded = useSourceProxy(project);
  const thumbUrl = useMemo(() => {
    const clip = project?.tracks.find((t) => t.kind === "speech")?.clips?.[0];
    return clip ? resolveMedia(clip.media) : null;
  }, [project, proxiesLanded]);

  const sel = range ? orderedRange(range) : null;

  const seekWord = useCallback((idx: number) => {
    const p = useProjectStore.getState().project;
    const t = resolveTimed(words)[idx];
    if (p && media && t >= 0) {
      const tl = srcToTimeline(p, media, wordSrcFrame(words[t], p.fps));
      if (tl !== null) setPlayhead(tl);
    }
    setCaret(idx);
  }, [words, media]);

  const cutWords = useCallback(async (r: WordRange) => {
    const p = useProjectStore.getState().project;
    setRange(null);
    if (!p || !media) return;
    const cuts = selectionCuts(p, media, words, r.a, r.b);
    if (!cuts.length) return;
    let warn = "";
    await rippleEdit(
      () => acceptSourceCuts(p.name, cuts, p.fps, (w) => { warn = warningText(w); }, setBusy),
      () => [`Cut ${plural(r.b - r.a + 1, "word")}`, warn].filter(Boolean).join(" · "),
    );
  }, [media, words]);

  const restoreWords = useCallback(async (r: WordRange) => {
    const p = useProjectStore.getState().project;
    setRange(null);
    if (!p || !media) return;
    const label = `Restored ${plural(r.b - r.a + 1, "word")}`;
    let warn = "";
    await rippleEdit(
      () => restoreSourceRanges(p.name, restoreRanges(p, media, words, r.a, r.b), label, (w) => { warn = warningText(w); }, setBusy),
      () => [label, warn].filter(Boolean).join(" · "),
    );
  }, [media, words]);

  const applyFillers = async (): Promise<void> => {
    const p = useProjectStore.getState().project;
    if (!p || !media) return;
    const { cuts } = fillerBatch(fillers, p, media, timedWords(words), p.fps);
    if (!cuts.length) return;
    await rippleEdit(() => acceptSourceCuts(p.name, cuts, p.fps, undefined, setBusy), () => `Cut ${plural(cuts.length, "filler word")}`);
  };

  // Drag ends wherever the pointer is released: a press that never left its word is a click (seek).
  useEffect(() => {
    const onUp = (): void => {
      if (!drag.current) return;
      drag.current = null;
      setDragging(false);
      const r = useStory.getState().range;
      if (r && r.a === r.b) seekWord(r.a);
    };
    window.addEventListener("mouseup", onUp);
    return () => window.removeEventListener("mouseup", onUp);
  }, [seekWord]);

  // Delete/Backspace cuts the selection, or the caret word when nothing is selected, and restores
  // them when they are already cut (the selection bar's Cut/Restore). Not while a sheet covers the script.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent): void => {
      const { range: r, caretWord: caret, shotSceneId, exportOpen } = useStory.getState();
      if (isEditingText(e.target) || shotSceneId || exportOpen) return;
      if (e.key === "Escape") {
        if (r) setRange(null);
      } else if (e.key === "Delete" || e.key === "Backspace") {
        const target = r ? orderedRange(r) : caret !== null ? { a: caret, b: caret } : null;
        if (!target) return;
        e.preventDefault();
        void (rangeAllCut(cut, target.a, target.b) ? restoreWords(target) : cutWords(target));
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [cut, cutWords, restoreWords]);

  const wordAt = (target: EventTarget): number | null => {
    const el = (target as Element).closest?.("[data-word-idx]");
    return el ? Number((el as HTMLElement).dataset.wordIdx) : null;
  };

  const onMouseDown = (e: MouseEvent<HTMLDivElement>): void => {
    if (e.button !== 0) return;
    const idx = wordAt(e.target);
    if (idx === null) {
      if (!(e.target as Element).closest(".selpop, .cue")) setRange(null);
      return;
    }
    e.preventDefault();
    (document.activeElement as HTMLElement | null)?.blur?.();
    const section = sectionOf(sections, idx);
    if (!section) return;
    const anchor = range ? range.a : caretWord;
    if (e.shiftKey && anchor !== null && anchor >= section.startWord && anchor < section.endWord) {
      setRange({ a: anchor, b: idx });
      return;
    }
    drag.current = { a: idx, lo: section.startWord, hi: section.endWord };
    setDragging(true);
    setRange({ a: idx, b: idx });
  };

  const onMouseOver = (e: MouseEvent<HTMLDivElement>): void => {
    const d = drag.current;
    const idx = d && wordAt(e.target);
    if (!d || idx === null || idx < d.lo || idx >= d.hi) return;
    const r = useStory.getState().range;
    if (r?.b !== idx) setRange({ a: d.a, b: idx });
  };

  const onDoubleClick = (e: MouseEvent<HTMLDivElement>): void => {
    const idx = wordAt(e.target);
    if (idx !== null) setRange({ a: idx, b: idx });
  };

  // ✦ Character: the selection's scene as a cameo springing in on its first kept word.
  const cameoFor = (r: WordRange): CameoPick | null =>
    cameoPick(project, sectionOf(sections, r.a)?.span?.scene, cueTarget(r, null, cut, null)?.at, words, cut, cast, seekWord);

  const onClick = (e: MouseEvent<HTMLDivElement>): void => {
    const chip = (e.target as Element).closest("[data-cue]") as HTMLElement | null;
    if (!chip) return;
    const id = chip.dataset.cue ?? null;
    selectCue(id);
    const p = useProjectStore.getState().project;
    const at = p?.tracks.flatMap((t) => [...(t.clips ?? []), ...(t.items ?? [])]).find((x) => x.id === id);
    if (at) setPlayhead(at.tlStartF);
  };

  if (!project || error || !doc || !words.length) {
    return (
      <div className="doc" data-slot="script" ref={rootRef}>
        <ScriptState lifecycle={lifecycle} loading={loading} error={error} empty={!!doc} />
      </div>
    );
  }

  const fps = project.fps;
  const trailingKey = sections.find((s) => s.endWord === words.length)?.key;

  return (
    <div
      className="doc"
      data-slot="script"
      ref={rootRef}
      onMouseDown={onMouseDown}
      onMouseOver={onMouseOver}
      onDoubleClick={onDoubleClick}
      onClick={onClick}
    >
      {suggestion.count > 0 && !fillersDismissed && (
        <div className="proposal" role="group" aria-label="Suggestion: cut filler words">
          <span className="spark">✦</span>
          <p>
            I found {plural(suggestion.count, "filler word")} across {plural(suggestion.scenes, "scene")}. They are marked in the script.
          </p>
          <button type="button" className="ghost" onClick={() => setFillersDismissed(true)}>Dismiss</button>
          <button type="button" className="primary" disabled={busy} onClick={() => void applyFillers()}>Apply</button>
        </div>
      )}
      {sections.map((s) => {
        const owns = (i: number | null | undefined): boolean => i !== null && i !== undefined && i >= s.startWord && i < s.endWord;
        const firstKept = s.span ? s.span.tlStartF : 0;
        const src = firstKept === null ? null : timelineToSrc(project, firstKept);
        return (
          <SceneSection
            key={s.key}
            section={s}
            words={words}
            cut={cut}
            cues={cues}
            fillers={fillerTokens}
            showSuggestion={!fillersDismissed}
            marks={proposalWords}
            sel={owns(sel?.a) ? sel : null}
            caret={owns(caretWord) ? (caretWord as number) : -1}
            cueId={cueId}
            durF={s.span ? s.span.durF : speechTotalF(project)}
            fps={fps}
            thumbUrl={thumbUrl}
            thumbSec={(src ? src.srcF : (s.span?.srcStartF ?? 0)) / fps}
            picture={(s.span && pictures.get(s.span.scene.id)) || null}
            recipe={s.span ? sceneRecipe(s.span.scene, cast) : null}
            frame={frame}
            trailing={s.key === trailingKey}
            onRename={renameScene}
          />
        );
      })}
      {sel && !dragging && (
        <SelectionBar
          key={`${sel.a}:${sel.b}`}
          rootRef={rootRef}
          lastWord={sel.b}
          allCut={rangeAllCut(cut, sel.a, sel.b)}
          busy={busy}
          onCut={() => void cutWords(sel)}
          onRestore={() => void restoreWords(sel)}
          library={{ assets, loading: libraryLoading }}
          phrase={words.slice(sel.a, sel.b + 1).map((w) => w.w).join(" ")}
          phraseS={phraseSeconds(words, sel)}
          onPlace={(payload) => placeCue(payload, cueTarget(sel, null, cut, null), assets, words, media)}
          cameo={cameoFor(sel)}
        />
      )}
    </div>
  );
}

function ScriptState({
  lifecycle,
  loading,
  error,
  empty,
}: {
  lifecycle: TranscriptLifecycle | null;
  loading: boolean;
  error: string | null;
  empty: boolean;
}) {
  const state = lifecycle?.state;
  const working = state === "queued" || state === "running";
  if (error) return <Card headline="Couldn't load the script" detail={error} />;
  if (empty) return <Card headline="No spoken words" detail="This recording's transcript has no words, so there is no script to edit." />;
  if (working) {
    return (
      <Card
        headline="Preparing your footage"
        steps={[state === "queued" ? "Waiting to transcribe" : "Transcribing every word with WhisperX", "Finding the topics", "Laying out your story"]}
        detail={lifecycle?.job?.progressStage ?? "Your script appears here as soon as the words are in."}
      />
    );
  }
  if (state === "failed") return <Card headline="Transcription failed" detail={lifecycle?.message ?? lifecycle?.job?.error ?? "The transcription could not be completed."}
    needsAddon={(lifecycle?.errorCode ?? lifecycle?.job?.errorCode) === "TRANSCRIPTION_NOT_INSTALLED"} />;
  if (state === "stale") return <Card headline="The transcript is out of date" detail={lifecycle?.message ?? "The source footage changed or is offline."} />;
  if (loading || state === "ready") return <p className="script-loading muted" aria-busy="true">Loading the script…</p>;
  return <Card headline="No transcript yet" detail="Transcribe this video and its words arrive here as a script you edit." />;
}

function Card({ headline, detail, steps, needsAddon = false }: { headline: string; detail: string; steps?: string[]; needsAddon?: boolean }) {
  return (
    <div className="process-card" role="status">
      <p className="eyebrow">Script</p>
      <h2>{headline}</h2>
      {steps && (
        <ol className="steps">
          {steps.map((s, i) => (
            <li key={s} className={i === 0 ? "on" : undefined}>
              <i />
              {s}
            </li>
          ))}
        </ol>
      )}
      <p className="muted">{detail}</p>
      {needsAddon && <AddonSettingsLink />}
    </div>
  );
}
