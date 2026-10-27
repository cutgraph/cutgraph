"""Versioned provider-neutral co-pilot registry.

The registry is deliberately SDK-free. It describes provider capabilities and availability; execution
adapters construct ``AgentSession`` instances separately, so an unavailable provider can never fall
through to the Claude SDK or gain authority it has not implemented.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol

from pipeline.agent_session import AgentSession


REGISTRY_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Availability:
    available: bool
    error_code: str | None = None
    error_message: str | None = None

    def to_wire(self) -> dict[str, Any]:
        error = None if not self.error_code else {
            "code": self.error_code,
            "message": self.error_message or self.error_code,
        }
        return {"available": self.available, "error": error}


@dataclass(frozen=True)
class Capabilities:
    """Authority claims are independent; a mode never implies a broader authority tier."""

    proposal_only: bool = False
    plan_read_only: bool = False
    auto_full_access: bool = False

    def to_wire(self) -> dict[str, bool]:
        return {
            "proposalOnly": self.proposal_only,
            "planReadOnly": self.plan_read_only,
            "autoFullAccess": self.auto_full_access,
        }


@dataclass(frozen=True)
class ProviderProfile:
    provider_id: str
    profile_id: str
    label: str
    model_id: str | None
    efforts: tuple[str, ...]
    modes: tuple[str, ...]
    capabilities: Capabilities
    availability: Availability

    def to_wire(self) -> dict[str, Any]:
        profile = {
            "id": self.profile_id,
            "label": self.label,
            "model": self.model_id,
            "efforts": list(self.efforts),
            "modes": list(self.modes),
            "capabilities": self.capabilities.to_wire(),
            "availability": self.availability.to_wire(),
        }
        return profile


@dataclass(frozen=True)
class Provider:
    provider_id: str
    label: str
    profiles: tuple[ProviderProfile, ...]
    availability: Availability

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.provider_id,
            "label": self.label,
            "availability": self.availability.to_wire(),
            "profiles": [profile.to_wire() for profile in self.profiles],
        }


@dataclass(frozen=True)
class AgentSelection:
    provider_id: str
    profile_id: str
    model_id: str | None
    effort: str | None
    mode: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "providerId": self.provider_id,
            "profileId": self.profile_id,
            "model": self.model_id,
            "effort": self.effort,
            "mode": self.mode,
        }


class ProviderAdapter(Protocol):
    """Contract implemented by a runnable provider-specific code-chat adapter.

    ``create_session`` is called only after ``ProviderRegistry.normalize`` has accepted an available
    profile, and returns the existing shared event/proposal seam rather than a provider-specific stream.
    """

    provider_id: str

    def create_session(self, project: str, context: Any) -> AgentSession:
        ...


@dataclass(frozen=True)
class SelectionRejected(ValueError):
    """A new-contract selection cannot run without changing its provider semantics."""

    code: str
    message: str


def _error(code: str, message: str) -> Availability:
    return Availability(False, code, message)


class ClaudeRegistryAdapter:
    provider_id = "claude"

    def __init__(self, config: Any, availability: Availability | None = None) -> None:
        self._config = config
        self._availability = availability

    def discover(self) -> Provider:
        host_enabled = self._config.AGENT_HOST_MODE in ("inproc", "subprocess")
        availability = self._availability or (Availability(True) if host_enabled else _error(
            "host_disabled", "The configured Claude agent host is disabled."))
        profiles: list[ProviderProfile] = []
        for item in self._config.AGENT_MODELS:
            model = item["id"]
            profile_id = "default" if model is None else str(model)
            profiles.append(ProviderProfile(
                provider_id=self.provider_id,
                profile_id=profile_id,
                label=str(item["label"]),
                model_id=model,
                efforts=tuple(self._config.AGENT_EFFORTS),
                modes=tuple(self._config.AGENT_MODES),
                # Default is proposal-mediated, never unrestricted filesystem or shell access.
                capabilities=Capabilities(proposal_only=True, plan_read_only=True),
                availability=availability,
            ))
        return Provider(self.provider_id, "Claude", tuple(profiles), availability)


class ProviderRegistry:
    def __init__(self, providers: Iterable[Provider], default: AgentSelection) -> None:
        self.providers = tuple(providers)
        self.default = default

    def _profile(self, provider_id: str, profile_id: str) -> ProviderProfile | None:
        for provider in self.providers:
            if provider.provider_id == provider_id:
                return next((p for p in provider.profiles if p.profile_id == profile_id), None)
        return None

    def normalize(self, request: Mapping[str, Any]) -> AgentSelection:
        """Validate a selection, preserving legacy fallback but rejecting explicit unsafe choices."""
        explicit_selection = "providerId" in request or "profileId" in request
        provider_id = request.get("providerId", self.default.provider_id)
        profile_id = request.get("profileId")
        legacy_model = request.get("model")
        if legacy_model in ("", "default"):
            legacy_model = None
        if profile_id is None:
            profile_id = "default" if legacy_model is None else legacy_model
        if not isinstance(provider_id, str) or not isinstance(profile_id, str):
            if explicit_selection:
                raise SelectionRejected("invalid_selection", "Provider and profile IDs must be strings.")
            return self.default
        profile = self._profile(provider_id, profile_id)
        if profile is None:
            if explicit_selection:
                raise SelectionRejected("unknown_profile", "The selected provider profile is not supported.")
            return self.default
        if not profile.availability.available:
            raise SelectionRejected(
                profile.availability.error_code or "provider_unavailable",
                profile.availability.error_message or "The selected provider is unavailable.",
            )
        if "model" in request and legacy_model != profile.model_id:
            if explicit_selection:
                raise SelectionRejected("model_mismatch", "The selected model does not belong to this profile.")
            return self.default
        effort = request.get("effort")
        if effort not in profile.efforts:
            if explicit_selection and "effort" in request and effort is not None:
                raise SelectionRejected("unsupported_effort", "The selected effort is not supported by this profile.")
            effort = self.default.effort if provider_id == self.default.provider_id else None
        mode = request.get("mode")
        if mode not in profile.modes:
            if explicit_selection and "mode" in request:
                raise SelectionRejected("unsupported_mode", "The selected mode is not supported by this profile.")
            mode = (self.default.mode if provider_id == self.default.provider_id
                    else profile.modes[0] if len(profile.modes) == 1 else "")
        if not mode or mode not in profile.modes:
            if explicit_selection:
                raise SelectionRejected("unsupported_mode", "The selected mode is not supported by this profile.")
            return self.default
        return AgentSelection(provider_id, profile_id, profile.model_id, effort, mode)

    def to_wire(self, legacy: Mapping[str, Any]) -> dict[str, Any]:
        """Versioned wire contract with legacy model settings."""
        return {
            "schemaVersion": REGISTRY_SCHEMA_VERSION,
            "providers": [provider.to_wire() for provider in self.providers],
            "selectionDefaults": self.default.to_wire(),
            "models": legacy["models"],
            "efforts": list(legacy["efforts"]),
            "modes": list(legacy["modes"]),
            "defaults": legacy["defaults"],
        }


def registry_from_config(config: Any, *, claude_availability: Availability | None = None) -> ProviderRegistry:
    """Build the Claude registry from fixed product configuration."""
    default_model = config.AGENT_MODEL
    default_profile = "default" if default_model is None else str(default_model)
    default = AgentSelection("claude", default_profile, default_model,
                             config.AGENT_DEFAULT_EFFORT, config.AGENT_DEFAULT_MODE)
    return ProviderRegistry((ClaudeRegistryAdapter(config, claude_availability).discover(),), default)
