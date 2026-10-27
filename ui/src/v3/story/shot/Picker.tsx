import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import type { Ctx } from "motion-core";
import { useMotionFonts } from "../../../preview/useMotionFonts";

/** Paints a tile at time `t`; null is the resting frame. */
export type TilePaint = (ctx: Ctx, width: number, height: number, t: number | null) => void;

export interface TileOption {
  id: string;
  label: string;
}

/**
 * A live motion-core canvas: painted once (t = null) whenever `look` changes, and every animation
 * frame while `live`, with t counting from when it went live.
 */
export function useTileCanvas(paint: TilePaint, look: string, live: boolean): RefObject<HTMLCanvasElement | null> {
  const ref = useRef<HTMLCanvasElement>(null);
  const fonts = useMotionFonts();
  const paintRef = useRef(paint);
  paintRef.current = paint;

  useEffect(() => {
    if (!fonts) return;
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    if (!live) {
      paintRef.current(ctx, canvas.width, canvas.height, null);
      return;
    }
    const t0 = performance.now();
    let raf = 0;
    const tick = (now: number): void => {
      paintRef.current(ctx, canvas.width, canvas.height, (now - t0) / 1000);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [live, look, fonts]);
  return ref;
}

// A picker tile: painted once whenever its look changes, and animated only while hovered.
function Tile({ label, on, wide, look, paint, onPick }: {
  label: string;
  on: boolean;
  wide: boolean;
  look: string;
  paint: TilePaint;
  onPick: () => void;
}) {
  const [hover, setHover] = useState(false);
  const ref = useTileCanvas(paint, look, hover);

  return (
    <button
      type="button"
      className={`atile${wide ? " wide" : ""}${on ? " on" : ""}`}
      aria-pressed={on}
      title={label}
      onClick={onPick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <canvas ref={ref} width={192} height={wide ? 108 : 208} aria-hidden="true" />
      <span>{label}</span>
    </button>
  );
}

/** One picker row: a horizontal strip of tiles, the selected one highlighted. */
export function Strip({ title, action, options, selected, wide = false, look, paint, onPick }: {
  title: string;
  /** A control beside the title, e.g. removing what the row picks for. */
  action?: ReactNode;
  options: TileOption[];
  selected: string;
  wide?: boolean;
  /** Changes whenever the tiles' shared look changes, so they repaint. */
  look: string;
  paint: (id: string) => TilePaint;
  onPick: (id: string) => void;
}) {
  return (
    <div className="rrow" data-picker={title.toLowerCase()}>
      {action ? <div className="rhead"><h4>{title}</h4>{action}</div> : <h4>{title}</h4>}
      <div className="strip" role="group" aria-label={title}>
        {options.map((o) => (
          <Tile key={o.id} label={o.label} on={o.id === selected} wide={wide} look={look} paint={paint(o.id)} onPick={() => onPick(o.id)} />
        ))}
      </div>
    </div>
  );
}
