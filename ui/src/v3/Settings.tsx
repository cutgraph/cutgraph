import { useEffect, useState, type FormEvent } from "react";
import { api, type ChatConfig } from "../api";
import { v3api, type ClaudeConnection, type StockProviderStatus, type StockProviders } from "./api";
import { useChatStore } from "../store/useChatStore";
import { errorText, useLoad } from "./common";
import { toast } from "./Toast";
import Addons from "./Addons";
import Updates from "./Updates";

type Tone = "ok" | "warn" | "";

function Row({ name, what, status, tone }: { name: string; what: string; status: string; tone: Tone }) {
  return (
    <div className="conn">
      <div>
        <b>{name}</b>
        <small>{what}</small>
      </div>
      <span className={`st ${tone}`.trim()}>{status}</span>
    </div>
  );
}

function Agents({ revision }: { revision: number }) {
  const { load } = useLoad<ChatConfig>(() => api.getChatConfig(false), `chat-config-${revision}`);
  if (load.state === "loading") return <Row name="Agent providers" what="Checking…" status="…" tone="" />;
  if (load.state === "error") return <Row name="Agent providers" what={load.error.message} status="Unknown" tone="warn" />;
  return (
    <>
      {load.data.providers.map((p) => (
        <Row
          key={p.id}
          name={p.label}
          what={p.availability.error?.message ?? (p.profiles.map((f) => f.label).join(" · ") || "Agent provider")}
          status={p.availability.available ? "Available" : "Unavailable"}
          tone={p.availability.available ? "ok" : "warn"}
        />
      ))}
    </>
  );
}

const PROVIDER_LABEL: Record<keyof StockProviders, string> = { pexels: "Pexels", pixabay: "Pixabay" };

// API keys for stock search: env (`PEXELS_API_KEY`/`PIXABAY_API_KEY`) wins when set;
// this form writes a protected local secret. Only `configured: true/false` is ever
// read back — the key itself never round-trips into this page.
function StockKeyRow({ id, status, onSave }: {
  id: keyof StockProviders;
  status: StockProviderStatus;
  onSave: (id: keyof StockProviders, key: string) => Promise<void>;
}) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!value.trim() || busy) return;
    setBusy(true);
    try {
      await onSave(id, value.trim());
      setValue("");
    } finally {
      setBusy(false);
    }
  };
  const clear = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await onSave(id, "");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="conn">
      <div>
        <b>{PROVIDER_LABEL[id]}</b>
        <small>{status.configured ? "Key configured" : "No key set — stock search for this provider is off"}</small>
      </div>
      <form onSubmit={(e) => void submit(e)}>
        <input
          type="password"
          autoComplete="off"
          placeholder={status.configured ? "Replace key…" : "Paste API key…"}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <button type="submit" className="ghost" disabled={busy || !value.trim()}>Save</button>
        {status.configured && (
          <button type="button" className="ghost" disabled={busy} onClick={() => void clear()}>Clear</button>
        )}
      </form>
    </div>
  );
}

function StockKeys() {
  const { load, update } = useLoad(() => v3api.stockProviders(), "stock-providers");
  const save = async (id: keyof StockProviders, key: string) => {
    try {
      const status = await v3api.setStockKeys({ [id]: key || null });
      update(() => status);
      toast(key ? `${PROVIDER_LABEL[id]} key saved.` : `${PROVIDER_LABEL[id]} key cleared.`);
    } catch (err) {
      toast(errorText(err));
    }
  };
  if (load.state === "loading") return <Row name="Pexels · Pixabay" what="Checking…" status="…" tone="" />;
  if (load.state === "error") return <Row name="Pexels · Pixabay" what={load.error.message} status="Unknown" tone="warn" />;
  return (
    <>
      <StockKeyRow id="pexels" status={load.data.pexels} onSave={save} />
      <StockKeyRow id="pixabay" status={load.data.pixabay} onSave={save} />
    </>
  );
}

function ClaudeKeys({ onChange, available }: { onChange: () => void; available: boolean }) {
  const { load, update } = useLoad(available ? v3api.claudeConnection : v3api.claudeKeyPresence,
    `claude-connection-${available}`);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const checkingElsewhere = available && load.state === "ready" && load.data.state === "checking";
  useEffect(() => {
    if (!checkingElsewhere) return;
    const timer = window.setInterval(() => {
      void v3api.claudeConnection().then((status) => {
        update(() => status);
        if (status.state !== "checking") {
          useChatStore.setState({ chatConfig: null, errorKind: null, authError: null });
          onChange();
        }
      }, () => {
        setFailure("Couldn't check the Claude connection. Try again.");
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [checkingElsewhere, update, onChange]);
  const run = async (action: () => Promise<ClaudeConnection>) => {
    if (busy) return;
    setBusy(true);
    setFailure(null);
    try {
      const status = await action();
      update(() => status);
      useChatStore.setState({ chatConfig: null, errorKind: null, authError: null });
      onChange();
    } catch {
      setFailure("Couldn't update the Claude connection. Try again.");
    } finally {
      setBusy(false);
    }
  };
  if (load.state === "loading") return <Row name="Claude" what="Checking connection…" status="Checking…" tone="" />;
  if (load.state === "error") return <Row name="Claude" what={load.error.message} status="Error" tone="warn" />;
  const status = load.data;
  if (!available) return <div className="conn" role="group" aria-label="Assistant key">
    {status.source === "environment" ? <small>ANTHROPIC_API_KEY is set in this environment and takes precedence</small>
      : status.connected && <button type="button" className="ghost" disabled={busy}
        onClick={() => void run(v3api.disconnectClaude)}>Disconnect saved key</button>}
    {failure && <small role="alert">{failure}</small>}
  </div>;
  const checking = busy || status.state === "checking";
  const label = checking ? "Checking…" : failure ? "Error" : {
    not_connected: "Connect your key", unknown: "Key not verified", checking: "Checking…", ready: "Ready", error: "Error",
  }[status.state];
  return (
    <div className="conn" role="group" aria-label="Assistant key">
      <div>
        <small>{status.source === "environment"
          ? "ANTHROPIC_API_KEY is set in this environment and takes precedence"
          : failure ?? status.message ?? "Use your Anthropic API key for the Cutgraph Assistant."}</small>
        {status.source === "environment" && status.message && <small>{status.message}</small>}
        <span className={`st ${label === "Ready" ? "ok" : "warn"}`}>{label}</span>
      </div>
      {status.source !== "environment" && <div>
      {available && <form onSubmit={(e) => {
        e.preventDefault();
        if (!value || checking) return;
        const key = value;
        setValue("");
        void run(() => v3api.connectClaude(key));
      }}>
        <input type="password" autoComplete="off" aria-label="Anthropic API key"
          placeholder="Paste Anthropic API key…" value={value} disabled={checking}
          onChange={(e) => setValue(e.target.value)} />
        <button type="submit" className="ghost" disabled={checking || !value}>Connect</button>
        {status.connected && <>
          <button type="button" className="ghost" disabled={checking} onClick={() => void run(v3api.verifyClaude)}>Verify</button>
        </>}
      </form>}
      {status.connected && <button type="button" className="ghost" disabled={busy}
        onClick={() => void run(v3api.disconnectClaude)}>Disconnect saved key</button>}
      </div>}
    </div>
  );
}

function Pipeline() {
  const { load } = useLoad(v3api.health, "runtime-health");
  const capabilities = load.state === "ready" ? load.data.capabilities : null;
  const pending = load.state === "loading" ? "Checking" : "Unknown";
  return <>
    <Row name="ffmpeg" what="Local export with NVENC when available, otherwise CPU encoding"
      status={capabilities ? (capabilities.cpuRender ? "Tools available" : "Tools unavailable") : pending}
      tone={capabilities?.cpuRender ? "ok" : "warn"} />
  </>;
}

export default function Settings() {
  const [connectionRevision, setConnectionRevision] = useState(0);
  return (
    <section className="settings">
      <h1>Settings</h1>
      <p className="muted">
        Connections: where things come from and where they run.
      </p>
      <h2>Agents</h2>
      <Agents revision={connectionRevision} />
      <h2>Pipeline</h2>
      <Pipeline />
      <h2 id="addons">Add-ons</h2>
      <p className="muted">Optional tools for your Videos. Script editing works without add-ons.</p>
      <Addons assistant={(available) => <ClaudeKeys available={available} onChange={() => setConnectionRevision((r) => r + 1)} />} />
      <h2>Stock B-roll</h2>
      <p className="muted">Search Pexels and Pixabay for B-roll from the drawer. Either key works alone.</p>
      <StockKeys />
      <h2>Storage</h2>
      <p className="muted">
        Workspaces and their libraries live on the studio. Every asset is stored once by content hash, so importing
        the same file twice reuses it.
      </p>
      <Updates />
      <h2 id="about">About</h2>
      <p className="muted">This software uses code of FFmpeg licensed under the LGPLv3. FFmpeg runs as a separate program.</p>
      <p>
        Included with installed distributions: <a href="/api/distribution/notices" target="_blank" rel="noreferrer">Third-party notices</a>
        {" · "}<a href="/api/distribution/sbom" target="_blank" rel="noreferrer">Software bill of materials (SBOM)</a>.
        {" "}Matching FFmpeg source is published beside the installer download.
      </p>
    </section>
  );
}
