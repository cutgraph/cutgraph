import type { AssetAttribution } from "./api";
import "./stock-attribution.css";

function httpsLink(value: string | null | undefined): string | undefined {
  try {
    return value && new URL(value).protocol === "https:" ? value : undefined;
  } catch {
    return undefined;
  }
}

export default function StockAttribution({ attribution, kind }: {
  attribution: AssetAttribution;
  kind: "photo" | "video";
}) {
  const provider = attribution.provider === "pexels" ? "Pexels" : "Pixabay";
  const authorUrl = httpsLink(attribution.authorUrl);
  const sourceUrl = httpsLink(attribution.sourceUrl);
  return (
    <small className="stock-attribution">
      {kind === "photo" ? "Photo" : "Video"} by{" "}
      {authorUrl
        ? <a href={authorUrl} target="_blank" rel="noopener noreferrer">{attribution.author}</a>
        : attribution.author}
      {" on "}{sourceUrl
        ? <a href={sourceUrl} target="_blank" rel="noopener noreferrer">{provider}</a>
        : provider}
    </small>
  );
}
