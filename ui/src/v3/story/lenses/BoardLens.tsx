import { useMemo, useState, type CSSProperties, type KeyboardEvent } from "react";
import ScenePicture from "../shot/ScenePicture";
import { animatedSceneSpecs } from "../shot/specs";
import { useScreenAssets } from "../shot/screenLibrary";
import type { TranscriptWord } from "../../../api";
import { resolveMedia } from "../../../preview/resolveMedia";
import { useSourceProxy } from "../../../preview/sourceProxy";
import { setPlayhead } from "../../../store/useProjectStore";
import { speechClipAt, timelineToSrc } from "../../../transcript/cutmap";
import { isGraphicItem, type TrustedGraphicItem } from "../../../graphics/registry";
import type { Clip, Project } from "../../../types/project";
import { clock } from "../../common";
import { roleColor } from "../arc/roles";
import { moveItem, reorderScenes } from "../board/reorder";
import { panelStill } from "../board/still";
import { GraphicFrame } from "../drawer/Tiles";
import { sceneOfWord, type SceneSpan } from "../model";
import { CUE_ICON, cueLabel, wordCues, type Cue } from "../source/cues";
import { openShot, setCaret, setLens, useStory } from "../storyStore";
import { animatedExpression } from "../shot/ShotChip";
import { useVideoCast } from "../../library/castStore";
import { useStoryModel } from "../useStoryModel";
import "./BoardLens.css";

const PREVIEW_CHARS = 110;

function keptPreview(words: TranscriptWord[], cut: Set<number>, span: SceneSpan): string {
  let text = "";
  for (let i = span.startWord; i < span.endWord; i++) {
    if (cut.has(i) || !words[i].w) continue;
    text = text ? `${text} ${words[i].w}` : words[i].w;
    if (text.length > PREVIEW_CHARS) return `${text.slice(0, PREVIEW_CHARS - 2).trimEnd()}…`;
  }
  return text;
}

const IMAGE = /\.(png|jpe?g|webp|gif)$/i;

// One frame of `media` at `sec`: an image as it is, a video as a media-fragment still from the scrub
// proxy, so the browser fetches only what it needs to decode that one frame.
function Frame({ media, sec, className }: { media: Clip["media"]; sec: number; className: string }) {
  const url = resolveMedia(media);
  if (!url) return <div className="still" aria-hidden="true" />;
  if (IMAGE.test(media.source)) return <img className={className} src={url} alt="" draggable={false} aria-hidden="true" />;
  return (
    <video
      className={className}
      src={`${url}#t=${sec.toFixed(2)}`}
      preload="metadata"
      muted
      playsInline
      draggable={false}
      tabIndex={-1}
      aria-hidden="true"
    />
  );
}

// The panel's still (board/still.ts): the scene's B-roll, or its footage with the graphic on screen then.
function SceneStill({ project, span, cues }: { project: Project; span: SceneSpan; cues: readonly Cue[] }) {
  useSourceProxy(project);
  const still = panelStill(span, cues);
  if (still?.kind === "broll") return <Frame media={still.clip.media} sec={still.srcF / project.fps} className="still broll" />;
  const at = still && (timelineToSrc(project, still.tlF) ?? (span.tlStartF === null ? null : timelineToSrc(project, span.tlStartF)));
  const clip = at ? speechClipAt(project, at.media, at.srcF) : null;
  if (!at || !clip) return <div className="still" aria-hidden="true" />;
  return (
    <>
      <Frame media={clip.media} sec={at.srcF / project.fps} className="still" />
      {still?.graphic && isGraphicItem(still.graphic) && (
        <GraphicFrame item={still.graphic as TrustedGraphicItem} backdrop={null} className="still-gfx" width={project.width} height={project.height} />
      )}
    </>
  );
}

export default function BoardLens() {
  const { project, media, words, cut, spans } = useStoryModel();
  const { cast } = useVideoCast(false);
  const screens = useScreenAssets(project);
  const pictures = useMemo(
    () => new Map((project ? animatedSceneSpecs(project, words, spans, media, cast, screens) : []).map((spec) => [spec.sceneId, spec])),
    [project, words, spans, media, cast, screens],
  );
  const caretWord = useStory((s) => s.caretWord);
  const [drag, setDrag] = useState<{ from: number; over: number | null } | null>(null);
  const cues = useMemo(() => (project ? wordCues(project, media, words.length) : []), [project, media, words.length]);
  if (!project) return null;
  const name = project.name;
  const current = caretWord === null ? -1 : (sceneOfWord(spans, caretWord)?.index ?? -1);

  function move(from: number, to: number): void {
    if (from === to || to < 0 || to >= spans.length) return;
    void reorderScenes(name, moveItem(spans.map((s) => s.scene.id), from, to), `Moved “${spans[from].scene.title}”`);
  }

  function select(span: SceneSpan): void {
    setCaret(span.startWord);
    if (span.tlStartF !== null) setPlayhead(span.tlStartF);
  }

  function open(span: SceneSpan): void {
    select(span);
    setLens("script");
  }

  function openOrShot(span: SceneSpan): void {
    if (animatedExpression(span.scene, cast)) {
      select(span);
      openShot(span.scene.id);
    } else open(span);
  }

  function onKeyDown(e: KeyboardEvent, i: number): void {
    const earlier = e.key === "ArrowLeft" || e.key === "ArrowUp";
    const later = e.key === "ArrowRight" || e.key === "ArrowDown";
    if (e.key === "Enter") open(spans[i]);
    else if (e.altKey && (earlier || later)) move(i, earlier ? i - 1 : i + 1);
    else return;
    e.preventDefault();
  }

  return (
    <div className="board-lens" data-slot="board">
      <div className="board">
        {spans.map((span, i) => {
          const { scene } = span;
          const expression = animatedExpression(scene, cast);
          const sceneCues = cues.filter((c) => c.wordIdx >= span.startWord && c.wordIdx < span.endWord);
          const cls = ["panel", i === current && "on", drag?.from === i && "dragging", drag && drag.from !== i && drag.over === i && "drop"];
          return (
            <article
              key={scene.id}
              className={cls.filter(Boolean).join(" ")}
              data-scene={scene.id}
              draggable
              tabIndex={0}
              aria-label={`Scene ${i + 1}: ${scene.title}. Alt+arrow keys move it, Enter opens it.`}
              onClick={() => select(span)}
              onDoubleClick={() => openOrShot(span)}
              onKeyDown={(e) => onKeyDown(e, i)}
              onDragStart={(e) => {
                e.dataTransfer.effectAllowed = "move";
                e.dataTransfer.setData("text/plain", scene.id);
                setDrag({ from: i, over: null });
              }}
              onDragOver={(e) => {
                if (!drag) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
                if (drag.over !== i) setDrag({ from: drag.from, over: i });
              }}
              onDrop={(e) => {
                e.preventDefault();
                if (drag) move(drag.from, i);
                setDrag(null);
              }}
              onDragEnd={() => setDrag(null)}
            >
              <div className="still-wrap">
                {pictures.get(scene.id)?.mode !== "animation" ? <SceneStill project={project} span={span} cues={sceneCues} /> : <div className="still" aria-hidden="true" />}
                {pictures.has(scene.id) && <ScenePicture spec={pictures.get(scene.id)!} width={project.width} height={project.height} fps={project.fps} />}
              </div>
              <div className="panel-meta">
                <span className="role" style={{ "--c": roleColor(scene.role) } as CSSProperties}>
                  {scene.role}
                </span>
                <b>{scene.title}</b>
                <span className="dur">{clock(span.durF / project.fps)}</span>
              </div>
              <p>{keptPreview(words, cut, span)}</p>
              <div className="panel-cues">
                {sceneCues.map((c) => (
                  <span key={c.id} className={`mini cue-${c.kind}`} title={cueLabel(c)}>
                    {CUE_ICON[c.kind]}
                  </span>
                ))}
                {expression && <span className="mini cue-anim">✦ {expression}</span>}
              </div>
            </article>
          );
        })}
      </div>
      <p className="hint">Drag panels to reorder the story. Double-click to open a scene.</p>
    </div>
  );
}
