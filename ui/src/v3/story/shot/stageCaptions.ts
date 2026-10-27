import { ACCENT } from "motion-core";
// Captions on the Shot stage: the player's pill caption (preview/PillCaption.tsx), paged and
// timed by the same rule (captionPages.ts), drawn onto the stage canvas so it scales with the frame.
import type { Ctx } from "motion-core";
import { captionLayout, pageAt, wordStates, type PillPage, type WordState } from "../../../preview/captionPages";

const COLOR: Record<WordState, string> = { C: ACCENT, P: "#f4f6f7", U: "rgba(244,246,247,.5)" };

/** The page on screen at timeline second `t`, in W x H composition space (the canvas scaled to it). */
export function drawPillCaption(ctx: Ctx, pages: readonly PillPage[], t: number, W: number, H: number, fontFamily: string): void {
  const cs = t * 100;
  const page = pageAt(pages, cs);
  if (!page) return;
  const states = wordStates(page, cs);
  const { capY, fs } = captionLayout(W, H);
  ctx.save();
  ctx.setTransform(ctx.canvas.width / W, 0, 0, ctx.canvas.height / H, 0, 0);
  ctx.font = `700 ${fs}px ${fontFamily}`;
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";
  ctx.fontKerning = "none";
  const space = ctx.measureText(" ").width;
  const widths = page.words.map((w) => ctx.measureText(w.w).width);
  const total = widths.reduce((a, b) => a + b, 0) + space * (widths.length - 1);
  let x = (W - total) / 2;
  ctx.beginPath();
  ctx.roundRect(x - fs * 0.6, capY - fs * 1.1, total + fs * 1.2, fs * 1.6, fs * 0.35);
  ctx.fillStyle = "rgba(8,10,12,.72)";
  ctx.fill();
  page.words.forEach((w, i) => {
    ctx.fillStyle = COLOR[states[i]];
    ctx.fillText(w.w, x, capY);
    x += widths[i] + space;
  });
  ctx.restore();
}
