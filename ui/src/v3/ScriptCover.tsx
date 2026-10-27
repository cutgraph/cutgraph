// A video's drawn cover. With a thumbnail it is that poster, as the Export sheet previews it. A script
// video's speech media is a black, silent source, so without one its cover is its first animated
// scene instead: drawn by motion-core from the player's spec rule (story/shot/specs.ts), at the settled
// moment the Board and Script thumbnails show.
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api";
import { useTranscript } from "../transcript/useTranscript";
import type { Project } from "../types/project";
import { useCast } from "./library/castStore";
import { sceneSpans, speechMedia } from "./story/model";
import ScenePicture from "./story/shot/ScenePicture";
import { useScreenAssets } from "./story/shot/screenLibrary";
import { animatedSceneSpecs } from "./story/shot/specs";
import PosterPicture from "./story/thumbnail/PosterPicture";
import { posterOf } from "./story/thumbnail/thumbnail";

export default function ScriptCover({ video, fallback }: { video: string; fallback: ReactNode }) {
  const [project, setProject] = useState<Project | null>(null);
  useEffect(() => {
    let live = true;
    api.getProject(video).then(
      (envelope) => {
        if (live) setProject(envelope.project);
      },
      () => {},
    );
    return () => {
      live = false;
    };
  }, [video]);
  const covered = !!project?.thumbnail;
  const { doc } = useTranscript(project && !covered ? video : null);
  const { cast, status } = useCast({ workspace: project?.workspaceId ?? null }, !!project);
  const screens = useScreenAssets(covered ? null : project);
  const poster = useMemo(
    () => (project && covered && (status === "ready" || status === "error") ? posterOf(project, cast) : null),
    [project, covered, cast, status],
  );
  const spec = useMemo(() => {
    const words = doc?.words;
    if (!project || covered || !words?.length) return null;
    const media = speechMedia(project);
    return animatedSceneSpecs(project, words, sceneSpans(project, media, words), media, cast, screens)[0] ?? null;
  }, [project, covered, doc, cast, screens]);
  if (poster) {
    return (
      <span className="cover cover-scene" aria-hidden="true">
        <PosterPicture poster={poster} format="16:9" width={480} />
      </span>
    );
  }
  if (!project || !spec) return <>{fallback}</>;
  return (
    <span className="cover cover-scene" aria-hidden="true">
      <ScenePicture spec={spec} width={project.width} height={project.height} fps={project.fps} />
    </span>
  );
}
