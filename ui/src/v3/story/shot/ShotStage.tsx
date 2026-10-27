import { useEffect, useRef, type CSSProperties } from "react";
import { DEFAULT_FONT, type MotionEnv, type Shot, type ShotMode, type ShotTiming } from "motion-core";
import type { PillPage } from "../../../preview/captionPages";
import { paintScene } from "../../../preview/SceneLayer";
import { drawPillCaption } from "./stageCaptions";
import { useMotionFonts } from "../../../preview/useMotionFonts";

const LONG_SIDE = 960;

interface StageLook {
  mode: ShotMode;
  shot: Shot;
  timing: ShotTiming;
  /** Loop length in seconds: the scene's duration on the timeline. */
  durS: number;
}

/** The video's caption pages and where the scene starts on the timeline, when captions are on. */
export interface StageCaptions {
  pages: readonly PillPage[];
  startS: number;
}

// The Shot view's large preview: the scene's picture looping over its duration, drawn over a still of
// its footage (which only shows through in cameo and footage modes), with the player's captions on top.
export default function ShotStage({ look, width, height, still, captions, env }: {
  look: StageLook;
  width: number;
  height: number;
  still: string | null;
  captions: StageCaptions | null;
  env: MotionEnv;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const fonts = useMotionFonts();
  const live = useRef({ look, width, height, captions });
  live.current = { look, width, height, captions };

  useEffect(() => {
    if (!fonts) return;
    const t0 = performance.now();
    let raf = 0;
    const tick = (now: number): void => {
      const ctx = ref.current?.getContext("2d");
      const { look: l, width: W, height: H, captions: c } = live.current;
      const lt = ((now - t0) / 1000) % Math.max(l.durS, 0.5);
      if (ctx) {
        paintScene(ctx, l, lt, W, H, env);
        if (c) drawPillCaption(ctx, c.pages, c.startS + lt, W, H, env.fontFamily ?? DEFAULT_FONT);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [env, fonts]);

  const scale = LONG_SIDE / Math.max(width, height);
  return (
    <div className="frame" data-mode={look.mode} style={{ "--ar": `${width}/${height}` } as CSSProperties}>
      {still && look.mode !== "animation" && (
        <video src={still} muted playsInline preload="auto" tabIndex={-1} aria-hidden="true" />
      )}
      <canvas
        ref={ref}
        width={Math.round(width * scale)}
        height={Math.round(height * scale)}
        role="img"
        aria-label="Shot preview"
      />
    </div>
  );
}
