// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ChatConfig } from "../src/api";
import type { StockProviders } from "../src/v3/api";

const stockProviders = vi.fn<() => Promise<StockProviders>>();
const setStockKeys = vi.fn<(keys: Record<string, string | null>) => Promise<StockProviders>>();

vi.mock("../src/api", () => ({
  api: {
    getChatConfig: (): Promise<ChatConfig> => Promise.resolve({
      schemaVersion: 1, providers: [], selectionDefaults: {}, models: [], efforts: [], modes: [], defaults: {},
    } as unknown as ChatConfig),
  },
}));

vi.mock("../src/v3/api", () => ({
  v3api: {
    updates: async () => ({ state: "not_configured", automatic: true, currentVersion: "0.2.0", reason: "Updates not configured." }),
    addons: async () => ({ addons: [], gpus: [], recommendedTier: "cpu" }),
  health: async () => ({ capabilities: { localInference: false, cpuRender: true } }),
    claudeConnection: async () => ({ connected: false, state: "not_connected" }),
    claudeKeyPresence: async () => ({ connected: false, state: "not_connected" }),
    stockProviders: (...args: unknown[]) => stockProviders(...(args as [])),
    setStockKeys: (...args: unknown[]) => setStockKeys(...(args as [Record<string, string | null>])),
  },
}));

import Settings from "../src/v3/Settings";
import ToastHost from "../src/v3/Toast";

beforeEach(() => {
  stockProviders.mockReset();
  setStockKeys.mockReset();
  stockProviders.mockResolvedValue({ pexels: { configured: false }, pixabay: { configured: false } });
});
afterEach(cleanup);

function renderSettings() {
  return render(
    <div className="v3">
      <ToastHost />
      <Settings />
    </div>,
  );
}

describe("Settings: stock API keys", () => {
  it("shows both providers unconfigured, saves a key without ever redisplaying it, and clears the input", async () => {
    renderSettings();
    expect(await screen.findAllByText("No key set — stock search for this provider is off")).toHaveLength(2);

    setStockKeys.mockResolvedValue({ pexels: { configured: true }, pixabay: { configured: false } });
    const [pexelsInput] = screen.getAllByPlaceholderText("Paste API key…");
    fireEvent.change(pexelsInput, { target: { value: "super-secret-pexels-key" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);

    await waitFor(() => expect(setStockKeys).toHaveBeenCalledWith({ pexels: "super-secret-pexels-key" }));
    expect(await screen.findByText("Key configured")).toBeTruthy();
    expect((pexelsInput as HTMLInputElement).value).toBe("");
    expect(document.body.textContent).not.toContain("super-secret-pexels-key");
    expect(screen.getByRole("status", { name: "" }).textContent).toBe("Pexels key saved.");
  });

  it("clears a configured key via the Clear button", async () => {
    stockProviders.mockResolvedValue({ pexels: { configured: false }, pixabay: { configured: true } });
    setStockKeys.mockResolvedValue({ pexels: { configured: false }, pixabay: { configured: false } });
    renderSettings();
    expect(await screen.findByText("Key configured")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(setStockKeys).toHaveBeenCalledWith({ pixabay: null }));
    expect(await screen.findAllByText("No key set — stock search for this provider is off")).toHaveLength(2);
    expect(screen.getByRole("status", { name: "" }).textContent).toBe("Pixabay key cleared.");
  });
});
