from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def runner() -> ModuleType:
    path = Path(__file__).parent / "e2e" / "run.py"
    spec = importlib.util.spec_from_file_location("e2e_cleanup_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("error", [
    subprocess.CalledProcessError(1, "taskkill"),
    subprocess.TimeoutExpired("taskkill", 20),
    OSError("taskkill unavailable"),
])
@pytest.mark.parametrize("wait_times_out", [False, True])
def test_failed_tree_kill_terminates_and_waits(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception, wait_times_out: bool,
) -> None:
    process = MagicMock(spec=subprocess.Popen)
    process.pid = 1234
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired("server", 20), 0] if wait_times_out else [0]
    killer = MagicMock(side_effect=error)
    system_root = tmp_path / "Windows"
    monkeypatch.setattr(runner, "os", SimpleNamespace(name="nt", environ={"SystemRoot": str(system_root)}))
    monkeypatch.setattr(runner.subprocess, "run", killer)

    runner.stop_tree(process)

    killer.assert_called_once_with(
        [str(system_root / "System32" / "taskkill.exe"), "/PID", "1234", "/T", "/F"],
        check=True, capture_output=True, timeout=20,
    )
    process.terminate.assert_called_once_with()
    assert process.wait.call_args_list[0].kwargs == {"timeout": 20}
    if wait_times_out:
        process.kill.assert_called_once_with()
        assert process.wait.call_args_list[1].kwargs == {"timeout": 10}
    else:
        process.kill.assert_not_called()


@pytest.mark.parametrize("environ", [{}, {"SystemRoot": ""}, {"SystemRoot": "Windows"}])
def test_tree_kill_without_an_absolute_system_directory_terminates(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, environ: dict[str, str],
) -> None:
    process = MagicMock(spec=subprocess.Popen)
    process.pid = 1234
    process.poll.return_value = None
    process.wait.side_effect = [0]
    monkeypatch.setattr(runner, "os", SimpleNamespace(name="nt", environ=environ))
    monkeypatch.setattr(runner.subprocess, "run", MagicMock(side_effect=AssertionError("must not run")))

    runner.stop_tree(process)

    process.terminate.assert_called_once_with()


@pytest.mark.parametrize("port_open", [False, True])
def test_cleanup_checks_port_even_when_process_wait_fails(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, port_open: bool,
) -> None:
    process = MagicMock(spec=subprocess.Popen)
    cleanup = MagicMock(side_effect=subprocess.TimeoutExpired("server", 10))
    probe = MagicMock()
    probe.__enter__.return_value = probe
    probe.connect_ex.return_value = 0 if port_open else 1
    monkeypatch.setattr(runner, "stop_tree", cleanup)
    monkeypatch.setattr(runner.socket, "socket", MagicMock(return_value=probe))

    expected = RuntimeError if port_open else subprocess.TimeoutExpired
    with pytest.raises(expected):
        runner.stop_server(process, 43210)

    cleanup.assert_called_once_with(process)
    probe.settimeout.assert_called_once_with(2)
    probe.connect_ex.assert_called_once_with(("127.0.0.1", 43210))
    probe.__exit__.assert_called_once()
