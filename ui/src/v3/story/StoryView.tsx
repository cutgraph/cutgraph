import { useEffect, useState } from "react";
import { api } from "../../api";
import { useProjectSSE } from "../../sse";
import { adoptSaved, useProjectStore } from "../../store/useProjectStore";
import { redoStep, undoStep } from "./history";
import { resetStory, useStory } from "./storyStore";
import Coach from "./Coach";
import TopBar from "./TopBar";
import ArcStrip from "./ArcStrip";
import ScriptLens from "./lenses/ScriptLens";
import BoardLens from "./lenses/BoardLens";
import TimelineLens from "./lenses/TimelineLens";
import SourceLens from "./lenses/SourceLens";
import PlayerPanel from "./PlayerPanel";
import Drawer from "./Drawer";
import AskBar from "./AskBar";
import ExportSheet from "./ExportSheet";
import ShotSheet from "./shot/ShotSheet";
import "./story.css";

export function isEditingText(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
}

export default function StoryView({ video }: { ws?: string; video: string }) {
  const project = useProjectStore((s) => s.project);
  const lens = useStory((s) => s.lens);
  const exportOpen = useStory((s) => s.exportOpen);
  const shotSceneId = useStory((s) => s.shotSceneId);
  const [error, setError] = useState<string | null>(null);

  useProjectSSE(video);

  useEffect(() => {
    resetStory();
    setError(null);
    api
      .getProject(video)
      .then(({ project: doc, revision }) => adoptSaved(doc, revision))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [video]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (isEditingText(event.target)) return;
      const key = event.key.toLowerCase();
      if ((event.ctrlKey || event.metaKey) && (key === "z" || key === "y")) {
        event.preventDefault();
        if (key === "y" || event.shiftKey) redoStep();
        else undoStep();
      } else if (event.key === " ") {
        event.preventDefault();
        window.dispatchEvent(new Event("cutgraph:toggle-preview"));
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  if (error) {
    return (
      <section className="story-error">
        <h1>Couldn&apos;t open this video</h1>
        <p className="muted">{error}</p>
      </section>
    );
  }
  if (!project || project.name !== video) {
    return <section className="story-loading" aria-busy="true">Opening video…</section>;
  }

  return (
    <section className="story" data-lens={lens}>
      <TopBar video={video} onUndo={undoStep} />
      <ArcStrip />
      <div className="pane" id="story-pane">
        {lens === "script" && <ScriptLens />}
        {lens === "board" && <BoardLens />}
        {lens === "timeline" && <TimelineLens />}
        {lens === "source" && <SourceLens />}
      </div>
      <aside className="side">
        <PlayerPanel />
        <Drawer />
      </aside>
      <AskBar />
      {exportOpen && <ExportSheet />}
      {shotSceneId && <ShotSheet />}
      <Coach active={!exportOpen && !shotSceneId} />
    </section>
  );
}
