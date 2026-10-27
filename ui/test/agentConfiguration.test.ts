import { describe, expect, it } from "vitest";
import type { ChatConfig } from "../src/api";
import { activeProfile, capabilityHints, settingsForProfile, unavailableHints } from "../src/chat/agentConfiguration";

const config: ChatConfig = {
  schemaVersion: 1,
  selectionDefaults: { providerId: "claude", profileId: "default", model: null, effort: null, mode: "default" },
  models: [{ id: null, label: "Default" }], efforts: ["medium", "high"], modes: ["default", "plan"],
  defaults: { model: null, effort: null, mode: "default" },
  providers: [
    {
      id: "claude", label: "Claude", availability: { available: true, error: null }, profiles: [
        {
          id: "default", label: "Default", model: null, efforts: ["medium", "high"], modes: ["default", "plan"],
          capabilities: { proposalOnly: true, planReadOnly: true, autoFullAccess: false }, availability: { available: true, error: null },
        },
        {
          id: "claude-fable", label: "Claude Fable", model: "claude-fable", efforts: ["high"], modes: ["plan"],
          capabilities: { proposalOnly: true, planReadOnly: true, autoFullAccess: false }, availability: { available: true, error: null },
        },
      ],
    },
  ],
};

describe("agent configuration registry binding", () => {
  it("derives a Claude profile from legacy persisted model-only settings", () => {
    const active = activeProfile(config, { model: "claude-fable", effort: "high", mode: "plan" });
    expect(active?.providerId).toBe("claude");
    expect(active?.profile.id).toBe("claude-fable");
  });

  it("persists the selected fixed provider/profile and normalizes unsupported knobs", () => {
    const profile = activeProfile(config, { providerId: "claude", profileId: "claude-fable", model: null, effort: null, mode: "default" });
    expect(profile).not.toBeNull();
    expect(settingsForProfile(profile!, { model: null, effort: "medium", mode: "default" })).toEqual({
      providerId: "claude", profileId: "claude-fable", model: "claude-fable", effort: null, mode: "plan",
    });
  });

  it("exposes capability and unavailable-adapter hints without enabling unavailable profiles", () => {
    const active = activeProfile(config, { providerId: "claude", profileId: "default", model: null, effort: null, mode: "default" });
    expect(capabilityHints(active?.profile ?? null)).toEqual(["Proposal-mediated edits", "Plan mode is read-only"]);
    expect(unavailableHints(config)).toEqual([]);
    const signedOut: ChatConfig = {
      ...config,
      providers: config.providers.map((provider) => ({
        ...provider,
        profiles: provider.profiles.map((profile) => ({
          ...profile,
          availability: { available: false, error: { code: "authentication_required", message: "Sign in" } },
        })),
      })),
    };
    expect(unavailableHints(signedOut)).toEqual(["Claude: Default — Sign in", "Claude: Claude Fable — Sign in"]);
  });

  it("recovers an unsupported persisted profile using the available Claude default", () => {
    const active = activeProfile(config, {
      providerId: "retired", profileId: "retired", model: "retired", effort: "high", mode: "auto",
    });
    expect(active?.providerId).toBe("claude");
    expect(active?.profile.id).toBe("default");
    expect(settingsForProfile(active!, { model: "retired", effort: "high", mode: "auto" })).toEqual({
      providerId: "claude", profileId: "default", model: null, effort: "high", mode: "default",
    });
  });
});
