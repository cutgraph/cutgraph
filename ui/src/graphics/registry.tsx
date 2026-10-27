import type { ReactNode } from "react";
import { designScale } from "motion-core";
import { useFrame } from "../player";
import type { GraphicItem, TextItem } from "../types/project";
import { GRAPHIC_REGISTRY_CONTRACT, GRAPHIC_REGISTRY_GEOMETRY, GRAPHIC_REGISTRY_MOTION } from "./registry.data";

export type GraphicComponentId = GraphicItem["componentId"];
export type GraphicPropKind = "text" | "color" | "text-list" | "number-list";
export type GraphicProps =
  | { eyebrow: string; title: string; detail: string }
  | { label: string; body: string; accent: string }
  | { source: string; claim: string; detail: string }
  | { chapter: string; title: string; subtitle: string }
  | { title: string; labels: string[]; values: number[] };
export type TrustedGraphicItem = Omit<GraphicItem, "props"> & { props: GraphicProps };

export interface GraphicDefinition {
  id: GraphicComponentId;
  version: 1;
  name: string;
  purpose: string;
  requiredFields: readonly string[];
  propSchema: Readonly<Record<string, GraphicPropKind>>;
  defaults: GraphicProps;
  thumbnail: string;
  previewRenderer: "player:trusted-registry";
  finalRenderer: "pipeline.graphics";
  style: { foreground: string; border: string; background: string; foregroundProp?: string };
  layout: { width: number; height: number; layers: readonly GraphicLayer[] };
}

export interface GraphicGeometryLayer {
  kind: GraphicLayer["kind"];
  field?: string;
  x: number;
  y: number;
  width: number;
  height: number;
  fontPx?: number;
  barWidth?: number;
  gap?: number;
  bars?: readonly { x: number; y: number; width: number; height: number }[];
  baseline?: { x: number; y: number; width: number; height: number };
  lineHeightPx?: number;
  maxChars?: number;
}

export interface GraphicGeometry {
  box: { x: number; y: number; width: number; height: number };
  style: GraphicStyle & { backgroundAlpha: number; borderPx: number; paddingPx: number; cornerRadiusPx: number; chartBaselinePx: number };
  layers: readonly GraphicGeometryLayer[];
}

export type GraphicLayer =
  | { kind: "panel" | "rail"; x: number; y: number; width: number; height: number }
  | { kind: "text"; field: string; x: number; y: number; font: number }
  | { kind: "bars"; field: "values"; x: number; y: number; width: number; height: number }
  | { kind: "labels"; field: "labels"; x: number; y: number; font: number };

const DEFINITIONS = GRAPHIC_REGISTRY_CONTRACT as unknown as readonly GraphicDefinition[];

export const GRAPHIC_DEFINITIONS = DEFINITIONS;
export const GRAPHIC_REGISTRY: Readonly<Record<GraphicComponentId, GraphicDefinition>> = Object.fromEntries(
  DEFINITIONS.map((item) => [item.id, item]),
) as Record<GraphicComponentId, GraphicDefinition>;

export function graphicDefinition(id: string): GraphicDefinition | null {
  return GRAPHIC_REGISTRY[id as GraphicComponentId] ?? null;
}

export function defaultGraphicProps(id: GraphicComponentId): GraphicProps {
  return JSON.parse(JSON.stringify(GRAPHIC_REGISTRY[id].defaults)) as GraphicProps;
}

function safeText(value: unknown): value is string {
  return typeof value === "string" && !/:\/\//.test(value) && !/^(?:file|data|javascript):/i.test(value);
}

export interface GraphicStyle { foreground: string; border: string; background: string }

export function graphicStyle(item: TrustedGraphicItem): GraphicStyle {
  const spec = GRAPHIC_REGISTRY[item.componentId];
  const foreground = spec.style.foregroundProp
    ? String((item.props as Record<string, unknown>)[spec.style.foregroundProp])
    : spec.style.foreground;
  return { foreground, border: foreground, background: spec.style.background };
}

function roundPx(value: number): number { return Math.floor(value + 0.5); }

const PX_KEYS = new Set(["x", "y", "width", "height", "fontPx", "lineHeightPx", "barWidth", "gap",
  "borderPx", "paddingPx", "cornerRadiusPx", "chartBaselinePx"]);

/** A geometry descriptor with every pixel value scaled by `k`; counts, ratios and colours as they are. */
function scaledPx<T>(value: T, k: number): T {
  if (Array.isArray(value)) return value.map((v) => scaledPx(v, k)) as T;
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, v]) =>
      [key, PX_KEYS.has(key) ? roundPx((v as number) * k) : scaledPx(v, k)])) as T;
  }
  return value;
}

/** Concrete integer geometry shared with pipeline.graphics; no percentage/flex layout is involved. A
 * canvas larger than its 1080-class design (a 4K export) gets the design's geometry scaled up. */
export function graphicGeometry(item: TrustedGraphicItem, canvasWidth: number, canvasHeight: number): GraphicGeometry {
  const k = designScale(canvasWidth, canvasHeight);
  if (k > 1) return scaledPx(graphicGeometry(item, Math.round(canvasWidth / k), Math.round(canvasHeight / k)), k);
  const spec = GRAPHIC_REGISTRY[item.componentId];
  const scale = item.scale;
  const boxWidth = Math.max(1, roundPx(spec.layout.width * canvasWidth * scale));
  const boxHeight = Math.max(1, roundPx(spec.layout.height * canvasHeight * scale));
  const boxX = roundPx(item.pos.x * canvasWidth - boxWidth / 2);
  const boxY = roundPx(item.pos.y * canvasHeight - boxHeight / 2);
  const padding = GRAPHIC_REGISTRY_GEOMETRY.paddingPx;
  const props = item.props as unknown as Record<string, unknown>;
  const values = Array.isArray(props.values) ? props.values as number[] : undefined;
  const max = Math.max(...(values?.map((value) => Math.abs(Number(value))) ?? []), 1);
  const layers = spec.layout.layers.map((layer) => {
    const relX = roundPx(layer.x * boxWidth);
    const relY = roundPx(layer.y * boxHeight);
    const x = boxX + relX;
    const y = boxY + relY;
    const width = Math.max(1, "width" in layer ? roundPx(layer.width * boxWidth) : boxWidth - relX - padding);
    const fontPx = "font" in layer ? Math.max(12, roundPx(layer.font * boxHeight)) : undefined;
    const lineHeightPx = fontPx === undefined ? undefined : Math.max(1, roundPx(fontPx * GRAPHIC_REGISTRY_GEOMETRY.lineHeightRatio));
    const height = Math.max(1, "height" in layer ? roundPx(layer.height * boxHeight) : lineHeightPx ?? roundPx(12 * GRAPHIC_REGISTRY_GEOMETRY.lineHeightRatio));
    const maxChars = fontPx === undefined ? undefined : Math.max(1, Math.floor(width / Math.max(1, fontPx * GRAPHIC_REGISTRY_GEOMETRY.charWidthRatio)));
    const result: GraphicGeometryLayer = { kind: layer.kind, ...( "field" in layer ? { field: layer.field } : {} ), x, y, width, height, ...(fontPx === undefined ? {} : { fontPx }) };
    if (lineHeightPx !== undefined) result.lineHeightPx = lineHeightPx;
    if (maxChars !== undefined) result.maxChars = maxChars;
    if (layer.kind === "bars") {
      const count = values?.length ?? 0;
      const barWidth = Math.max(1, Math.floor(width / Math.max(count * 2, 1)));
      const gap = Math.max(1, Math.floor((width - barWidth * count) / Math.max(count + 1, 1)));
      result.barWidth = barWidth;
      result.gap = gap;
      result.baseline = { x, y: y + height, width, height: GRAPHIC_REGISTRY_GEOMETRY.chartBaselinePx };
      result.bars = (values ?? []).map((value, index) => {
        const barHeight = Math.max(4, roundPx(Math.abs(Number(value)) / max * height));
        return { x: x + gap + index * (barWidth + gap), y: y + height - barHeight, width: barWidth, height: barHeight };
      });
    }
    return result;
  });
  return { box: { x: boxX, y: boxY, width: boxWidth, height: boxHeight },
    style: { ...graphicStyle(item), backgroundAlpha: GRAPHIC_REGISTRY_GEOMETRY.backgroundAlpha,
      borderPx: GRAPHIC_REGISTRY_GEOMETRY.borderPx, paddingPx: padding,
      cornerRadiusPx: GRAPHIC_REGISTRY_GEOMETRY.cornerRadiusPx,
      chartBaselinePx: GRAPHIC_REGISTRY_GEOMETRY.chartBaselinePx }, layers };
}

export interface GraphicMotion { inF: number; outF: number; slidePx: number }

/** Entrance/exit timing shared with pipeline.graphics.graphic_motion; short spans split in half. */
export function graphicMotion(durF: number, canvasHeight: number): GraphicMotion {
  const half = Math.max(0, Math.floor(durF / 2));
  return { inF: Math.min(GRAPHIC_REGISTRY_MOTION.inF, half), outF: Math.min(GRAPHIC_REGISTRY_MOTION.outF, half),
    slidePx: roundPx(GRAPHIC_REGISTRY_MOTION.slide * canvasHeight) };
}

function clamp01(value: number): number { return Math.max(0, Math.min(1, value)); }

/**
 * Opacity and downward offset `frame` frames into a `durF` span. Mirrors the compiled graph: the
 * alpha fades are ffmpeg's time-based `fade` factors and the offset is the overlay's y expression.
 */
export function graphicMotionAt(frame: number, durF: number, canvasHeight: number): { opacity: number; dy: number } {
  const { inF, outF, slidePx } = graphicMotion(durF, canvasHeight);
  const entered = inF ? clamp01(frame / inF) : 1;
  const leaving = outF ? clamp01((frame - (durF - outF)) / outF) : 0;
  return { opacity: entered * (1 - leaving), dy: slidePx * ((1 - entered) ** 2 + leaving ** 2) };
}

/** A registry graphic inside its own player <FrameWindow>, fading and sliding in and out. */
export function AnimatedGraphic({ item, geometry, canvasHeight }: { item: TrustedGraphicItem; geometry: GraphicGeometry; canvasHeight: number }) {
  const { opacity, dy } = graphicMotionAt(useFrame(), item.durF, canvasHeight);
  return <div style={{ width: "100%", height: "100%", opacity, transform: dy ? `translateY(${dy}px)` : undefined }}>
    {renderGraphic(item, geometry)}
  </div>;
}

export function validateGraphicProps(id: string, value: unknown): value is GraphicProps {
  const spec = graphicDefinition(id);
  if (!spec || !value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  if (Object.keys(record).length !== spec.requiredFields.length || !spec.requiredFields.every((field) => field in record)) return false;
  if (id === "simple-chart") {
    return safeText(record.title) && Array.isArray(record.labels) && Array.isArray(record.values)
      && record.labels.length > 0 && record.labels.length === record.values.length && record.labels.length <= 12
      && record.labels.every(safeText) && record.values.every((n) => typeof n === "number" && Number.isFinite(n));
  }
  if (id === "callout" && (typeof record.accent !== "string" || !/^#[0-9a-f]{6}$/i.test(record.accent))) return false;
  return Object.values(record).every(safeText);
}

export function isGraphicItem(item: TextItem | GraphicItem): item is GraphicItem {
  return "type" in item && item.type === "graphic" && item.componentVersion === 1
    && validateGraphicProps(item.componentId, item.props);
}

/** The only preview renderer entry point. It consumes declarative trusted layers, never project code. */
function rgba(hex: string, alpha: number): string {
  const value = hex.slice(1);
  return `rgba(${parseInt(value.slice(0, 2), 16)},${parseInt(value.slice(2, 4), 16)},${parseInt(value.slice(4, 6), 16)},${alpha})`;
}

function fitGraphicText(value: string, maxChars: number): string {
  if (value.length <= maxChars) return value;
  if (maxChars <= 1) return "…";
  return `${value.slice(0, maxChars - 1)}…`;
}

export function renderGraphic(item: TrustedGraphicItem, geometry = graphicGeometry(item, 1920, 1080)): ReactNode {
  const style = graphicStyle(item);
  const props = item.props as unknown as Record<string, unknown>;
  return <div className={`graphic-render graphic-${item.componentId}`} style={{ position: "relative", overflow: "hidden", boxSizing: "border-box", fontFamily: '"Liberation Sans"', color: style.foreground, width: `${geometry.box.width}px`, height: `${geometry.box.height}px`, padding: 0, border: 0, boxShadow: "none" }}>
    {geometry.layers.map((layer, index) => {
      // Positioning is inline so the renderer never depends on which shell's stylesheet is loaded.
      const base = { position: "absolute" as const, boxSizing: "border-box" as const, overflow: "hidden", zIndex: layer.kind === "panel" || layer.kind === "rail" ? undefined : 2, left: `${layer.x - geometry.box.x}px`, top: `${layer.y - geometry.box.y}px`, width: `${layer.width}px`, height: `${layer.height}px` };
      if (layer.kind === "panel") return <span aria-hidden="true" className="graphic-layer graphic-panel" style={{ ...base, border: `${geometry.style.borderPx}px solid ${style.border}`, borderRadius: `${geometry.style.cornerRadiusPx}px`, backgroundColor: rgba(style.background, geometry.style.backgroundAlpha) }} key={index} />;
      if (layer.kind === "rail") return <span aria-hidden="true" className="graphic-layer graphic-rail" style={{ ...base, backgroundColor: style.border }} key={index} />;
      if (layer.kind === "bars") return <span className="graphic-layer graphic-bars" style={base} key={index}>{(layer.bars ?? []).map((bar, barIndex) => <i key={barIndex} title={`${(props.labels as string[])[barIndex]}: ${(props.values as number[])[barIndex]}`} style={{ position: "absolute", left: `${bar.x - layer.x}px`, top: `${bar.y - layer.y}px`, width: `${bar.width}px`, height: `${bar.height}px`, backgroundColor: style.foreground }} />)}{layer.baseline && <i aria-hidden="true" className="graphic-baseline" style={{ position: "absolute", left: `${layer.baseline.x - layer.x}px`, top: `${layer.baseline.y - layer.y}px`, width: `${layer.baseline.width}px`, height: `${layer.baseline.height}px`, backgroundColor: style.foreground }} />}</span>;
      if (layer.kind === "labels") return <small className="graphic-layer graphic-labels" style={{ ...base, fontSize: `${layer.fontPx}px`, lineHeight: `${layer.lineHeightPx}px`, textAlign: "left", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "clip" }} key={index}>{fitGraphicText((props.labels as string[]).join(" · "), layer.maxChars ?? 1)}</small>;
      if (layer.kind !== "text") return null;
      const field = layer.field ?? "";
      const value = props[field];
      return value ? <span className={`graphic-layer graphic-text graphic-text-${field}`} style={{ ...base, display: "block", fontWeight: 700, fontSize: `${layer.fontPx}px`, lineHeight: `${layer.lineHeightPx}px`, textAlign: "left", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "clip" }} key={index}>{fitGraphicText(String(value), layer.maxChars ?? 1)}</span> : null;
    })}
  </div>;
}

export function graphicLines(item: TrustedGraphicItem): string[] {
  switch (item.componentId) {
    case "lower-third": { const p = item.props as { eyebrow: string; title: string; detail: string }; return [p.eyebrow, p.title, p.detail].filter(Boolean); }
    case "callout": { const p = item.props as { label: string; body: string; accent: string }; return [p.label, p.body]; }
    case "evidence-card": { const p = item.props as { source: string; claim: string; detail: string }; return [p.source, p.claim, p.detail].filter(Boolean); }
    case "chapter-card": { const p = item.props as { chapter: string; title: string; subtitle: string }; return [p.chapter, p.title, p.subtitle].filter(Boolean); }
    case "simple-chart": { const p = item.props as { title: string; labels: string[]; values: number[] }; return [p.title, p.labels.map((label, i) => `${label}: ${p.values[i]}`).join("  ")]; }
  }
}
