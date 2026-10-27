// The first-run tour is shown once per browser when Story opens. It pauses while
// the Shot view or Export sheet is open and resumes at the same step when it closes.
import { useEffect, useState } from "react";
import "./coach.css";

const STEPS: readonly [string, string][] = [
  ["#story-pane", "This is your script. The whole video lives here: click any word to jump there, select words to cut them or add B-roll, graphics and music."],
  [".player", "Your video, always in sync with the script. Press Space to play."],
  [".askform", "Ask the Assistant for anything, like “cut filler words”. Changes arrive as suggestions you accept."],
];

const KEY = "cg3-coach";

function seenCoach(): boolean {
  try {
    return localStorage.getItem(KEY) === "done";
  } catch {
    return false;
  }
}

function endCoach(): void {
  try {
    localStorage.setItem(KEY, "done");
  } catch {
    // private mode: the tour just replays next visit
  }
}

const MAX_LOOKS = 240; // ~4s at 60fps: the project doc (and so #story-pane) may still be loading

export default function Coach({ active }: { active: boolean }) {
  const [step, setStep] = useState(-1);
  const [rect, setRect] = useState<DOMRect | null>(null);

  useEffect(() => {
    if (!active || seenCoach()) return;
    const t = setTimeout(() => {
      if (!seenCoach()) setStep((s) => Math.max(s, 0));
    }, 350);
    return () => clearTimeout(t);
  }, [active]);

  // The target may not exist yet (the project is still loading, or a lens swap remounts it), so
  // this polls a few animation frames rather than giving up on the first miss.
  useEffect(() => {
    const entry = STEPS[step];
    if (!entry || !active) {
      setRect(null);
      return;
    }
    const [selector] = entry;
    let raf = 0;
    let tries = 0;
    const look = (): void => {
      const target = document.querySelector(selector);
      if (target) {
        setRect(target.getBoundingClientRect());
        return;
      }
      if (tries++ < MAX_LOOKS) raf = requestAnimationFrame(look);
    };
    look();
    return () => cancelAnimationFrame(raf);
  }, [step, active]);

  const entry = STEPS[step];
  if (!active || !entry || !rect) return null;
  const [selector, message] = entry;
  const top = Math.max(12, Math.min(selector === ".askform" ? rect.top - 150 : rect.top + 24, window.innerHeight - 160));
  const left = Math.max(12, Math.min(rect.left + 24, window.innerWidth - 312));

  const finish = (): void => {
    setStep(-1);
    endCoach();
  };
  const next = (): void => (step === STEPS.length - 1 ? finish() : setStep(step + 1));

  return (
    <div className="coach" role="dialog" aria-label="Guided tour" style={{ top, left }}>
      <p>{message}</p>
      <div>
        <button type="button" className="ghost" onClick={finish}>Skip</button>
        <button type="button" className="primary" onClick={next}>{step === STEPS.length - 1 ? "Got it" : "Next"}</button>
      </div>
      <small>{step + 1} / {STEPS.length}</small>
    </div>
  );
}
