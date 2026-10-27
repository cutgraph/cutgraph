from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
from pipeline import secret_store


@pytest.fixture()
def secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    return config.RUNTIME_DIR / "secrets"


def test_secret_round_trip_and_delete(secrets: Path) -> None:
    assert secret_store.get("sample-api-key") is None
    secret_store.set("sample-api-key", "sample-secret-value")
    assert secret_store.get("sample-api-key") == "sample-secret-value"
    path = secrets / "sample-api-key"
    if os.name == "nt":
        assert b"sample-secret-value" not in path.read_bytes()
    else:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(secrets.stat().st_mode) == 0o700
    secret_store.delete("sample-api-key")
    secret_store.delete("sample-api-key")
    assert secret_store.get("sample-api-key") is None


@pytest.mark.parametrize("name", ["../key", "", "Uppercase", "a/b", "a" * 65, "key\n"])
def test_secret_names_are_validated(secrets: Path, name: str) -> None:
    for operation in (secret_store.get, secret_store.delete):
        with pytest.raises(ValueError, match="invalid secret name"):
            operation(name)
    with pytest.raises(ValueError, match="invalid secret name"):
        secret_store.set(name, "value")
    assert not secrets.exists()


def test_failed_atomic_replace_keeps_previous_secret(secrets: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret_store.set("sample-api-key", "previous-value")

    def fail_replace(*args: object) -> None:
        raise OSError("replacement-value")

    monkeypatch.setattr(secret_store.os, "replace", fail_replace)
    with pytest.raises(secret_store.SecretStoreError, match="^could not store secret$"):
        secret_store.set("sample-api-key", "replacement-value")
    assert secret_store.get("sample-api-key") == "previous-value"
    assert [p.name for p in secrets.iterdir()] == ["sample-api-key"]


@pytest.mark.parametrize("permissions", [0o604, 0o640, 0o660])
def test_posix_refuses_readable_secret_files(
    secrets: Path, monkeypatch: pytest.MonkeyPatch, permissions: int,
) -> None:
    secrets.mkdir(parents=True)
    (secrets / "sample-api-key").write_bytes(b"value")
    monkeypatch.setattr(secret_store, "_WINDOWS", False)
    monkeypatch.setattr(secret_store.os, "fstat", lambda fd: SimpleNamespace(st_mode=stat.S_IFREG | permissions))
    with pytest.raises(secret_store.SecretStoreError, match="permissions are unsafe"):
        secret_store.get("sample-api-key")


def test_posix_store_creates_private_directory_and_file(secrets: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secret_store, "_WINDOWS", False)
    mkdir_modes: list[int] = []
    original_mkdir = Path.mkdir

    def mkdir(path: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False) -> None:
        if path == secrets:
            mkdir_modes.append(mode)
        original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    secret_store.set("sample-api-key", "value")
    assert mkdir_modes and all(mode == 0o700 for mode in mkdir_modes)
    assert (secrets / "sample-api-key").read_bytes() == b"value"
    if os.name != "nt":
        assert stat.S_IMODE((secrets / "sample-api-key").stat().st_mode) == 0o600
