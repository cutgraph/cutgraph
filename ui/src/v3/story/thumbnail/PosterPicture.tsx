// A thumbnail drawn in the browser by motion-core's drawPoster, the same engine and spec the
// studio's PNG render uses: the Export sheet's preview, the editor's stage and a video's card cover.
import { useLayoutEffect, useRef } from "react";
import { POSTER_FORMATS, drawPoster, type Poster, type PosterFormat } from "motion-core";
import { domEnv } from "../../../preview/SceneLayer";
import { useMotionFonts } from "../../../preview/useMotionFonts";
import "./thumbnail.css";

const ENV = domEnv();

/** Canvas pixels `width` wide (and `height` tall, the format's shape when absent); CSS sizes it. */
export default function PosterPicture({ poster, format, width, height: tall, label }: {
  poster: Poster;
  format: PosterFormat;
  width: number;
  height?: number;
  label?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const fonts = useMotionFonts();
  const [W, H] = POSTER_FORMATS[format];
  const height = tall ?? Math.round((width * H) / W);
  useLayoutEffect(() => {
    const ctx = ref.current?.getContext("2d");
    if (!ctx || !fonts) return;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.setTransform(width / W, 0, 0, height / H, 0, 0);
    drawPoster(ctx, poster, W, H, ENV);
  }, [poster, W, H, width, height, fonts]);
  return (
    <canvas ref={ref} className="poster-picture" width={width} height={height} data-format={format}
      {...(label ? { role: "img", "aria-label": label } : { "aria-hidden": true })} />
  );
}
