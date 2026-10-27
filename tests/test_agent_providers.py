from __future__ import annotations

import config
import pytest
from pipeline.agent_providers import REGISTRY_SCHEMA_VERSION, SelectionRejected, registry_from_config


def _provider(wire: dict, provider_id: str) -> dict:
    return next(provider for provider in wire["providers"] if provider["id"] == provider_id)


def test_registry_wire_contract_is_versioned_and_preserves_claude_settings() -> None:
    registry = registry_from_config(config)
    wire = registry.to_wire({
        "models": config.AGENT_MODELS,
        "efforts": config.AGENT_EFFORTS,
        "modes": config.AGENT_MODES,
        "defaults": {"model": config.AGENT_MODEL, "effort": config.AGENT_DEFAULT_EFFORT,
                     "mode": config.AGENT_DEFAULT_MODE},
    })

    assert wire["schemaVersion"] == REGISTRY_SCHEMA_VERSION == 1
    assert wire["models"] == config.AGENT_MODELS
    assert wire["defaults"] == {"model": None, "effort": None, "mode": "default"}
    assert wire["selectionDefaults"] == {
        "providerId": "claude", "profileId": "default", "model": None,
        "effort": None, "mode": "default",
    }
    claude = _provider(wire, "claude")
    assert [provider["id"] for provider in wire["providers"]] == ["claude"]
    assert claude["profiles"][0] == {
        "id": "default", "label": "Default", "model": None,
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "modes": ["default", "plan"],
        "capabilities": {"proposalOnly": True, "planReadOnly": True, "autoFullAccess": False},
        "availability": {"available": True, "error": None},
    }


def test_normalization_preserves_legacy_claude_selection_and_rejects_cross_provider_inputs() -> None:
    registry = registry_from_config(config)
    selected = registry.normalize({"model": "claude-opus-4-8", "effort": "high", "mode": "plan"})
    assert selected.to_wire() == {
        "providerId": "claude", "profileId": "claude-opus-4-8", "model": "claude-opus-4-8",
        "effort": "high", "mode": "plan",
    }
    # Foreign profiles and mismatched models are rejected.
    with pytest.raises(SelectionRejected, match="not supported"):
        registry.normalize({"providerId": "unknown", "profileId": "default",
                            "model": None, "effort": "high", "mode": "auto"})
    with pytest.raises(SelectionRejected, match="does not belong"):
        registry.normalize({"providerId": "claude", "profileId": "claude-opus-4-8",
                            "model": "claude-haiku-4-5-20251001", "effort": "high",
                            "mode": "plan"})


def test_configured_claude_fable_is_discovered_and_uses_the_existing_claude_adapter(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_MODELS", [
        *config.AGENT_MODELS, {"id": "claude-fable", "label": "Claude Fable"},
    ])
    registry = registry_from_config(config)
    fable = _provider(registry.to_wire({"models": [], "efforts": (), "modes": (), "defaults": {}}), "claude")["profiles"][-1]
    assert fable["id"] == "claude-fable"
    assert fable["availability"] == {"available": True, "error": None}
