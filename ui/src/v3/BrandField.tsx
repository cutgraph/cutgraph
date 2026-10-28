// The Workspace brand supplies the eyebrow and accent for animated headlines.
// A shot can change or hide the eyebrow in the Shot view.
import { useEffect } from "react";
import type { Brand, BrandPatch } from "./api";
import { useDraft } from "./common";
import "./brand.css";

const HEX = /^#[0-9a-f]{6}$/i;
const SAVE_ACCENT_MS = 400;

export default function BrandField({ brand, editable, onSave }: {
  brand: Brand;
  editable: boolean;
  onSave: (patch: BrandPatch) => Promise<boolean>;
}) {
  const [name, setName] = useDraft(brand.name);
  const [accent, setAccent] = useDraft(brand.accent);

  useEffect(() => {
    if (accent === brand.accent || !HEX.test(accent)) return;
    const t = window.setTimeout(() => void onSave({ accent }).then((ok) => ok || setAccent(brand.accent)), SAVE_ACCENT_MS);
    return () => window.clearTimeout(t);
  }, [accent, brand.accent, onSave]);

  const commitName = () => {
    const next = name.trim();
    if (next === brand.name) setName(brand.name);
    else void onSave({ name: next || null }).then((ok) => ok || setName(brand.name));
  };

  return (
    <div className="brand-kit">
      <div className="brand-fields">
        <label className="brand-name">
          <span>Eyebrow</span>
          <input
            aria-label="Brand name"
            value={name}
            maxLength={40}
            spellCheck={false}
            disabled={!editable}
            onChange={(e) => setName(e.target.value)}
            onBlur={commitName}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
              if (e.key === "Escape") setName(brand.name);
            }}
          />
        </label>
        <label className="brand-accent">
          <span>Accent</span>
          <span className="brand-swatch">
            <input type="color" aria-label="Brand accent" value={accent} disabled={!editable} onChange={(e) => setAccent(e.target.value)} />
            <code>{accent}</code>
          </span>
        </label>
      </div>
      <div className="brand-sample" aria-label="Headline preview">
        <b style={{ color: accent }}>{name.trim() || "—"}</b>
        <span>Your animated headline</span>
      </div>
    </div>
  );
}
