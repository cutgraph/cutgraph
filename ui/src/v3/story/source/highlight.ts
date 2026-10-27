// Light syntax colouring for script.mdx: frontmatter (k), scene headings (h),
// struck cut runs (x), strings and braces (s), and component tag names (t). A cut run may wrap lines.

export interface Segment {
  text: string;
  cls?: "k" | "h" | "x" | "s" | "t";
}

const FRONTMATTER = /^---\n[\s\S]*?\n---(?:\n|$)/;
const BODY = /(^## [^{\n]*)|(~~[\s\S]+?~~)|("[^"\n]*"|\{[^}\n]*\})|(<\/?)([A-Z]\w*)/gm;

export function highlightMdx(text: string): Segment[] {
  const out: Segment[] = [];
  const fm = FRONTMATTER.exec(text);
  if (fm) out.push({ text: fm[0], cls: "k" });
  const body = fm ? text.slice(fm[0].length) : text;
  let last = 0;
  for (const m of body.matchAll(BODY)) {
    const at = m.index ?? 0;
    if (at > last) out.push({ text: body.slice(last, at) });
    if (m[1]) out.push({ text: m[1], cls: "h" });
    else if (m[2]) out.push({ text: m[2], cls: "x" });
    else if (m[3]) out.push({ text: m[3], cls: "s" });
    else out.push({ text: m[4] }, { text: m[5], cls: "t" });
    last = at + m[0].length;
  }
  if (last < body.length) out.push({ text: body.slice(last) });
  return out;
}
