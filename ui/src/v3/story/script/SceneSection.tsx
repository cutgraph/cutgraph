import { ACCENT } from "motion-core";
import { Fragment, memo, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { SceneRenderSpec } from "../../../preview/SceneLayer";
import ScenePicture from "../shot/ScenePicture";
import type { TranscriptWord } from "../../../api";
import type { SceneRecipe } from "../model";
import type { ProposalWordKind, WordRange } from "../storyStore";
import ShotChip from "../shot/ShotChip";
import { CUE_ICON, clock, type FillerToken, type Section, type WordCue } from "./script";

export const ROLE_COLOR: Record<string, string> = {
  Hook: ACCENT, Problem: "#e8845c", Approach: "#79d5df", Build: "#8fb3ff",
  Example: "#8fb3ff", Check: "#b69cff", Payoff: "#90caa5", Wrap: "#90caa5", Beat: "#9aa7b1",
};

export interface SceneSectionProps {
  section: Section;
  words: TranscriptWord[];
  cut: Set<number>;
  cues: Map<number, WordCue[]>;
  fillers: Map<number, FillerToken[]>;
  showSuggestion: boolean;
  /** Words a pending Ask-bar proposal touches (null when none is pending). */
  marks: ReadonlyMap<number, ProposalWordKind> | null;
  /** The selection and caret, only when they fall in this section (keeps the others memo-stable). */
  sel: WordRange | null;
  caret: number;
  cueId: string | null;
  durF: number;
  fps: number;
  thumbUrl: string | null;
  thumbSec: number;
  /** The scene's animated picture (Animation or Cameo), painted over the footage still. */
  picture: SceneRenderSpec | null;
  /** The animation recipe line under the words; null on a footage scene. */
  recipe: SceneRecipe | null;
  frame: { width: number; height: number };
  trailing: boolean;
  onRename: (sceneId: string, title: string) => void;
}

function SceneSection(props: SceneSectionProps) {
  const { section, words, cut, cues, fillers, showSuggestion, marks, sel, caret, cueId, durF, fps, thumbUrl, thumbSec, picture, recipe, frame, trailing, onRename } = props;
  const scene = section.span?.scene ?? null;

  const tokens: ReactNode[] = [];
  const pushFillers = (at: number): void => {
    for (const f of fillers.get(at) ?? []) {
      const cls = f.state === "cut" ? "w filler cut" : showSuggestion ? "w filler sug" : "w filler";
      tokens.push(<Fragment key={f.key}><span className={cls} title="Filler word">{f.w}</span> </Fragment>);
    }
  };
  for (let i = section.startWord; i < section.endWord; i++) {
    pushFillers(i);
    for (const c of cues.get(i) ?? []) {
      tokens.push(
        <button key={`c:${c.id}`} type="button" className={`cue cue-${c.kind}${c.id === cueId ? " on" : ""}`} data-cue={c.id} title={c.label}>
          {CUE_ICON[c.kind]} {c.label}
        </button>,
      );
    }
    let cls = "w";
    if (cut.has(i)) cls += " cut";
    else if (marks?.get(i) === "cut") cls += " sug";
    else if (marks?.has(i)) cls += " prop";
    if (sel && i >= sel.a && i <= sel.b) cls += " selw";
    else if (i === caret) cls += " caret";
    tokens.push(<Fragment key={i}><span className={cls} data-word-idx={i}>{words[i].w}</span> </Fragment>);
  }
  if (trailing) pushFillers(words.length);

  return (
    <section className="scene" data-scene={scene?.id ?? "all"}>
      <SceneThumb url={thumbUrl} sec={thumbSec} picture={picture} frame={frame} fps={fps} />
      <div className="scene-body">
        <header className="scene-head">
          {scene ? (
            <>
              <span className="role" style={{ ["--c" as string]: ROLE_COLOR[scene.role] ?? ROLE_COLOR.Beat }}>{scene.role}</span>
              <SceneTitle key={scene.title} title={scene.title} onCommit={(t) => onRename(scene.id, t)} />
              <ShotChip scene={scene} />
            </>
          ) : (
            <h2>Your script</h2>
          )}
          <span className="dur" data-frames={durF}>{clock(durF, fps)}</span>
        </header>
        <p className="words">{tokens}</p>
        {recipe && (
          <p className="recipe-line">
            {recipe.actor} · {recipe.pose} · {recipe.expression} · {recipe.move} · “{recipe.headline}”
          </p>
        )}
      </div>
    </section>
  );
}

export default memo(SceneSection);

function SceneTitle({ title, onCommit }: { title: string; onCommit: (title: string) => void }) {
  const commit = (el: HTMLElement): void => {
    const next = (el.textContent ?? "").trim();
    if (next && next !== title) onCommit(next);
    else el.textContent = title;
  };
  const onKeyDown = (e: KeyboardEvent<HTMLHeadingElement>): void => {
    if (e.key === "Enter") {
      e.preventDefault();
      e.currentTarget.blur();
    } else if (e.key === "Escape") {
      e.currentTarget.textContent = title;
      e.currentTarget.blur();
    }
  };
  return (
    <h2
      contentEditable
      suppressContentEditableWarning
      spellCheck={false}
      aria-label="Scene title"
      onBlur={(e) => commit(e.currentTarget)}
      onKeyDown={onKeyDown}
    >
      {title}
    </h2>
  );
}

// A still of the proxy at the scene's first kept frame: a paused <video> seeked by a media fragment,
// mounted only once the section scrolls near the viewport.
function SceneThumb({ url, sec, picture, frame, fps }: {
  url: string | null;
  sec: number;
  picture: SceneRenderSpec | null;
  frame: { width: number; height: number };
  fps: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [near, setNear] = useState(typeof IntersectionObserver === "undefined");
  useEffect(() => {
    const el = ref.current;
    if (near || !el) return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        setNear(true);
        io.disconnect();
      }
    }, { rootMargin: "400px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, [near]);
  return (
    <div className="scene-thumb" ref={ref}>
      {near && url && picture?.mode !== "animation" ? <video src={`${url}#t=${sec.toFixed(2)}`} preload="metadata" muted playsInline tabIndex={-1} aria-hidden="true" /> : <div className="thumb-ph" />}
      {near && picture && <ScenePicture spec={picture} width={frame.width} height={frame.height} fps={fps} />}
    </div>
  );
}
