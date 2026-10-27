// Animated and cameo scenes use one composition-sized canvas per scene, drawn
// by motion-core from the player frame. It sits above the footage and B-roll and below text,
// graphics and captions, the same z-order the export uses.
import { useLayoutEffect, useRef } from "react";
import { FrameWindow, useFrame } from "../player";
import { drawSceneFrame, type Ctx, type MotionEnv, type Shot, type ShotMode, type ShotTiming } from "motion-core";
import { sceneImage, useSceneImagesVersion } from "./sceneImages";
import { useMotionFonts } from "./useMotionFonts";

/** One scene's picture on the timeline, as the scene render spec rule computes it. */
export interface SceneRenderSpec {
  sceneId: string;
  mode: ShotMode;
  shot: Shot;
  timing: ShotTiming;
  startF: number;
  durF: number;
}

export function domEnv(): MotionEnv {
  return {
    createCanvas: (width, height) => Object.assign(document.createElement("canvas"), { width, height }),
    fontFamily: '"Inter"',
    image: sceneImage,
  };
}

const PLAYER_ENV = domEnv();

/** Clear the canvas, then draw the scene at scene-local time `lt` in W x H composition space. */
export function paintScene(ctx: Ctx, spec: Pick<SceneRenderSpec, "mode" | "shot" | "timing">, lt: number, W: number, H: number, env: MotionEnv): void {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  ctx.setTransform(ctx.canvas.width / W, 0, 0, ctx.canvas.height / H, 0, 0);
  drawSceneFrame(ctx, spec.mode, spec.shot, lt, spec.timing, W, H, env);
}

function SceneCanvas({ spec, width, height, fps }: { spec: SceneRenderSpec; width: number; height: number; fps: number }) {
  const frame = useFrame();
  const images = useSceneImagesVersion();
  const fonts = useMotionFonts();
  const ref = useRef<HTMLCanvasElement>(null);
  useLayoutEffect(() => {
    const ctx = ref.current?.getContext("2d");
    if (ctx && fonts) paintScene(ctx, spec, frame / fps, width, height, PLAYER_ENV);
  }, [frame, spec, width, height, fps, images, fonts]);
  return (
    <canvas
      ref={ref}
      width={width}
      height={height}
      data-scene-layer={spec.sceneId}
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}
    />
  );
}

export function SceneLayer({ scenes, width, height, fps }: { scenes: SceneRenderSpec[]; width: number; height: number; fps: number }) {
  return (
    <>
      {scenes.map((spec) => (
        <FrameWindow key={spec.sceneId} startFrame={spec.startF} frameCount={spec.durF} label={`Scene ${spec.sceneId}`}>
          <SceneCanvas spec={spec} width={width} height={height} fps={fps} />
        </FrameWindow>
      ))}
    </>
  );
}
