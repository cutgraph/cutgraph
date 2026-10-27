import type { KeyboardEvent } from "react";
import { persistCurrent } from "../../persist";
import { applyLocal, useProjectStore } from "../../store/useProjectStore";
import { NarrationButton, ScriptKind } from "../newscript/Narration";
import { openExport, setLens, useStory, type Lens } from "./storyStore";

const LENSES: [Lens, string][] = [
  ["script", "Script"],
  ["board", "Board"],
  ["timeline", "Timeline"],
  ["source", "Source"],
];

function commitTitle(next: string): void {
  const { project } = useProjectStore.getState();
  if (!project) return;
  const title = next.trim() || undefined;
  if ((project.title ?? undefined) === title) return;
  applyLocal(() => ({ project: { ...project, title } }), { origin: "human", label: "Renamed video" });
  void persistCurrent(project.name);
}

function ProjectTitle({ title }: { title: string }) {
  const commit = (el: HTMLElement): void => {
    const next = (el.textContent ?? "").trim();
    if (next === title) el.textContent = title;
    else commitTitle(next);
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
    <h1
      contentEditable
      suppressContentEditableWarning
      spellCheck={false}
      onBlur={(e) => commit(e.currentTarget)}
      onKeyDown={onKeyDown}
    >
      {title}
    </h1>
  );
}

export default function TopBar({ video, onUndo }: { video: string; onUndo: () => void }) {
  const title = useProjectStore((s) => s.project?.title || s.project?.name || video);
  const lens = useStory((s) => s.lens);
  return (
    <header className="top">
      <div className="ptitle">
        <ProjectTitle key={title} title={title} />
        <ScriptKind />
      </div>
      <div className="seg" role="group" aria-label="View">
        {LENSES.map(([id, label]) => (
          <button key={id} type="button" className={lens === id ? "on" : undefined} aria-pressed={lens === id} onClick={() => setLens(id)}>
            {label}
          </button>
        ))}
      </div>
      <div className="top-actions">
        <NarrationButton video={video} />
        <button type="button" className="icon" title="Undo (Ctrl+Z)" aria-label="Undo" onClick={onUndo}>
          ↶
        </button>
        <button type="button" className="primary" onClick={() => openExport(true)}>
          Export
        </button>
      </div>
    </header>
  );
}
