import { useEffect, useMemo, useState } from "react";
import { graphicDefinition } from "../../../graphics/registry";
import Timeline, { type SceneBand, type TimelineLanes } from "./Timeline";
import { setPlayhead, setSelection, useProjectStore } from "../../../store/useProjectStore";
import { useVideoCast } from "../../library/castStore";
import { roleColor } from "../arc/roles";
import { assetLabels, useDrawerLibrary } from "../drawer/library";
import { clipName } from "../source/cues";
import { selectCue, setCaret, useStory } from "../storyStore";
import { useStoryModel } from "../useStoryModel";
import { laneBlocks } from "./timelineLanes";
import "./TimelineLens.css";

const noAsset = () => null;
const noDrop = () => {};

function isSpeechClip(id: string): boolean {
  const project = useProjectStore.getState().project;
  return !!project?.tracks.some((t) => t.kind === "speech" && t.clips?.some((c) => c.id === id));
}

// The multi-track timeline uses lanes for Picture, B-roll, Graphics, Music and
// Captions under the story's scene bands, every block named as the library and the script name it. The
// selected overlay is the Story view's selected cue in both directions.
export default function TimelineLens() {
  const { project, words, media, spans } = useStoryModel();
  const { cast } = useVideoCast(false);
  const { assets } = useDrawerLibrary(project?.workspaceId);
  const [zoom, setZoom] = useState<number | null>(null);
  const bands = useMemo<SceneBand[]>(
    () =>
      spans.flatMap((s) =>
        s.tlStartF === null || s.durF <= 0
          ? []
          : [{ id: s.scene.id, label: s.scene.title, color: roleColor(s.scene.role), startF: s.tlStartF, endF: s.tlStartF + s.durF }],
      ),
    [spans],
  );
  const lanes = useMemo<TimelineLanes | null>(() => {
    if (!project) return null;
    const labels = assetLabels(assets);
    return {
      ...laneBlocks(project, words, spans, media, cast),
      label: (el) =>
        "media" in el ? clipName(el, labels)
        : "componentId" in el ? graphicDefinition(el.componentId)?.name ?? el.componentId
        : el.text,
    };
  }, [project, words, spans, media, cast, assets]);

  useEffect(() => {
    const cue = useStory.getState().cueId;
    if (cue) setSelection(cue);
    return useProjectStore.subscribe((s, prev) => {
      if (s.selection === prev.selection) return;
      if (s.selection && !isSpeechClip(s.selection)) selectCue(s.selection);
      else if (useStory.getState().cueId) selectCue(null);
    });
  }, []);

  function onSceneBand(band: SceneBand): void {
    const span = spans.find((s) => s.scene.id === band.id);
    setPlayhead(band.startF);
    if (span) setCaret(span.startWord);
  }

  return (
    <div className="tl-lens" data-slot="timeline">
      <Timeline
        className="v3-timeline"
        draggedAsset={noAsset}
        onAssetDrop={noDrop}
        initialZoom={zoom}
        onZoomChange={setZoom}
        sceneBands={bands}
        onSceneBand={onSceneBand}
        lanes={lanes ?? undefined}
      />
      <p className="hint">Everything here is derived from the script. Click anywhere to jump.</p>
    </div>
  );
}
