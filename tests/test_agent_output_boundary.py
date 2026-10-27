from __future__ import annotations

import io
import json
import logging
import threading
import uuid
from contextlib import contextmanager

import pytest
from uvicorn.logging import AccessFormatter

import config
from pipeline import agent_credentials, decisions, secret_store

KEY = "synthetic-output-boundary-key"


@pytest.fixture(autouse=True)
def credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(config, "DECISIONS_DIR", tmp_path / "decisions")
    monkeypatch.setattr(agent_credentials, "_known_secrets", set())
    monkeypatch.setattr(agent_credentials, "_secret_pattern", None)
    agent_credentials.set_key(KEY)


@contextmanager
def output_logger(name, *formatters):
    logger = logging.getLogger(name)
    level, propagate, handlers = logger.level, logger.propagate, logger.handlers[:]
    streams = [io.StringIO() for _ in formatters]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers = []
    for stream, formatter in zip(streams, formatters):
        handler = logging.StreamHandler(stream)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    try:
        yield logger, streams
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers = handlers
        logger.setLevel(level)
        logger.propagate = propagate


@pytest.mark.parametrize("family", ["claude_agent_sdk._internal", "mcp", "asyncio", "random"])
def test_every_handler_redacts_formatted_arguments_extra_and_exceptions(family):
    name = "asyncio" if family == "asyncio" else f"{family}.{uuid.uuid4().hex}"
    formatters = [logging.Formatter("%(message)s %(credential)s"),
                  logging.Formatter("%(levelname)s %(credential)s %(message)s")]
    with output_logger(name, *formatters) as (logger, streams):
        try:
            raise RuntimeError(KEY)
        except RuntimeError:
            logger.exception("argument %s", KEY, extra={"credential": KEY}, stack_info=True)
        for stream in streams:
            output = stream.getvalue()
            assert KEY not in output
            assert "argument [redacted]" in output
            assert "RuntimeError: [redacted]" in output
            assert "Stack (most recent call last)" in output


def test_formatting_preserves_record_values_and_extra_identity():
    logger = logging.getLogger(f"random.{uuid.uuid4().hex}")
    extra = {"credential": {"text": KEY}}
    record = logger.makeRecord(logger.name, logging.INFO, __file__, 1, "%s", (KEY,), None, extra=extra)
    assert record.msg == "%s"
    assert record.args == (KEY,)
    assert getattr(record, "credential") is extra["credential"]
    assert KEY not in logging.Formatter("%(message)s %(credential)s").format(record)
    assert record.msg == "%s"
    assert record.args == (KEY,)
    assert getattr(record, "credential") is extra["credential"]


@pytest.mark.parametrize("case", ["invalid_character", "tuple_key", "cycle"])
def test_unusual_log_records_do_not_raise_from_redaction(monkeypatch, case):
    monkeypatch.setattr(logging, "raiseExceptions", False)
    with output_logger(f"random.{uuid.uuid4().hex}", logging.Formatter()) as (logger, streams):
        if case == "invalid_character":
            logger.info("%c", 0x110000)
        elif case == "tuple_key":
            logger.info("safe", extra={"input": {("tuple", "key"): KEY}})
        else:
            cyclic = {}
            cyclic["self"] = cyclic
            logger.info("safe", extra={"cycle": cyclic})
        if case != "invalid_character":
            assert streams[0].getvalue() == "safe\n"


def test_access_formatter_keeps_its_five_arguments():
    formatter = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False)
    with output_logger(f"uvicorn.access.{uuid.uuid4().hex}", formatter) as (logger, streams):
        logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1234", "GET", "/api/status", "1.1", 200)
        assert streams[0].getvalue() == '127.0.0.1:1234 - "GET /api/status HTTP/1.1" 200 OK\n'


def test_setting_key_releases_redaction_lock_before_store_write(monkeypatch):
    acquired = threading.Event()
    workers = []

    def store(_name, _value):
        def log_under_store_lock():
            with secret_store._lock:
                with output_logger(f"store.{uuid.uuid4().hex}", logging.Formatter()) as (logger, streams):
                    logger.info("%s", _value)
                    assert streams[0].getvalue() == "[redacted]\n"
                acquired.set()

        worker = threading.Thread(target=log_under_store_lock)
        workers.append(worker)
        worker.start()
        assert acquired.wait(2), "store logging blocked on the redaction lock"

    monkeypatch.setattr(secret_store, "set", store)
    try:
        agent_credentials.set_key("replacement-output-boundary-key")
    finally:
        for worker in workers:
            worker.join(5)
    assert all(not worker.is_alive() for worker in workers)


def test_secret_representations_are_cached_until_keys_change(monkeypatch):
    calls = []
    original_quote = agent_credentials.quote

    def quote(value, **kwargs):
        calls.append(value)
        return original_quote(value, **kwargs)

    monkeypatch.setattr(agent_credentials, "quote", quote)
    key = 'synthetic-escaped-key-"/\\+'
    agent_credentials.set_key(key)
    cached_calls = calls[:]
    assert cached_calls
    text = f"{key} {json.dumps(key)[1:-1]} {original_quote(key, safe='')}"
    for _ in range(2):
        agent_credentials.resolve_key()
        assert agent_credentials.redact(text) == "[redacted] [redacted] [redacted]"
    assert calls == cached_calls
    agent_credentials.set_key("another-output-boundary-key")
    assert len(calls) > len(cached_calls)
    assert agent_credentials.redact(f"{key} another-output-boundary-key") == "[redacted] [redacted]"


def test_decision_redacts_free_text_and_preserves_identifiers():
    event = decisions.record(KEY, decision="manual", intent=f"intent {KEY}", rationale=f"reason {KEY}",
                             proposal_id=KEY, operation_ids=[KEY], evidence_ids=[KEY])
    assert event.projectId == KEY
    assert event.proposalId == KEY
    assert event.operationIds == [KEY]
    assert event.evidenceIds == [KEY]
    assert event.intent == "intent [redacted]"
    assert event.rationale == "reason [redacted]"
    assert decisions.list_for(KEY, scope="project") == [event]
    persisted = json.loads(next(config.DECISIONS_DIR.rglob("*.json")).read_text())
    assert persisted == decisions.to_dict(event)


def test_direct_decision_write_preserves_identifiers_and_redacts_text():
    event = decisions.DecisionEvent(1, "decisionEvent", KEY, KEY, "manual", KEY,
                                    "2026-01-01T00:00:00Z", "project", KEY, KEY, [KEY], [KEY])
    payload = json.loads(decisions.write(event).read_text())
    assert payload["intent"] == payload["rationale"] == "[redacted]"
    for name in ("eventId", "projectId", "proposalId"):
        assert payload[name] == KEY
    assert payload["operationIds"] == payload["evidenceIds"] == [KEY]
