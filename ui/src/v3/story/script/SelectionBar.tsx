import { useLayoutEffect, useRef, useState, type RefObject } from "react";
import { GRAPHIC_DEFINITIONS } from "../../../graphics/registry";
import type { CueKind } from "../drawer/insert";
import { assetsForTab, EMPTY_TAB, type DrawerTab, type LibraryAsset } from "../drawer/library";
import { rankAssets } from "../drawer/rank";
import { AssetTile, GraphicTile, TYPE_NAME } from "../drawer/Tiles";
import type { CuePayload } from "../drawer/wordDrop";
import { openDrawer } from "../storyStore";
import type { CameoPick } from "./cameo";
import CameoTile from "./CameoTile";

const CUE_BUTTONS: [DrawerTab, CueKind, string][] = [
  ["broll", "broll", "▣ B-roll"],
  ["graphics", "graphic", "◆ Graphic"],
  ["music", "music", "♪ Music"],
  ["sfx", "sfx", "✧ Sound"],
];

const SUGGESTED = 3;

type PickKind = DrawerTab | "anim";

export interface SelectionLibrary {
  assets: LibraryAsset[];
  loading: boolean;
}

// The floating toolbar over a word selection: cut (or restore an all-cut range), pick a cue to lay on
// the selection, or ✦ Character: the scene's character springing in on it as a cameo. Fixed-positioned
// above the selection's last word, or below it when there is no room.
export default function SelectionBar({
  rootRef,
  lastWord,
  allCut,
  busy,
  onCut,
  onRestore,
  library,
  phrase,
  phraseS,
  onPlace,
  cameo,
}: {
  rootRef: RefObject<HTMLElement | null>;
  lastWord: number;
  allCut: boolean;
  busy: boolean;
  onCut: () => void;
  onRestore: () => void;
  library: SelectionLibrary;
  /** The selected words and how long they run, which rank the picks. */
  phrase: string;
  phraseS: number;
  onPlace: (payload: CuePayload) => void;
  /** What ✦ Character adds, or null where it isn't offered (a script video, or no kept word in a scene). */
  cameo: CameoPick | null;
}) {
  const barRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const [pick, setPick] = useState<PickKind | null>(null);

  useLayoutEffect(() => {
    const place = (): void => {
      const anchor = rootRef.current?.querySelector(`[data-word-idx="${lastWord}"]`);
      const bar = barRef.current;
      if (!anchor || !bar) return;
      const r = anchor.getBoundingClientRect();
      const w = bar.offsetWidth;
      const h = bar.offsetHeight;
      const left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2));
      setPos({ left, top: r.top - h - 10 > 8 ? r.top - h - 10 : r.bottom + 10 });
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [rootRef, lastWord, pick]);

  return (
    <div
      className="selpop"
      ref={barRef}
      role={pick ? "dialog" : "toolbar"}
      aria-label={pick ? "Pick" : "Selection"}
      style={pos ? { left: pos.left, top: pos.top } : { visibility: "hidden" }}
    >
      {pick === "anim" ? (
        cameo && (
          <div className="pick">
            <p className="eyebrow">Add animation</p>
            <div className="tiles"><CameoTile cameo={cameo} /></div>
          </div>
        )
      ) : pick ? (
        <Pick tab={pick} library={library} phrase={phrase} phraseS={phraseS} onPlace={onPlace} />
      ) : (
        <div className="selbar">
          <button type="button" disabled={busy} onClick={allCut ? onRestore : onCut}>
            {allCut ? "↺ Restore" : "✂ Cut"}
          </button>
          {CUE_BUTTONS.map(([tab, , label]) => (
            <button key={tab} type="button" onClick={() => { setPick(tab); openDrawer(tab); }}>
              {label}
            </button>
          ))}
          {cameo && <button type="button" onClick={() => setPick("anim")}>✦ Character</button>}
        </div>
      )}
    </div>
  );
}

// The pick popover shows B-roll ranked for the selected words ("Claude suggests", the co-pilot's
// own ranking), or every graphic, music track or sound to choose from.
function Pick({ tab, library, phrase, phraseS, onPlace }: {
  tab: DrawerTab;
  library: SelectionLibrary;
  phrase: string;
  phraseS: number;
  onPlace: (payload: CuePayload) => void;
}) {
  const kind = CUE_BUTTONS.find(([t]) => t === tab)![1];
  const title = tab === "broll" ? "Claude suggests" : `Add ${TYPE_NAME[kind].toLowerCase()}`;
  const ranked = tab === "graphics" ? [] : rankAssets(assetsForTab(library.assets, tab), phrase, phraseS);
  const tiles = tab === "graphics"
    ? GRAPHIC_DEFINITIONS.map((d) => (
      <GraphicTile key={d.id} componentId={d.id} onPick={() => onPlace({ type: "graphic", componentId: d.id })} />
    ))
    : (tab === "broll" ? ranked.slice(0, SUGGESTED) : ranked).map((entry) => (
      <AssetTile
        key={`${entry.workspaceId}/${entry.asset.id}`}
        entry={entry}
        onPick={() => onPlace({ type: "asset", workspaceId: entry.workspaceId, assetId: entry.asset.id })}
      />
    ));
  return (
    <div className="pick">
      <p className="eyebrow">{title}</p>
      {tiles.length ? (
        <div className="tiles">{tiles}</div>
      ) : (
        <p className="pick-empty">{library.loading ? "Loading the library…" : `${EMPTY_TAB[tab]} in this workspace's library yet.`}</p>
      )}
    </div>
  );
}
