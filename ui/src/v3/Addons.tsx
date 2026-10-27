import { useEffect, useState, type ReactNode } from "react";
import { v3api, type AddonStatus } from "./api";
import { errorText, useLoad } from "./common";
import { subscribeJobEvents } from "./story/export/jobEvents";
import { linkProps } from "./router";
import { addonSnapshot } from "./addonSnapshot";
import { useChatStore } from "../store/useChatStore";
import "./Addons.css";

const size = (bytes: number) => bytes === 0 ? "0 MB" : bytes < 1e9
  ? `${Math.ceil(bytes / 1e6)} MB` : `${(bytes / 1e9).toFixed(2)} GB`;

export function AddonSettingsLink({ children = "Open Settings, Add-ons" }: { children?: ReactNode }) {
  return <a {...linkProps({ view: "settings", section: "addons" })}>{children}</a>;
}

export function TranscriptionRequired({ children }: { children: ReactNode }) {
  const { load, reload } = useLoad(v3api.health, "transcription-availability");
  if (load.state === "ready" && load.data?.capabilities?.localInference === true) return <>{children}</>;
  return <div className="addon-notice" role="status">
    <p>{load.state === "loading" ? "Checking transcription…" : load.state === "error"
      ? "Couldn't check transcription." : transcriptionReason(load.data?.capabilities?.transcription)}</p>
    <AddonSettingsLink />
    {load.state === "error" && <button type="button" className="ghost" onClick={reload}>Retry</button>}
  </div>;
}

function transcriptionReason(addon?: AddonStatus) {
  if (addon?.state === "installing") return "Transcription is installing. You can edit a script Video while you wait.";
  if (addon?.state === "removing") return "Transcription is being removed. Close and reopen Studio to finish.";
  if (addon?.restartRequired) return "Restart Studio to enable transcription.";
  if (addon?.state === "needs update") return "Transcription needs an update in Settings, Add-ons.";
  return "Transcription is not installed. Script Videos remain available.";
}

export function AddonCard({ addon, recommended, refresh, packaged = false, preview = false, onDismiss, heading = false }: {
  addon: AddonStatus; recommended: string; refresh: () => Promise<void>; packaged?: boolean;
  preview?: boolean; onDismiss?: () => void; heading?: boolean;
}) {
  const [expanded, setExpanded] = useState(!preview);
  const [choice, setChoice] = useState(addon.tier ?? (addon.tiers?.some((item) => item.tier === recommended)
    ? recommended : addon.tiers?.[0]?.tier ?? "off"));
  const [accepted, setAccepted] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const tier = addon.tiers?.find((item) => item.tier === choice);
  const installing = addon.state === "installing";
  const removing = addon.state === "removing";
  const label = removing ? "Removing…" : installing ? "Installing…" : addon.job?.canRetry ? "Download failed"
    : addon.state === "needs update" ? "Update available" : addon.available ? "Ready" : addon.restartRequired ? "Restart required" : "Not installed";
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setFailure(null);
    try {
      await action();
      if (addon.name === "assistant") useChatStore.setState({ chatConfig: null, errorKind: null, authError: null });
      await refresh();
    } catch (error) {
      setFailure(errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return <article className="addon-card" aria-label={addon.title ?? addon.name}>
    <div className="conn">
      <div>{heading ? <h3 id={addon.name}>{addon.title ?? addon.name}</h3> : <b>{addon.title ?? addon.name}</b>}
        <small>{addon.description ?? "Optional local capability"}</small></div>
      <span className={`st ${addon.available ? "ok" : "warn"}`}>{label}</span>
      {onDismiss && <button type="button" className="icon" aria-label={`Dismiss ${addon.title ?? addon.name} setup`} onClick={onDismiss}>×</button>}
    </div>
    {!preview && <p className="muted">{addon.installedBytes ? `${size(addon.installedBytes)} on disk for this add-on and its managed caches. ` : "Optional download. "}
      {addon.downloadNote}</p>}
    {preview && tier && <p className="muted">{tier.label ?? tier.tier} {addon.name === "transcription" ? "· detected tier · " : "· "}
      about {size(tier.approximateBytes)} to download.</p>}
    {preview && addon.name === "assistant" && <p className="muted">Install, then connect your own Anthropic key. API use is billed by the provider.</p>}
    {removing && <p role="status">Removal is pending. Close and reopen Studio to finish freeing its files.</p>}
    {addon.state === "unavailable" && <p role="status">This add-on is unavailable in this build. Update Studio.</p>}
    {addon.restartRequired && <p role="status">Close and reopen Studio to apply this change.</p>}
    {!addon.sourceInstallation && addon.tier && <p className="muted">{addon.removalNote ?? "Remove frees this add-on's packages and owned caches."}</p>}
    {addon.sourceInstallation ? <p className="muted">{packaged ? "Included with this installation."
      : "Provided by this source installation. Manage its packages in its Python environment."}</p> : !expanded && !installing && !removing ? <div className="addon-actions">
      {!addon.available && !addon.restartRequired && <button type="button" className="ghost" onClick={() => setExpanded(true)}>Install {addon.title?.toLowerCase() ?? addon.name}</button>}
      <AddonSettingsLink>{addon.available && addon.name === "assistant" ? "Connect your key or manage assistant" : "Manage in Settings, Add-ons"}</AddonSettingsLink>
    </div> : <>
      <label>{addon.title ?? addon.name} tier <select aria-label={`${addon.title ?? addon.name} tier`} value={choice} disabled={busy || installing || removing || addon.restartRequired}
        onChange={(event) => { setChoice(event.target.value); setAccepted(""); }}>
        <option value="off">Off</option>
        {addon.tiers?.map((item) => <option value={item.tier} key={item.tier} disabled={!item.supported}>
          {item.label ?? item.tier}{item.tier === recommended ? " · recommended" : ""}
        </option>)}
      </select></label>
      {tier?.note && <p className="muted">{tier.note}</p>}
      {tier && !installing && !removing && <>
        <p className="muted">About {size(tier.approximateBytes)} of downloads. Free disk space is also needed during installation.</p>
        <p>Review licences before downloading:</p><ul>
          {tier.declaration.licenses.map((license) => <li key={license.label}>
            <a href={license.url} target="_blank" rel="noreferrer">{license.label}</a>
          </li>)}
        </ul>
        <label className="addon-accept"><input type="checkbox" checked={accepted === tier.acceptance}
          aria-label={`Accept ${addon.title ?? addon.name} third-party terms`}
          onChange={(event) => setAccepted(event.target.checked ? tier.acceptance : "")} />
          I accept the applicable third-party terms, including the linked licences, for this download.</label>
      </>}
      {addon.job && <div role="status" aria-live="polite">
        <p>{addon.job.progressStage}{addon.job.canRetry ? " — Retry reuses completed downloads kept for up to seven days. Partial downloads start again." : ""}</p>
        {installing && <progress aria-label="Add-on installation" max={1} value={addon.job.progress} />}
        {addon.job.error && <p>{addon.job.error}</p>}
      </div>}
      <div className="addon-actions">
        {removing ? <button className="ghost" type="button" disabled>Removing…</button> : installing ? <button className="ghost" type="button" disabled={busy || !addon.job?.canCancel}
          onClick={() => addon.jobId && void run(() => v3api.cancelAddon(addon.jobId!))}>Cancel download</button> : <>
          {tier && <button className="primary" type="button"
            disabled={busy || !tier.supported || accepted !== tier.acceptance || addon.restartRequired || (addon.tier === choice && addon.state !== "needs update" && !addon.job?.canRetry)}
            onClick={() => void run(() => v3api.installAddon(addon.name, choice, accepted, addon.job?.canRetry))}>
            {addon.job?.canRetry ? "Retry download" : addon.state === "needs update" ? "Update add-on"
              : addon.name === "assistant" ? `Install assistant (${size(tier.approximateBytes)})` : "Install add-on"}
          </button>}
          {(addon.tier || addon.jobId) && <button className="ghost" type="button" disabled={busy}
            onClick={() => void run(() => v3api.removeAddon(addon.name))}>{choice === "off" ? "Turn off and remove" : "Remove add-on"}</button>}
        </>}
      </div>
    </>}
    {failure && <p role="alert">{failure}</p>}
  </article>;
}

export default function Addons({ assistant }: {
  assistant?: (available: boolean) => ReactNode;
}) {
  const { load, update, reload } = useLoad(addonSnapshot, "addons");
  const refresh = async () => { const data = await addonSnapshot(); update(() => data); };
  const installing = load.state === "ready" && load.data.addons.some((addon) => addon.state === "installing");
  useEffect(() => {
    if (window.location.hash === "#assistant") document.getElementById("assistant")?.scrollIntoView?.();
  }, [load.state]);
  useEffect(() => {
    let live = true;
    const reconcile = () => { void addonSnapshot().then((data) => live && update(() => data), () => {}); };
    const unwatch = typeof EventSource === "undefined" ? () => {} : subscribeJobEvents("@addons", reconcile);
    const timer = installing ? window.setInterval(reconcile, 2000) : undefined;
    return () => { live = false; unwatch(); window.clearInterval(timer); };
  }, [installing, update]);
  const data = load.state === "ready" ? load.data : null;
  const card = (addon: AddonStatus) => data && <AddonCard addon={addon} recommended={data.recommendedTier}
    refresh={refresh} packaged={data.packaged} heading />;
  const assistantAddon = data?.addons.find((addon) => addon.name === "assistant");
  // The key panel mounts once the catalog settles, in one tree position, so it neither remounts
  // nor switches between presence-only and verified loads.
  return <div>
    {load.state === "loading" && <p className="muted">Checking add-ons…</p>}
    {load.state === "error" && <p role="alert">Couldn't check add-ons. <button className="ghost" onClick={reload}>Retry</button></p>}
    {data?.gpus.map((gpu) => <p className="muted" key={`${gpu.name}-${gpu.memoryMiB}`}>
      {gpu.name} · driver {gpu.driver} · {Math.round(gpu.memoryMiB / 1024)} GiB VRAM{gpu.supported ? "" : " · use the CPU tier"}
    </p>)}
    {data?.addons.filter((addon) => addon !== assistantAddon).map((addon) => <div key={addon.name}>{card(addon)}</div>)}
    {assistantAddon ? card(assistantAddon) : assistant && <h3 id="assistant">Assistant</h3>}
    {load.state !== "loading" && assistant?.(assistantAddon?.available ?? false)}
  </div>;
}
