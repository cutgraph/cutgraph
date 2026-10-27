"""Studio opts out of telemetry, including environment-driven startup configuration."""
from __future__ import annotations

import asyncio
import logging

import pytest
from starlette.types import Message, Scope

from scripts import studio_app


def test_app_disables_telemetry() -> None:
    assert studio_app.app._telemetry.get("auto_configure") is False
    assert studio_app.app._telemetry.get("tracing") is False
    assert studio_app.app._telemetry.get("metrics") is False
    assert studio_app.app._telemetry.get("logs") is False


def test_startup_does_not_log_telemetry_configuration(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://telemetry.invalid")
    # Unsupported exporters expose accidental configuration without making network requests.
    for signal in ("TRACES", "METRICS", "LOGS"):
        monkeypatch.setenv(f"OTEL_{signal}_EXPORTER", "unsupported")
    caplog.set_level(logging.DEBUG)

    async def scenario() -> list[Message]:
        messages = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
        sent: list[Message] = []

        async def receive() -> Message:
            return next(messages)

        async def send(message: Message) -> None:
            sent.append(message)

        scope: Scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}}
        await studio_app.app(scope, receive, send)
        return sent

    assert asyncio.run(scenario()) == [
        {"type": "lifespan.startup.complete"}, {"type": "lifespan.shutdown.complete"},
    ]
    assert not any("telemetry" in record.getMessage().lower() for record in caplog.records)
