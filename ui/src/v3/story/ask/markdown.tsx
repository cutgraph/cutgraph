import { Fragment, type ReactNode } from "react";

// A safe subset of the co-pilot's markdown replies: bold, italic, inline code, and
// paragraphs/lists. This builds React nodes straight from
// the text — never dangerouslySetInnerHTML — so nothing the model writes, including literal HTML, is
// ever parsed as markup; it always renders as plain text.
const INLINE_RE = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|_[^_\n]+_)/g;

function inline(text: string, keyBase: string): ReactNode[] {
  return text.split(INLINE_RE).filter((part) => part !== "").map((part, i) => {
    const key = `${keyBase}:${i}`;
    if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) return <code key={key}>{part.slice(1, -1)}</code>;
    if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) return <strong key={key}>{part.slice(2, -2)}</strong>;
    if (part.length > 2 && ((part.startsWith("*") && part.endsWith("*")) || (part.startsWith("_") && part.endsWith("_")))) {
      return <em key={key}>{part.slice(1, -1)}</em>;
    }
    return part;
  });
}

type Block = { kind: "list"; items: string[] } | { kind: "p"; lines: string[] };

const isListLine = (line: string): boolean => /^[-*]\s+/.test(line);

// Line by line, not paragraph by paragraph: a "- " run starts a list block even with no blank line
// before it (the model's own style, e.g. "Here's what I found:\n- um at word 4"), and a blank line
// (or the list/prose boundary itself) ends whichever block is open.
function blocksOf(text: string): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  let items: string[] = [];
  const flushPara = (): void => { if (para.length) { blocks.push({ kind: "p", lines: para }); para = []; } };
  const flushList = (): void => { if (items.length) { blocks.push({ kind: "list", items }); items = []; } };
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) {
      flushPara();
      flushList();
    } else if (isListLine(line)) {
      flushPara();
      items.push(line.replace(/^[-*]\s+/, ""));
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return blocks;
}

export function renderMarkdown(text: string): ReactNode {
  const blocks = blocksOf(text);
  if (!blocks.length) return null;
  return blocks.map((block, bi) =>
    block.kind === "list" ? (
      <ul key={bi}>
        {block.items.map((item, ii) => <li key={ii}>{inline(item, `${bi}:${ii}`)}</li>)}
      </ul>
    ) : (
      <p key={bi}>
        {block.lines.map((line, li) => (
          <Fragment key={li}>
            {li > 0 && <br />}
            {inline(line, `${bi}:${li}`)}
          </Fragment>
        ))}
      </p>
    ),
  );
}
