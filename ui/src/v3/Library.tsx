// The Library. Cast is the characters the current workspace can
// cast; the other tabs show the brand workspace's media, the graphic templates and the brand kit.
import { useState } from "react";
import CastView from "./library/CastView";
import { BrandKit, BrandMedia, GraphicTemplates } from "./library/MediaTabs";
import "./library/library.css";

type Tab = "cast" | "broll" | "music" | "sfx" | "graphics" | "brand";

const TABS: [Tab, string][] = [
  ["cast", "Cast"], ["broll", "B-roll"], ["music", "Music"], ["sfx", "Sounds"], ["graphics", "Graphics"], ["brand", "Brand"],
];

/** `ws` and `video` are the workspace and video the Library was opened from: its Cast shows theirs. */
export default function Library({ ws, video }: { ws?: string; video?: string }) {
  const [tab, setTab] = useState<Tab>("cast");
  return (
    <section className="library">
      <header className="lib-top">
        <h1>Library</h1>
        <div className="pills" role="group" aria-label="Library sections">
          {TABS.map(([id, label]) => (
            <button key={id} type="button" className={id === tab ? "on" : undefined} aria-pressed={id === tab} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </div>
      </header>
      {tab === "cast" && <CastView scope={{ workspace: ws, video }} />}
      {(tab === "broll" || tab === "music" || tab === "sfx") && <BrandMedia key={tab} tab={tab} ws={ws} />}
      {tab === "graphics" && <GraphicTemplates />}
      {tab === "brand" && <BrandKit />}
    </section>
  );
}
