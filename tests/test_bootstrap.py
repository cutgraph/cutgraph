"""Doctor behavior with offline probes and throwaway state."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from pipeline import runtime_tools, secret_store
from scripts import bootstrap


@pytest.fixture
def doctor(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.12"\n')
    (tmp_path / ".nvmrc").write_text("24.14.1\n")
    (tmp_path / "requirements-core.in").write_text("fastapi==0.142.2\npillow==12.3.0\n")
    for folder in ("ui", "motion-core"):
        (tmp_path / folder / "node_modules").mkdir(parents=True)
    font = tmp_path / "font.ttf"
    font.touch()
    monkeypatch.setenv("CUTGRAPH_STATE_HOME", str(tmp_path / "new-state"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(secret_store, "get", lambda _name: None)
    monkeypatch.setattr(runtime_tools, "ffmpeg_path", lambda **_kwargs: tmp_path / "ffmpeg")
    monkeypatch.setattr(runtime_tools, "ffprobe_path", lambda **_kwargs: tmp_path / "ffprobe")
    monkeypatch.setattr(runtime_tools, "font_path", lambda *_args, **_kwargs: font)
    monkeypatch.setattr(runtime_tools, "node_path", lambda **_kwargs: tmp_path / "node")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: name if name == "node" else None)
    monkeypatch.setattr(bootstrap, "_importable", lambda _name, *, load: load)

    def run(command):
        if command[-1] == "--version":
            return "v24.14.1\n"
        if command[-1] == "-version":
            return f"{Path(command[0]).name} version 8.1.1 Copyright\n"
        return " V....D libx264 H.264 encoder\n"

    monkeypatch.setattr(bootstrap, "_run", run)
    collect = bootstrap.collect_checks
    monkeypatch.setattr(bootstrap, "collect_checks", lambda: collect(tmp_path))
    return tmp_path


def test_missing_advisories_do_not_fail_and_do_not_create_state(doctor, capsys):
    assert bootstrap.main([]) == 0
    output = capsys.readouterr().out
    assert "ADVISORY NVIDIA GPU" in output
    assert "ADVISORY transcription pack" in output
    assert "ADVISORY assistant pack" in output
    assert "ADVISORY assistant key" in output
    assert not (doctor / "new-state").exists()


@pytest.mark.parametrize("failure", ["python", "core", "node", "ui", "motion-core", "ffmpeg", "ffprobe", "fonts"])
def test_required_failures_exit_one_with_fix(doctor, monkeypatch, capsys, failure):
    if failure == "python":
        (doctor / "pyproject.toml").write_text('[project]\nrequires-python = ">=99.0"\n')
    elif failure == "core":
        monkeypatch.setattr(bootstrap, "_importable", lambda *_args, **_kwargs: False)
    elif failure == "node":
        (doctor / ".nvmrc").write_text("99.0.0\n")
    elif failure in {"ui", "motion-core"}:
        (doctor / failure / "node_modules").rmdir()
    elif failure in {"ffmpeg", "ffprobe"}:
        monkeypatch.setattr(runtime_tools, f"{failure}_path", lambda **_kwargs: None)
    else:
        (doctor / "font.ttf").unlink()
    assert bootstrap.main([]) == 1
    assert "FAIL " in capsys.readouterr().out
    assert all(check.hint for check in bootstrap.collect_checks() if not check.ok)


@pytest.mark.parametrize("version", ["8.1.1", "n8.1.3-9-g29e619e767", "N-121360-g0d5f1e9a4c-20261001"])
def test_release_tag_and_git_build_versions_are_accepted(doctor, monkeypatch, version):
    monkeypatch.setattr(bootstrap, "_run", lambda command: (
        f"{Path(command[0]).name} version {version} Copyright\n" if command[-1] == "-version" else
        "v24.14.1\n" if command[-1] == "--version" else " V....D libx264 H.264 encoder\n"))
    tools = {check.name: check for check in bootstrap.collect_checks() if check.name in {"ffmpeg", "ffprobe"}}
    assert all(check.ok and version in check.detail for check in tools.values())


def test_json_is_valid_and_advisories_are_structured(doctor, capsys):
    assert bootstrap.main(["--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert len(report["checks"]) == 14
    assert all(check["ok"] for check in report["checks"] if check["required"])
    assert any(not check["ok"] for check in report["checks"] if not check["required"])


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("source", ["environment", "Settings"])
def test_key_reports_only_presence_and_source(doctor, monkeypatch, capsys, source, json_output):
    value = "synthetic-doctor-secret-value"
    if source == "environment":
        monkeypatch.setenv("ANTHROPIC_API_KEY", value)

        def refuse_read(_name):
            raise AssertionError("Environment override must not read stored credentials")

        monkeypatch.setattr(secret_store, "get", refuse_read)
    else:
        monkeypatch.setattr(secret_store, "get", lambda _name: value)
    assert bootstrap.main(["--json"] if json_output else []) == 0
    output = capsys.readouterr().out
    assert source in output
    assert "not verified" in output
    assert value not in output


def test_failed_secret_read_does_not_leak_exception(doctor, monkeypatch, capsys):
    def fail(_name):
        raise secret_store.SecretStoreError("synthetic-doctor-secret-value")

    monkeypatch.setattr(secret_store, "get", fail)
    assert bootstrap.main([]) == 0
    output = capsys.readouterr().out
    assert "could not read protected key" in output
    assert "synthetic-doctor-secret-value" not in output


def test_optional_imports_are_not_loaded_and_import_errors_are_quiet(monkeypatch, capsys):
    def fail(_name):
        print("synthetic-doctor-secret-value")
        raise RuntimeError("synthetic-doctor-secret-value")

    monkeypatch.setattr(bootstrap.importlib, "import_module", fail)
    monkeypatch.setattr(bootstrap.importlib.util, "find_spec", lambda _name: object())
    assert bootstrap._importable("whisperx", load=False)
    assert not bootstrap._importable("fastapi", load=True)
    assert capsys.readouterr().out == ""


def test_probe_failure_is_quiet_and_bounded(monkeypatch, capsys):
    def fail(_command, **kwargs):
        assert kwargs["timeout"] == 15
        raise subprocess.TimeoutExpired("synthetic-doctor-secret-value", 15)

    monkeypatch.setattr(bootstrap.subprocess, "run", fail)
    assert bootstrap._run(["node", "--version"]) is None
    assert capsys.readouterr().out == ""


def test_unwritable_state_and_missing_x264_are_advisory(doctor, monkeypatch, capsys):
    monkeypatch.setattr(bootstrap.os, "access", lambda *_args: False)
    run = bootstrap._run
    monkeypatch.setattr(bootstrap, "_run", lambda command: "" if command[-1] == "-encoders" else run(command))
    assert bootstrap.main([]) == 0
    output = capsys.readouterr().out
    assert "ADVISORY state home" in output
    assert "ADVISORY libx264" in output


def test_main_disables_bytecode_writes_and_restores_setting(doctor, monkeypatch):
    collect = bootstrap.collect_checks
    before = sys.dont_write_bytecode

    def check():
        assert sys.dont_write_bytecode
        return [replace(item, ok=True) for item in collect()]

    monkeypatch.setattr(bootstrap, "collect_checks", check)
    assert bootstrap.main([]) == 0
    assert sys.dont_write_bytecode == before
