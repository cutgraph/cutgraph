import { loadBuildPacks } from "./packs";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import V3App from "./v3/V3App";
import "../fonts.css";

void loadBuildPacks().catch((error) => console.warn("Skipping character packs:", error)).finally(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <V3App />
    </StrictMode>,
  );
});
