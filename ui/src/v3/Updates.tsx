import { useEffect, useRef, useState } from "react";
import { apiErrorText, v3api, type UpdateStatus } from "./api";
import "./Updates.css";

const labels: Record<UpdateStatus["state"], string> = {
  not_configured: "Updates not configured", disabled: "Update checks are off", checking: "Checking for updates…",
  up_to_date: "Up to date", available: "Update available", downloading: "Downloading update…",
  verifying: "Verifying update…", installing: "Installing update…", failed: "Update failed",
};
const busy = new Set(["checking", "downloading", "verifying", "installing"]);

export default function Updates({ compact = false }: { compact?: boolean }) {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [failure, setFailure] = useState("");
  const [pending, setPending] = useState(false);
  const [preference, setPreference] = useState<boolean | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    let live = true;
    const load = () => {
      const requestGeneration = generation.current;
      return v3api.updates().then((value) => {
        if (live && requestGeneration === generation.current) setStatus(value);
      }, () => { if (live && requestGeneration === generation.current) setFailure("Couldn't read update status."); });
    };
    void load();
    const timer = window.setInterval(() => void load(), 1500);
    return () => { live = false; window.clearInterval(timer); };
  }, []);
  const run = async (action: () => Promise<UpdateStatus>) => {
    generation.current += 1;
    setPending(true); setFailure("");
    try { setStatus(await action()); }
    catch (error) { setFailure(error instanceof Error ? apiErrorText(error) : "Couldn't update Studio."); }
    finally { generation.current += 1; setPending(false); }
  };
  const changeAutomatic = async (value: boolean) => {
    setPreference(value);
    await run(() => v3api.updatePreferences(value));
    setPreference(null);
  };
  if (compact && status?.state !== "available") return null;
  const working = pending || !!status && busy.has(status.state);
  return <section className="studio-updates" aria-label="Studio updates">
    {!compact && <h2>Updates</h2>}
    <p role="status" aria-label="Update status">{status ? labels[status.state] : "Checking update status…"}{status?.version && status.state === "available" ? `: ${status.version}` : ""}</p>
    {!compact && status && <>
      <p className="muted">Installed version: {status.currentVersion}. {status.reason}</p>
      <label><input type="checkbox" checked={preference ?? status.automatic} disabled={pending}
        onChange={(e) => void changeAutomatic(e.target.checked)} /> Check for updates automatically</label>
      <p className="muted">Checks GitHub at most once a day. Turning this off stops update requests and downloads.</p>
      <button className="ghost" disabled={working || !status.automatic || status.state === "not_configured"}
        onClick={() => void run(v3api.checkUpdates)}>Check for updates</button>
    </>}
    {status?.releaseNotesUrl && <p><a href={status.releaseNotesUrl} target="_blank" rel="noreferrer">Release notes and download</a></p>}
    {status?.canInstall && <>
      <p>Save your work before updating. Studio will close and reopen after installation.</p>
      <button className="primary" disabled={working} onClick={() => void run(v3api.installUpdate)}>Update to {status.version}</button>
    </>}
    {failure && <p role="alert">{failure}</p>}
  </section>;
}
