// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { AddonStatus } from "../src/v3/api";
import type { ChatConfig, ComputeJob } from "../src/api";
import { useChatStore } from "../src/store/useChatStore";
import { initialChatState } from "../src/chat/agentEvents";

const mocks = vi.hoisted(() => ({ health: vi.fn(), addons: vi.fn(), install: vi.fn(), remove: vi.fn(), cancel: vi.fn() }));
vi.mock("../src/v3/api", async (original) => ({
  ...(await original<typeof import("../src/v3/api")>()),
  v3api: { health: mocks.health, addons: mocks.addons, installAddon: mocks.install,
    removeAddon: mocks.remove, cancelAddon: mocks.cancel },
}));
import Addons, { AddonCard, TranscriptionRequired } from "../src/v3/Addons";
import { AssistantRequired } from "../src/v3/AssistantRequired";
import NewVideoFlow from "../src/v3/newvideo/NewVideoFlow";
import { useNewVideoFlow } from "../src/v3/newvideo/store";
import { IDLE_FLOW } from "../src/v3/newvideo/flow";

const addon = (patch: Partial<AddonStatus> = {}): AddonStatus => ({
  name: "transcription", state: "not installed", tier: null, available: false, restartRequired: false,
  title: "Transcription", description: "Local speech recognition and word timing",
  installedBytes: 0, sourceInstallation: false, jobId: null,
  tiers: [{ tier: "cpu", approximateBytes: 4e9, supported: true, acceptance: "current",
    label: "CPU", note: "CPU uses int8. Speed varies.",
    declaration: { licenses: [{ label: "Speech licence", url: "https://example.org/licence" }] } }],
  ...patch,
});

it("uses another declaration's labels and copy without transcription defaults", () => {
  render(<AddonCard addon={addon({ name: "assistant", title: "Assistant", description: "Optional writing help",
    downloadNote: "Fetched when requested.", removalNote: "Shared caches are kept.",
    tiers: [{ tier: "standard", label: "Standard", note: "Requires your own account.", approximateBytes: 2e8,
      supported: true, acceptance: "current", declaration: { licenses: [] } }] })}
    recommended="cpu" refresh={refresh} />);
  expect(screen.getByLabelText("Assistant tier")).toHaveProperty("value", "standard");
  expect(screen.getByText("Optional writing help")).toBeTruthy();
  expect(screen.getByText("Requires your own account.")).toBeTruthy();
  expect(screen.queryByText(/speech|int8|Transcription tier/i)).toBeNull();
});
const job = (patch: Partial<ComputeJob> = {}): ComputeJob => ({
  id: "download", kind: "addon_install", status: "running", progress: 0.4, progressStage: "Downloading verified wheels",
  canCancel: true, canRetry: false, ...patch,
});
const catalog = (value: AddonStatus) => ({ addons: [value], gpus: [], recommendedTier: "cpu" });
const refresh = vi.fn(async () => {});

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/editor/");
  mocks.health.mockResolvedValue({ capabilities: { localInference: false } });
  mocks.addons.mockResolvedValue(catalog(addon()));
  mocks.install.mockResolvedValue({ jobId: "download" });
  mocks.cancel.mockResolvedValue({});
  mocks.remove.mockResolvedValue({});
  useNewVideoFlow.setState({ open: false, reconciled: true, source: null, flow: IDLE_FLOW });
  useChatStore.setState({ ...initialChatState(), chatConfig: null });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it.each(["install", "remove"])("invalidates assistant readiness after %s", async (action) => {
  useChatStore.setState({ chatConfig: { providers: [] } as unknown as ChatConfig,
    errorKind: "assistant_not_installed", authError: "Old refusal" });
  render(<AddonCard addon={addon({ name: "assistant", title: "Assistant",
    tier: action === "remove" ? "cpu" : null })} recommended="cpu" refresh={refresh} />);
  if (action === "install") fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: action === "remove" ? "Remove add-on" : /Install assistant/ }));
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  expect(useChatStore.getState()).toMatchObject({ chatConfig: null, errorKind: null, authError: null });
});

it("requires acceptance with visible licence links before installing", async () => {
  render(<AddonCard addon={addon()} recommended="cpu" refresh={refresh} />);
  expect(screen.getByText("Not installed")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Speech licence" }).closest("details")).toBeNull();
  const install = screen.getByRole("button", { name: "Install add-on" }) as HTMLButtonElement;
  expect(install.disabled).toBe(true);
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(install);
  await waitFor(() => expect(mocks.install).toHaveBeenCalledWith("transcription", "cpu", "current", undefined));
  expect(refresh).toHaveBeenCalledOnce();
});

it("shows installation progress and cancels the active job", async () => {
  render(<AddonCard addon={addon({ state: "installing", jobId: "download", job: job() })} recommended="cpu" refresh={refresh} />);
  expect((screen.getByRole("progressbar") as HTMLProgressElement).value).toBe(0.4);
  expect(screen.getByText("Downloading verified wheels")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Cancel download" }));
  await waitFor(() => expect(mocks.cancel).toHaveBeenCalledWith("download"));
});

it("shows the installed footprint, restart requirement and remove action", async () => {
  render(<AddonCard addon={addon({ state: "cpu", tier: "cpu", installedBytes: 7.5e9, restartRequired: true })} recommended="cpu" refresh={refresh} />);
  expect(screen.getByText(/7.50 GB on disk for this add-on and its managed caches/)).toBeTruthy();
  expect(screen.getByText(/Close and reopen Studio/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "Install add-on" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Remove add-on" }));
  await waitFor(() => expect(mocks.remove).toHaveBeenCalledWith("transcription"));
});

it("labels a ready add-on and offers update for an incompatible record", () => {
  const { rerender } = render(<AddonCard addon={addon({ state: "cpu", tier: "cpu", available: true })} recommended="cpu" refresh={refresh} />);
  expect(screen.getByText("Ready")).toBeTruthy();
  rerender(<AddonCard addon={addon({ state: "needs update", tier: "cpu" })} recommended="cpu" refresh={refresh} />);
  expect(screen.getByText("Update available")).toBeTruthy();
  fireEvent.click(screen.getByRole("checkbox"));
  expect((screen.getByRole("button", { name: "Update add-on" }) as HTMLButtonElement).disabled).toBe(false);
});

it("shows the persisted failure and retries using renewed acceptance", async () => {
  render(<AddonCard addon={addon({ jobId: "download", job: job({ status: "error", canCancel: false, canRetry: true,
    error: "Installation ran out of disk space. Free space and retry." }) })} recommended="cpu" refresh={refresh} />);
  expect(screen.getByText(/Installation ran out of disk space/)).toBeTruthy();
  expect(screen.getByText(/Partial downloads start again/)).toBeTruthy();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Retry download" }));
  await waitFor(() => expect(mocks.install).toHaveBeenCalledWith("transcription", "cpu", "current", true));
});

it("reports action errors without losing the controls", async () => {
  mocks.install.mockRejectedValueOnce(new Error("Disk is full"));
  render(<AddonCard addon={addon()} recommended="cpu" refresh={refresh} />);
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Install add-on" }));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Disk is full");
});

it.each([undefined, {}, { capabilities: {} }, { capabilities: { localInference: false } }])(
  "renders unavailable for missing or partial capability data: %j", async (health) => {
    mocks.health.mockResolvedValue(health);
    render(<TranscriptionRequired><button>Transcribe</button></TranscriptionRequired>);
    expect(await screen.findByText(/Transcription is not installed/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Transcribe" })).toBeNull();
    expect(screen.getByRole("link", { name: "Open Settings, Add-ons" }).getAttribute("href")).toContain("#addons");
  },
);

it("renders loading, failed probe with retry, and available entry states", async () => {
  mocks.health.mockImplementationOnce(() => new Promise(() => {}));
  const view = render(<TranscriptionRequired><button>Transcribe</button></TranscriptionRequired>);
  expect(screen.getByText("Checking transcription…")).toBeTruthy();
  view.unmount();
  mocks.health.mockRejectedValueOnce(new Error("Offline"));
  render(<TranscriptionRequired><button>Transcribe</button></TranscriptionRequired>);
  expect(await screen.findByText("Couldn't check transcription.")).toBeTruthy();
  mocks.health.mockResolvedValue({ capabilities: { localInference: true } });
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("button", { name: "Transcribe" })).toBeTruthy();
});

it("gates footage selection and preserves its flow during Settings navigation", async () => {
  useNewVideoFlow.setState({ open: true, wsId: "workspace", reconciled: true,
    flow: { ...IDLE_FLOW, title: "My video" } });
  render(<NewVideoFlow />);
  const link = await screen.findByRole("link", { name: "Open Settings, Add-ons" });
  expect(screen.queryByRole("tab", { name: "From your library" })).toBeNull();
  fireEvent.click(link);
  expect(window.location.search).toBe("?view=settings");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(useNewVideoFlow.getState().flow.title).toBe("My video");
});

it("polls only while installing and stops polling after completion", async () => {
  vi.useFakeTimers();
  const view = render(<Addons />);
  await act(async () => {});
  await act(async () => { vi.advanceTimersByTime(6000); });
  expect(mocks.addons).toHaveBeenCalledTimes(1);
  view.unmount();
  mocks.addons.mockResolvedValue(catalog(addon({ state: "installing", job: job() })));
  render(<Addons />);
  await act(async () => {});
  mocks.addons.mockResolvedValue(catalog(addon({ state: "cpu", tier: "cpu", restartRequired: true })));
  await act(async () => { vi.advanceTimersByTime(2000); });
  const completed = mocks.addons.mock.calls.length;
  await act(async () => { vi.advanceTimersByTime(6000); });
  expect(mocks.addons).toHaveBeenCalledTimes(completed);
});

it.each([
  ["not installed", { tier: null }, "Not installed"],
  ["installing", { jobId: "download", job: job() }, "Installing…"],
  ["cpu", { tier: "cpu", available: true, installedBytes: 7e9 }, "Ready"],
  ["needs update", { tier: "cpu" }, "Update available"],
  ["not installed", { job: job({ status: "error", canRetry: true, error: "Download interrupted." }) }, "Download failed"],
  ["removing", { restartRequired: true }, "Removing…"],
] as const)("uses mocked health to display %s", async (state, patch, label) => {
  const status = addon({ state, ...patch });
  mocks.health.mockResolvedValue({ capabilities: { packaged: true, transcription: status } });
  render(<Addons />);
  expect(await screen.findByText(label, { selector: ".st" })).toBeTruthy();
  if (state === "installing") expect((screen.getByRole("progressbar") as HTMLProgressElement).value).toBe(0.4);
  if (state === "removing") {
    expect(screen.getByRole("button", { name: "Removing…" })).toHaveProperty("disabled", true);
    expect(screen.queryByRole("button", { name: /Install add-on|Remove add-on/ })).toBeNull();
  }
  if (label === "Download failed") expect(screen.getByText("Download interrupted.")).toBeTruthy();
});

it.each([true, false])("chooses installation guidance from packaged=%s", async (packaged) => {
  mocks.health.mockResolvedValue({ capabilities: { packaged, transcription: addon({ available: true, sourceInstallation: true }) } });
  render(<Addons />);
  await screen.findByText("Ready");
  expect(document.body.textContent?.includes("Python environment")).toBe(!packaged);
  expect(document.body.textContent).not.toMatch(/pip|venv|requirements-.*\.txt|terminal instructions/i);
});

it.each([
  [{ available: false }, "Assistant isn't installed."],
  [{ available: false, state: "installing" }, "Assistant is installing."],
  [{ available: false, restartRequired: true }, "Restart Studio to enable the assistant."],
  [{ available: true, keySource: "none" }, "Connect your key."],
  [{ available: true, keySource: "saved", verification: "error" }, "Verify your key in Settings, Add-ons."],
  [{ available: true, keySource: "saved", verification: "unknown" }, "Verify your key in Settings, Add-ons."],
])("explains assistant availability with an Add-ons destination: %j", async (assistant, message) => {
  mocks.health.mockResolvedValue({ capabilities: { packaged: true, assistant } });
  render(<AssistantRequired><button>Draft it</button></AssistantRequired>);
  expect(await screen.findByText(message)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Draft it" })).toBeNull();
  expect(screen.getByRole("link", { name: "Open Settings, Add-ons" }).getAttribute("href")).toContain("#addons");
});
