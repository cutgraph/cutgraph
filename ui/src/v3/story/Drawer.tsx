import { useMemo, useState } from "react";
import { api } from "../../api";
import { setPlayhead, useProjectStore } from "../../store/useProjectStore";
import type { Project } from "../../types/project";
import { GRAPHIC_DEFINITIONS, GRAPHIC_REGISTRY } from "../../graphics/registry";
import { updateGraphic } from "../../graphics/ops";
import { deleteElement } from "../../timeline/ops";
import { useVideoCast } from "../library/castStore";
import { linkProps } from "../router";
import { arcLayout, arcPlayhead } from "./arc/layout";
import { commitEdit } from "./history";
import { sceneOfWord, sceneRecipe } from "./model";
import { cameoPick } from "./script/cameo";
import CameoTile from "./script/CameoTile";
import ScenePicture from "./shot/ScenePicture";
import { sceneSpec } from "./shot/specs";
import { openDrawer, openShot, selectCue, setCaret, useStory, type StoryState } from "./storyStore";
import { useStoryModel } from "./useStoryModel";
import CueCard from "./drawer/CueCard";
import { findCue, swapAsset, swapGraphic, wordAnchor, wordAtFrame } from "./drawer/insert";
import { assetsForTab, DRAWER_TABS, EMPTY_TAB, useDrawerLibrary } from "./drawer/library";
import { cueTarget, placeCue } from "./drawer/place";
import StockPanel from "./drawer/stock/StockPanel";
import { AssetTile, GraphicTile, TYPE_NAME } from "./drawer/Tiles";
import { useWordDrop, type CuePayload } from "./drawer/wordDrop";
import "./drawer.css";

type Tab = NonNullable<StoryState["drawerTab"]>;

// The Character tab is for footage videos; a script video's scenes are already animated.
const CHARACTER_TAB: [Tab, string] = ["anim", "Character"];

function edit(op: (current: Project) => Project, label: string): void {
  const current = useProjectStore.getState().project;
  if (!current) return;
  const next = op(current);
  if (next !== current) commitEdit(next, label);
}

export default function Drawer() {
  const { project, words, media, cut, spans } = useStoryModel();
  const cueId = useStory((s) => s.cueId);
  const range = useStory((s) => s.range);
  const caretWord = useStory((s) => s.caretWord);
  const footage = !!project && project.narration == null;
  const tabs: readonly [Tab, string][] = footage ? [...DRAWER_TABS, CHARACTER_TAB] : DRAWER_TABS;
  const stored = useStory((s) => s.drawerTab) ?? "broll";
  const tab = tabs.some(([id]) => id === stored) ? stored : "broll";
  const library = useDrawerLibrary(project?.workspaceId);
  // A script video's speech media is black, so its graphics preview on the plain card instead.
  const backdrop = media && footage ? api.thumb(media.split("/").pop()!.replace(/\.[^.]+$/, "")) : null;
  const [stockOpen, setStockOpen] = useState(false);
  const playheadF = useProjectStore((s) => s.playheadF);
  const { cast } = useVideoCast(false);

  // The scene under the playhead, when it is animated or a cameo: the same
  // playhead-to-block lookup the arc uses for its lit segment.
  const arc = useMemo(() => arcLayout(spans), [spans]);
  const at = arcPlayhead(arc, playheadF);
  const currentScene = at ? arc.blocks[at.index] : null;
  const recipe = currentScene ? sceneRecipe(currentScene.span.scene, cast) : null;
  const shot = project && currentScene && recipe ? sceneSpec(project, words, currentScene.span, media, cast) : null;

  const seekWord = (idx: number): void => {
    const current = useProjectStore.getState().project;
    const f = current && media ? wordAnchor(current, words, media, idx)?.tlStartF : undefined;
    if (f !== undefined) setPlayhead(f);
    setCaret(idx);
  };
  // The Character tab's cameo, the same pick as ✦ Character on a selection: the scene holding the word.
  const cameoAt = (idx: number | undefined) =>
    cameoPick(project, idx === undefined ? undefined : sceneOfWord(spans, idx)?.scene, idx, words, cut, cast, seekWord);

  useWordDrop((payload, wordIdx) => {
    if (payload.type === "cameo") cameoAt(wordIdx)?.onPick();
    else placeCue(payload, { at: wordIdx }, library.assets, words, media);
  });

  // A click adds at the selection when there is one, else the caret,
  // else the word under the playhead.
  const cursor = () => {
    const playheadWord = project && media ? wordAtFrame(project, words, media, playheadF) : null;
    return cueTarget(range, caretWord, cut, playheadWord);
  };
  const insertAtCursor = (payload: CuePayload) => placeCue(payload, cursor(), library.assets, words, media);

  const cue = project && cueId ? findCue(project, cueId) : null;
  if (project && cue) {
    return (
      <div className="drawer" id="drawer" data-slot="drawer">
        <CueCard
          cue={cue}
          project={project}
          words={words}
          spans={spans}
          assets={library.assets}
          backdrop={backdrop}
          actions={{
            close: () => selectCue(null),
            remove: () => {
              edit((p) => deleteElement(p, cue.id), `Removed ${TYPE_NAME[cue.kind].toLowerCase()}`);
              selectCue(null);
            },
            swapAsset: (entry) => edit((p) => swapAsset(p, cue.id, entry), `Swapped for ${entry.asset.name}`),
            swapGraphic: (id) => edit((p) => swapGraphic(p, cue.id, id), `Swapped for ${GRAPHIC_REGISTRY[id].name.toLowerCase()}`),
            editGraphic: (props) => edit((p) => updateGraphic(p, cue.id, props), "Graphic text updated"),
          }}
        />
      </div>
    );
  }

  const assets = tab === "anim" ? [] : assetsForTab(library.assets, tab);
  const cameo = tab === "anim" ? cameoAt(cursor()?.at) : null;
  const workspaceId = project?.workspaceId;
  // Stock imports need a real library to land in; a video with no project yet falls back to the
  // built-in brand kit, the same library useDrawerLibrary always includes.
  const importWorkspaceId = workspaceId && workspaceId !== "unsorted" ? workspaceId : "brand";

  if (stockOpen && tab === "broll") {
    return (
      <div className="drawer" id="drawer" data-slot="drawer">
        <StockPanel workspaceId={importWorkspaceId} onAdded={library.refresh} onClose={() => setStockOpen(false)} />
      </div>
    );
  }

  return (
    <div className="drawer" id="drawer" data-slot="drawer">
      {currentScene && recipe && (
        <div className="shot-card">
          {shot && <ScenePicture spec={shot} width={project?.width ?? 1920} height={project?.height ?? 1080} fps={project?.fps ?? 30} />}
          <div>
            <p className="eyebrow">Scene {currentScene.span.index + 1} shot</p>
            <p>{recipe.actor} · {recipe.pose} · {recipe.expression} · {recipe.move}</p>
            <button type="button" className="primary" onClick={() => openShot(currentScene.span.scene.id)}>Open shot</button>
          </div>
        </div>
      )}
      <div className="drawer-head">
        <h3>Add to your video</h3>
        <p className="muted">Drag onto a word, or click to add at the cursor.</p>
        {tab === "broll" && (
          <button type="button" className="ghost" onClick={() => setStockOpen(true)}>Search stock</button>
        )}
      </div>
      <div className="pills" role="tablist" aria-label="Library">
        {tabs.map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={id === tab} className={id === tab ? "on" : undefined} onClick={() => openDrawer(id)}>
            {label}
          </button>
        ))}
      </div>
      {tab === "anim" ? (
        cameo ? (
          <div className="tiles">
            <CameoTile cameo={cameo} draggable />
          </div>
        ) : (
          <p className="drawer-empty">Put the cursor on a word that plays, and your character springs in there.</p>
        )
      ) : tab === "graphics" ? (
        <div className="tiles">
          {GRAPHIC_DEFINITIONS.map((d) => (
            <GraphicTile key={d.id} componentId={d.id} onPick={() => insertAtCursor({ type: "graphic", componentId: d.id })} />
          ))}
        </div>
      ) : assets.length ? (
        <div className="tiles">
          {assets.map((entry) => (
            <AssetTile
              key={`${entry.workspaceId}/${entry.asset.id}`}
              entry={entry}
              onPick={() => insertAtCursor({ type: "asset", workspaceId: entry.workspaceId, assetId: entry.asset.id })}
            />
          ))}
        </div>
      ) : (
        <p className="drawer-empty">
          {library.loading ? "Loading the library…" : library.error ?? `${EMPTY_TAB[tab]} in this workspace's library yet.`}
          {!library.loading && workspaceId && workspaceId !== "unsorted" && (
            <> <a className="link" {...linkProps({ view: "project", ws: workspaceId })}>Add media</a></>
          )}
        </p>
      )}
    </div>
  );
}
