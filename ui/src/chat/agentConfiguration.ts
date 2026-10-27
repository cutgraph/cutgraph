import type { AgentPanelSettings, AgentProfile, ChatConfig } from "../api";

export interface ProfileChoice {
  providerId: string;
  providerLabel: string;
  profile: AgentProfile;
}

export function profileChoices(config: ChatConfig | null): ProfileChoice[] {
  return (config?.providers ?? []).flatMap((provider) =>
    provider.profiles.map((profile) => ({ providerId: provider.id, providerLabel: provider.label, profile })),
  );
}

export function activeProfile(config: ChatConfig | null, settings: AgentPanelSettings): ProfileChoice | null {
  const choices = profileChoices(config);
  return choices.find((choice) =>
    choice.providerId === settings.providerId && choice.profile.id === settings.profileId,
  ) ?? choices.find((choice) =>
    choice.providerId === settings.providerId,
  ) ?? choices.find((choice) =>
    choice.providerId === "claude" && choice.profile.model === settings.model,
  ) ?? choices.find((choice) => choice.profile.availability.available) ?? null;
}

export function settingsForProfile(
  profile: ProfileChoice,
  current: AgentPanelSettings,
): AgentPanelSettings {
  // Claude's existing SDK contract accepts its provider default for every fixed model profile.
  const allowsDefaultEffort = profile.providerId === "claude";
  return {
    providerId: profile.providerId,
    profileId: profile.profile.id,
    model: profile.profile.model,
    effort: profile.profile.efforts.includes(current.effort ?? "") ? current.effort :
      (allowsDefaultEffort ? null : profile.profile.efforts[0] ?? null),
    mode: profile.profile.modes.includes(current.mode) ? current.mode : profile.profile.modes[0] ?? "",
  };
}

export function capabilityHints(profile: AgentProfile | null): string[] {
  if (!profile) return [];
  const hints: string[] = [];
  if (profile.capabilities.proposalOnly) hints.push("Proposal-mediated edits");
  if (profile.capabilities.planReadOnly) hints.push("Plan mode is read-only");
  if (profile.capabilities.autoFullAccess) hints.push("Auto/full access supported");
  if (profile.modes.includes("auto") && !profile.capabilities.autoFullAccess) {
    hints.push("Auto is workspace-scoped, not full access");
  }
  return hints;
}

export function unavailableHints(config: ChatConfig | null): string[] {
  const profiles = profileChoices(config)
    .filter((choice) => !choice.profile.availability.available)
    .map((choice) => `${choice.providerLabel}: ${choice.profile.label} — ${choice.profile.availability.error?.message ?? "Unavailable"}`);
  const sources = (config?.providers ?? [])
    .filter((provider) => provider.profiles.length === 0 && !provider.availability.available)
    .map((provider) => `${provider.label}: ${provider.availability.error?.message ?? "Unavailable"}`);
  return [...profiles, ...sources];
}
