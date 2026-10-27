import { useEffect, useState } from "react";

const FACES = [400, 600, 700, 900].map((weight) => `${weight} 16px "Inter"`);

/** Canvas measurements must use the bundled faces even on the first paused frame. A face that fails to load
 * still lets the canvas paint (with a fallback font) rather than leave it blank. */
export function useMotionFonts(): boolean {
  const [ready, setReady] = useState(() => !document.fonts);
  useEffect(() => {
    if (!document.fonts) return;
    let active = true;
    Promise.all(FACES.map((face) => document.fonts.load(face)))
      .then((loaded) => {
        const missing = FACES.filter((_, i) => loaded[i].length === 0);
        if (missing.length) console.error("Scene fonts are not declared", missing);
      })
      .catch((error: unknown) => console.error("Could not load scene fonts", error))
      .finally(() => {
        if (active) setReady(true);
      });
    return () => { active = false; };
  }, []);
  return ready;
}
