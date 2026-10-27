import { useMemo, type CSSProperties } from "react";
import { setPlayhead, useProjectStore } from "../../store/useProjectStore";
import { arcLayout, arcPlayhead, type ArcLayout } from "./arc/layout";
import { roleColor } from "./arc/roles";
import { setCaret } from "./storyStore";
import { useStoryModel } from "./useStoryModel";
import "./ArcStrip.css";

type Vars = CSSProperties & Record<`--${string}`, string | number>;

// The only part of the arc that re-renders on every playhead tick.
function ArcHead({ layout }: { layout: ArcLayout }) {
  const playheadF = useProjectStore((s) => s.playheadF);
  const at = arcPlayhead(layout, playheadF);
  if (!at) return null;
  return <i className="arc-head" style={{ "--i": at.index, "--at": at.at } as Vars} />;
}

export default function ArcStrip() {
  const { spans } = useStoryModel();
  const layout = useMemo(() => arcLayout(spans), [spans]);
  const current = useProjectStore((s) => arcPlayhead(layout, s.playheadF)?.index ?? -1);
  return (
    <div className="arc" data-slot="arc">
      {layout.blocks.length > 0 && (
        <div className="arc-in" style={{ "--n": layout.blocks.length } as Vars}>
          {layout.blocks.map(({ span, left, width }, i) => (
            <button
              key={span.scene.id}
              type="button"
              className={`arc-seg${i === current ? " on" : ""}${width < 0.04 ? " thin" : ""}`}
              data-scene={span.scene.id}
              title={`${span.scene.role} · ${span.scene.title}`}
              style={{ "--c": roleColor(span.scene.role), "--i": i, "--l": left, "--w": width } as Vars}
              onClick={() => {
                setPlayhead(span.tlStartF ?? 0);
                setCaret(span.startWord);
              }}
            >
              <b>{span.scene.role}</b>
              <span>{span.scene.title}</span>
            </button>
          ))}
          <ArcHead layout={layout} />
        </div>
      )}
    </div>
  );
}
