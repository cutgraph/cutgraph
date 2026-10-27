import { useMemo } from "react";
import { toast } from "../../Toast";
import { assetLabels, useDrawerLibrary } from "../drawer/library";
import { highlightMdx } from "../source/highlight";
import { scriptMdx } from "../source/mdx";
import { useStoryModel } from "../useStoryModel";
import "./SourceLens.css";

export default function SourceLens() {
  const { project, media, words, cut, spans } = useStoryModel();
  const { assets } = useDrawerLibrary(project?.workspaceId);
  const labels = useMemo(() => assetLabels(assets), [assets]);
  const text = useMemo(
    () => (project ? scriptMdx({ project, media, words, cut, spans, labels }) : ""),
    [project, media, words, cut, spans, labels],
  );
  const segments = useMemo(() => highlightMdx(text), [text]);

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied script.mdx");
    } catch {
      toast("Couldn't copy. Select the text and copy it instead.");
    }
  }

  return (
    <div className="source" data-slot="source">
      <div className="source-head">
        <p className="source-note">
          <b>script.mdx</b> is this video as text: its scenes in story order, every word with cuts struck through, and
          the cues pinned to them. Word timings live beside it in <b>words.json</b>.
        </p>
        <button type="button" className="ghost" onClick={() => void copy()}>
          Copy
        </button>
      </div>
      <pre className="mdx" aria-label="script.mdx">
        {segments.map((s, i) => (s.cls ? <span key={i} className={s.cls}>{s.text}</span> : s.text))}
      </pre>
    </div>
  );
}
