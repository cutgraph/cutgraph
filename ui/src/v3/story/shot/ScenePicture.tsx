// The animated part of a scene's thumbnail: painted once over the footage still, so an Animation
// scene shows its shot and a Cameo shows the default rig on the footage, as they look in the player.
import { useLayoutEffect, useRef } from "react";
import { domEnv, paintScene, type SceneRenderSpec } from "../../../preview/SceneLayer";
import { useSceneImagesVersion } from "../../../preview/sceneImages";
import { useMotionFonts } from "../../../preview/useMotionFonts";

const THUMB_ENV = domEnv();
const THUMB_W = 480;

/** A moment that shows the shot settled: its headline and diagram have landed, but not past the scene's end. */
export function stillTime(spec: SceneRenderSpec, fps: number): number {
  const end = Math.max(0, spec.durF / fps - 0.1);
  return Math.min(Math.max(spec.timing.firstT ?? 0, spec.timing.cueT ?? 0) + 1.2, end);
}

export default function ScenePicture({ spec, width, height, fps }: { spec: SceneRenderSpec; width: number; height: number; fps: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const images = useSceneImagesVersion();
  const fonts = useMotionFonts();
  const h = Math.round((THUMB_W * height) / width);
  useLayoutEffect(() => {
    const ctx = ref.current?.getContext("2d");
    if (ctx && fonts) paintScene(ctx, spec, stillTime(spec, fps), width, height, THUMB_ENV);
  }, [spec, width, height, fps, images, fonts]);
  return <canvas ref={ref} className="scene-picture" width={THUMB_W} height={h} aria-hidden="true" data-mode={spec.mode} />;
}
