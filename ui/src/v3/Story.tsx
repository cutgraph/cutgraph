import { useEffect } from "react";
import { useProjectStore } from "../store/useProjectStore";
import { linkProps, replaceRoute } from "./router";
import StoryView from "./story/StoryView";

export default function Story({ ws, video }: { ws?: string; video?: string }) {
  // The route names the workspace the open video is in (unsorted when none), so the context bar shows it
  // and the rail carries it to the Library.
  const home = useProjectStore((s) => (video && s.project?.name === video ? s.project.workspaceId || "unsorted" : null));
  useEffect(() => {
    if (video && home && home !== ws) replaceRoute({ view: "story", ws: home, video });
  }, [video, home, ws]);
  if (video) return <StoryView key={video} ws={ws} video={video} />;
  return (
    <section className="library">
      <header className="lib-top">
        <div>
          <p className="eyebrow">Story</p>
          <h1>No video open</h1>
        </div>
      </header>
      <div className="placeholder">
        <p>Open a video from one of your workspaces to see its story here.</p>
        <div className="row">
          <a className="primary" {...linkProps(ws ? { view: "project", ws } : { view: "home" })}>
            {ws ? "Back to workspace" : "Go to workspaces"}
          </a>
        </div>
      </div>
    </section>
  );
}
