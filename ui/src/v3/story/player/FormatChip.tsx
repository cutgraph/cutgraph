import type { Format } from "./format";

const ORDER: Format[] = ["16:9", "9:16", "1:1"];

/** The format after `format` in the cycle: 16:9 → 9:16 → 1:1 → 16:9. */
export const nextFormat = (format: Format): Format => ORDER[(ORDER.indexOf(format) + 1) % ORDER.length];

// One click on the format chip switches the frame to the next format.
export default function FormatChip({ format, onPick }: { format: Format; onPick: (next: Format) => void }) {
  return (
    <button type="button" className="chip" title="Format" onClick={() => onPick(nextFormat(format))}>
      {format}
    </button>
  );
}
