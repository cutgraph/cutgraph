import type { CSSProperties, HTMLAttributes, ImgHTMLAttributes } from "react";

export const fillStyle: CSSProperties = {
  position: "absolute", inset: 0, width: "100%", height: "100%", display: "flex", flexDirection: "column",
};

export type LayerProps = HTMLAttributes<HTMLDivElement>;

export function Layer({ style, ...props }: LayerProps) {
  return <div {...props} style={{ ...fillStyle, ...style }} />;
}

export type FrameImageProps = ImgHTMLAttributes<HTMLImageElement>;

export function FrameImage(props: FrameImageProps) {
  return <img {...props} />;
}
