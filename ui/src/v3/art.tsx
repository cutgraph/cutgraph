// Start-card art uses motion-core's character rig on a canvas that repaints every frame,
// driven by the Shot view's live-canvas hook.
import { drawCharacter, type Ctx } from "motion-core";
import { domEnv } from "../preview/SceneLayer";
import { CHARACTER_REST_T } from "./story/shot/paint";
import { useTileCanvas } from "./story/shot/Picker";

const ENV = domEnv();
const W = 224, H = 126; // twice the compact start button's art, sharp on a HiDPI screen

function paintIdeaArt(ctx: Ctx, w: number, h: number, t: number | null): void {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = "#1a2027";
  ctx.fillRect(0, 0, w, h);
  const cx = w * 0.5, cy = h * 0.17, r = h * 0.42;
  const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
  glow.addColorStop(0, "rgba(255, 227, 114, .4)");
  glow.addColorStop(1, "rgba(255, 227, 114, 0)");
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#11151a";
  ctx.beginPath();
  ctx.ellipse(w * 0.5, h * 0.86, w * 0.12, h * 0.03, 0, 0, Math.PI * 2);
  ctx.fill();
  drawCharacter(ctx, w * 0.5, h * 0.85, h * 0.76, { character: "lens", pose: "hold", expression: "idea", move: "hop", t: t ?? CHARACTER_REST_T }, ENV);
}

function paintFootageArt(ctx: Ctx, w: number, h: number, t: number | null): void {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  const room = ctx.createLinearGradient(0, 0, 0, h);
  room.addColorStop(0, "#2b3139");
  room.addColorStop(1, "#1d2228");
  ctx.fillStyle = room;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#11151a";
  ctx.beginPath();
  ctx.ellipse(w * 0.34, h * 0.9, w * 0.12, h * 0.03, 0, 0, Math.PI * 2);
  ctx.fill();
  drawCharacter(ctx, w * 0.34, h * 0.89, h * 0.7, { character: "lens", pose: "wave", expression: "happy", move: "walk", t: t ?? CHARACTER_REST_T }, ENV);
}

export function IdeaArt() {
  const ref = useTileCanvas(paintIdeaArt, "idea", true);
  return <canvas ref={ref} width={W} height={H} aria-hidden="true" />;
}

export function FootageArt() {
  const ref = useTileCanvas(paintFootageArt, "footage", true);
  return <canvas ref={ref} width={W} height={H} aria-hidden="true" />;
}
