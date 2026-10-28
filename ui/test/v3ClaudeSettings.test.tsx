// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const mocks = vi.hoisted(() => ({ status: vi.fn(), presence: vi.fn(), connect: vi.fn(), disconnect: vi.fn(), verify: vi.fn(), health: vi.fn(), addons: vi.fn(), install: vi.fn(), config: vi.fn() }));
vi.mock("../src/v3/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/v3/api")>()),
  v3api: {
  updates: async () => ({ state: "not_configured", automatic: true, currentVersion: "0.2.0", reason: "Updates not configured." }),
  addons: mocks.addons,
  installAddon: mocks.install,
  health: mocks.health,
  stockProviders: async () => ({ pexels: { configured: false }, pixabay: { configured: false } }),
  claudeConnection: mocks.status,
  claudeKeyPresence: mocks.presence,
  connectClaude: mocks.connect,
  disconnectClaude: mocks.disconnect,
  verifyClaude: mocks.verify,
} }));
vi.mock("../src/api", () => ({ api: { getChatConfig: mocks.config } }));
import Settings from "../src/v3/Settings";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.addons.mockResolvedValue({ addons: [{ name: "assistant", title: "Assistant", state: "standard", tier: "standard",
    sourceInstallation: true, available: true, installedBytes: 0, restartRequired: false, jobId: null }], gpus: [], recommendedTier: "cpu" });
  mocks.health.mockResolvedValue({ capabilities: { localInference: false, cpuRender: true } });
  mocks.status.mockResolvedValue({ connected: false, state: "not_connected" });
  mocks.presence.mockResolvedValue({ connected: false, state: "not_connected" });
  mocks.config.mockResolvedValue({ providers: [] });
});
afterEach(cleanup);

it("links About to the installed notices and SBOM", async () => {
  render(<Settings />);
  expect(screen.getByRole("heading", { name: "About" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Third-party notices" }).getAttribute("href")).toBe("/api/distribution/notices");
  expect(screen.getByRole("link", { name: "Software bill of materials (SBOM)" }).getAttribute("href")).toBe("/api/distribution/sbom");
  await screen.findByText("Connect your key");
});

it("shows the environment override instead of credential controls", async () => {
  mocks.status.mockResolvedValue({ connected: true, state: "ready", source: "environment" });
  render(<Settings />);
  expect(await screen.findByText("ANTHROPIC_API_KEY is set in this environment and takes precedence")).toBeTruthy();
  expect(screen.queryByLabelText("Anthropic API key")).toBeNull();
  expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Disconnect saved key" })).toBeNull();
});

it("lets an environment key retry verification after a transient failure", async () => {
  mocks.status.mockResolvedValue({ connected: true, state: "error", source: "environment", message: "Couldn't reach Anthropic." });
  mocks.verify.mockResolvedValue({ connected: true, state: "ready", source: "environment" });
  render(<Settings />);
  expect(await screen.findByText("Couldn't reach Anthropic.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Verify" }));
  expect(await within(screen.getByRole("group", { name: "Assistant key" })).findByText("Ready")).toBeTruthy();
  expect(mocks.verify).toHaveBeenCalledOnce();
  expect(screen.queryByText("Couldn't reach Anthropic.")).toBeNull();
  expect(screen.queryByLabelText("Anthropic API key")).toBeNull();
});

it("connects, checks, clears the key input and disconnects", async () => {
  let finish!: (value: { connected: boolean; state: string }) => void;
  mocks.connect.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  mocks.disconnect.mockResolvedValue({ connected: false, state: "not_connected" });
  render(<Settings />);
  expect(await screen.findByText("Connect your key")).toBeTruthy();
  const input = screen.getByLabelText("Anthropic API key") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "test-api-secret" } });
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));
  expect(await screen.findByText("Checking…")).toBeTruthy();
  expect(input.value).toBe("");
  expect(input.type).toBe("password");
  finish({ connected: true, state: "ready" });
  expect(await within(screen.getByRole("group", { name: "Assistant key" })).findByText("Ready")).toBeTruthy();
  expect(mocks.connect).toHaveBeenCalledWith("test-api-secret");
  expect(document.body.textContent).not.toContain("test-api-secret");
  fireEvent.click(screen.getByRole("button", { name: "Disconnect saved key" }));
  expect(await screen.findByText("Connect your key")).toBeTruthy();
  expect(mocks.disconnect).toHaveBeenCalledOnce();
});

it("keeps a rejected key in error until a replacement is connected", async () => {
  mocks.connect.mockResolvedValue({ connected: true, state: "error", message: "That API key was rejected." });
  mocks.verify.mockResolvedValue({ connected: true, state: "error", message: "That API key was rejected." });
  render(<Settings />);
  const input = await screen.findByLabelText("Anthropic API key");
  fireEvent.change(input, { target: { value: "test-api-secret" } });
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));
  expect(await screen.findByText("That API key was rejected.")).toBeTruthy();
  expect((input as HTMLInputElement).value).toBe("");
  fireEvent.click(screen.getByRole("button", { name: "Verify" }));
  await waitFor(() => expect(mocks.verify).toHaveBeenCalledOnce());
  expect(screen.getByText("That API key was rejected.")).toBeTruthy();
  expect(within(screen.getByRole("group", { name: "Assistant key" })).getByText("Error")).toBeTruthy();
  mocks.connect.mockResolvedValue({ connected: true, state: "ready", source: "settings" });
  await waitFor(() => expect((screen.getByRole("button", { name: "Verify" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.change(input, { target: { value: "replacement-test-secret" } });
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));
  expect(await within(screen.getByRole("group", { name: "Assistant key" })).findByText("Ready")).toBeTruthy();
  expect(screen.queryByText("That API key was rejected.")).toBeNull();
  expect((input as HTMLInputElement).value).toBe("");
});

it("requires terms before downloading the missing assistant and keeps keys hidden until restart", async () => {
  const missing = { name: "assistant", title: "Assistant", state: "not installed", tier: null,
    available: false, installedBytes: 0, restartRequired: false, jobId: null,
    description: "Contains Anthropic software.", tiers: [{ tier: "standard", supported: true,
      approximateBytes: 94e6, acceptance: "terms", declaration: { licenses: [
        { label: "Anthropic Commercial Terms", url: "https://www.anthropic.com/legal/commercial-terms" },
      ] } }] };
  mocks.addons.mockResolvedValue({ addons: [missing], gpus: [], recommendedTier: "cpu" });
  render(<Settings />);
  const install = await screen.findByRole("button", { name: /Install assistant \(94 MB\)/ });
  expect(install).toHaveProperty("disabled", true);
  expect(screen.getByRole("link", { name: "Anthropic Commercial Terms" })).toHaveProperty("href", "https://www.anthropic.com/legal/commercial-terms");
  expect(screen.queryByLabelText("Anthropic API key")).toBeNull();
  expect(mocks.presence).toHaveBeenCalled();
  expect(mocks.status).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox", { name: /assistant/i }));
  mocks.addons.mockResolvedValue({ addons: [{ ...missing, tier: "standard", state: "standard", restartRequired: true }], gpus: [], recommendedTier: "cpu" });
  fireEvent.click(install);
  await waitFor(() => expect(mocks.install).toHaveBeenCalledWith("assistant", "standard", "terms", undefined));
  expect(await screen.findByText("Close and reopen Studio to apply this change.")).toBeTruthy();
  expect(screen.queryByLabelText("Anthropic API key")).toBeNull();
  expect(mocks.status).not.toHaveBeenCalled();
  expect(mocks.verify).not.toHaveBeenCalled();
});

it.each(["not installed", "removed", "needs update", "restart pending", "load failed"])(
  "disconnects a saved key when the add-on is %s", async (state) => {
    mocks.presence.mockResolvedValue({ connected: true, source: "settings", state: "checking" });
    mocks.disconnect.mockResolvedValue({ connected: false, source: null, state: "not_connected" });
    if (state === "load failed") mocks.addons.mockRejectedValue(new Error("Unavailable"));
    else mocks.addons.mockResolvedValue({ addons: [{ name: "assistant", title: "Assistant", state,
      available: false, installedBytes: 0, restartRequired: state === "restart pending", jobId: null }], gpus: [], recommendedTier: "cpu" });
    render(<Settings />);
    const disconnect = await screen.findByRole("button", { name: "Disconnect saved key" });
    expect(screen.queryByLabelText("Anthropic API key")).toBeNull();
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Verify" })).toBeNull();
    expect(mocks.presence).toHaveBeenCalled();
    expect(mocks.status).not.toHaveBeenCalled();
    expect(mocks.verify).not.toHaveBeenCalled();
    expect(mocks.config).toHaveBeenCalledExactlyOnceWith(false);
    fireEvent.click(disconnect);
    await waitFor(() => expect(mocks.disconnect).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.queryByRole("button", { name: "Disconnect saved key" })).toBeNull());
    expect(mocks.status).not.toHaveBeenCalled();
  },
);

it("loads one catalog and holds one event subscription for all add-ons", async () => {
  const close = vi.fn();
  const sources = vi.fn(function () { return { close, onmessage: null }; });
  vi.stubGlobal("EventSource", sources);
  try {
    mocks.addons.mockResolvedValue({ addons: [
      { name: "transcription", title: "Transcription", available: false, state: "not installed", tiers: [
        { tier: "cpu", supported: true, approximateBytes: 4e9, acceptance: "speech", declaration: { licenses: [] } },
      ] },
      { name: "assistant", title: "Assistant", available: false, state: "not installed", tiers: [
        { tier: "standard", supported: true, approximateBytes: 94e6, acceptance: "assistant", declaration: { licenses: [] } },
      ] },
    ], gpus: [], recommendedTier: "cpu" });
    const view = render(<Settings />);
    expect(await screen.findByRole("heading", { name: "Assistant" })).toHaveProperty("id", "assistant");
    expect(screen.getAllByText("Assistant", { exact: true })).toHaveLength(1);
    expect(screen.getByRole("checkbox", { name: "Accept Assistant third-party terms" })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "Accept Transcription third-party terms" })).toBeTruthy();
    expect(mocks.addons).toHaveBeenCalledOnce();
    expect(sources).toHaveBeenCalledExactlyOnceWith("/api/events?project=%40addons");
    view.unmount();
    expect(close).toHaveBeenCalledOnce();
  } finally { vi.unstubAllGlobals(); }
});


it("reports local export tools from runtime health", async () => {
  mocks.health.mockResolvedValue({ capabilities: { localInference: true, cpuRender: false } });
  render(<Settings />);
  expect(await screen.findByText("Tools unavailable")).toBeTruthy();
  expect(screen.queryByText("WhisperX")).toBeNull();
  expect(screen.queryByText("Built in")).toBeNull();
});

it("reports available export tools", async () => {
  render(<Settings />);
  expect(await screen.findByText("Tools available")).toBeTruthy();
});

it("reports unknown capability status when the probe fails", async () => {
  mocks.health.mockRejectedValue(new Error("Unavailable"));
  render(<Settings />);
  await waitFor(() => expect(screen.getAllByText("Unknown")).toHaveLength(1));
});
