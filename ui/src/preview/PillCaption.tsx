import { ACCENT } from "motion-core";
// Pill captions: one line of bold Inter on a rounded
// dark box near the bottom; the spoken word yellow, spoken words white, upcoming words dimmed. Paged
// and timed by the rule the export burns (captionPages.ts ⇄ pipeline/caption_pages.py), and laid out
// inline so it renders the same in any host that mounts the composition.
import { Fragment, useMemo } from "react";
import { useFrame } from "../player";
import type { TranscriptWord } from "../api";
import type { Project } from "../types/project";
import { captionLayout, captionPages, pageAt, wordStates, type WordState } from "./captionPages";

// Inter's ascent and descent (em). A CSS line box centres ascent + descent, so this line height
// puts the baseline 1.1·fs below the box top, matching the ASS export.
const ASC = 1984 / 2048;
const DESC = 494 / 2048;

const COLOR: Record<WordState, string> = { C: ACCENT, P: "#f4f6f7", U: "rgba(244,246,247,.5)" };
const CLASS: Record<WordState, string> = { C: "caption-word-active", P: "caption-word-past", U: "caption-word-future" };

export function PillCaption({ project, words }: { project: Project; words: TranscriptWord[] }) {
  const { width, height, fps } = project;
  const pages = useMemo(() => captionPages(project, words, project.width, project.height), [project, words]);
  const t = useFrame() * 100 / fps;
  const page = pageAt(pages, t);
  if (!page) return null;
  const states = wordStates(page, t);
  const { capY, fs } = captionLayout(width, height);
  const lineHeight = (2.2 - ASC + DESC) * fs;
  return <div
    className="caption-overlay caption-pill"
    data-caption-word={page.words.find((_w, i) => states[i] === "C")?.w ?? ""}
    aria-label={`Caption: ${page.words.map((w) => w.w).join(" ")}`}
    style={{
      position: "absolute", zIndex: 5, left: 0, right: 0, top: capY - 1.1 * fs,
      display: "flex", justifyContent: "center", pointerEvents: "none",
    }}
  >
    <div style={{
      boxSizing: "border-box", height: 1.6 * fs, padding: `0 ${0.6 * fs}px ${1.6 * fs - lineHeight}px`,
      borderRadius: 0.35 * fs, background: "rgba(8,10,12,.72)",
      fontFamily: '"Inter"', fontWeight: 700, fontSize: fs, lineHeight: `${lineHeight}px`,
      // libass burns the export unkerned; matching it keeps the line (and its box) the same width.
      fontKerning: "none", whiteSpace: "nowrap",
    }}>
      {page.words.map((w, i) => <Fragment key={i}>
        {i > 0 ? " " : null}
        <span className={`caption-word ${CLASS[states[i]]}`} style={{ color: COLOR[states[i]] }}>{w.w}</span>
      </Fragment>)}
    </div>
  </div>;
}
