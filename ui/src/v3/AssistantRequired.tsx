import type { ReactNode } from "react";
import { v3api } from "./api";
import { useLoad } from "./common";
import { linkProps } from "./router";
import "./Addons.css";

export function AssistantRequired({ children, onSettings, offlineFallback = false }: {
  children: ReactNode; onSettings?: () => void; offlineFallback?: boolean;
}) {
  const { load, reload } = useLoad(v3api.health, "assistant-availability");
  const assistant = load.state === "ready" ? load.data?.capabilities?.assistant : undefined;
  if (assistant?.available && assistant.verification === "ready") return <>{children}</>;
  const reason = load.state === "loading" ? "Checking assistant…" : load.state === "error" ? "Couldn't check assistant."
    : assistant?.state === "installing" ? "Assistant is installing."
    : assistant?.state === "removing" ? "Assistant is being removed. Close and reopen Studio to finish."
    : assistant?.restartRequired ? "Restart Studio to enable the assistant."
    : assistant?.state === "needs update" ? "Assistant needs an update."
    : !assistant?.available ? "Assistant isn't installed."
    : assistant.keySource === "none" ? "Connect your key."
    : "Verify your key in Settings, Add-ons.";
  const settings = linkProps({ view: "settings", section: "addons" });
  return <><div className="addon-notice" role="status">
    <p>{reason}</p>
    {offlineFallback && <p>Keyword suggestions and manual expression editing work offline.</p>}
    <a {...settings} onClick={(event) => {
      settings.onClick(event);
      if (event.defaultPrevented) onSettings?.();
    }}>Open Settings, Add-ons</a>
    {load.state === "error" && <button type="button" className="ghost" onClick={reload}>Retry</button>}
  </div>{offlineFallback && children}</>;
}
