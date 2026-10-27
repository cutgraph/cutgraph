"""Release signatures bind exact metadata bytes and installer contents."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import pytest
from packaging.version import Version

from installer import release_manifest as release


@pytest.fixture
def signed(tmp_path):
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"synthetic installer")
    key = Ed25519PrivateKey.generate()
    encoded = base64.b64encode(key.private_bytes_raw()).decode()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    data = release.generate(installer, version="0.2.0", url="https://example.invalid/setup.exe",
                            minimum_version="0.2.0", release_notes_url="https://example.invalid/notes",
                            published_at="2026-01-01T00:00:00Z")
    return installer, data, release.sign(data, encoded), public


def test_manifest_and_installer_verify(signed):
    installer, data, signature, public = signed
    document = release.verify(data, signature, public, installer)
    assert document["channel"] == "stable"
    assert document["installer"]["size"] == installer.stat().st_size
    assert len(signature) == 64


def test_verification_cli_accepts_unsigned_installer_without_platform_signing_tools(tmp_path, signed):
    installer, data, signature, public = signed
    assert installer.read_bytes() == b"synthetic installer"
    manifest = tmp_path / "release.json"
    manifest.write_bytes(data)
    detached = tmp_path / "release.json.sig"
    detached.write_bytes(signature)
    public_key = tmp_path / "public.txt"
    public_key.write_text(public, encoding="ascii")
    command = [sys.executable, "installer/release_manifest.py", "verify", str(manifest),
               "--signature", str(detached), "--public-key", str(public_key), "--installer", str(installer)]
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, "PATH": ""}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    installer.write_bytes(b"x" * installer.stat().st_size)
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, "PATH": ""}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 1


def test_committed_minimum_version_is_stable_and_no_higher_than_product():
    source = Path(__file__).resolve().parents[1] / "pyproject.toml"
    document = tomllib.loads(source.read_text(encoding="utf-8"))
    minimum = document["tool"]["cutgraph"]["minimum-supported-version"]
    release.stable_version(minimum)
    assert Version(minimum) <= Version(document["project"]["version"])


@pytest.mark.parametrize("change", [b" ", b"\n"])
def test_signature_rejects_any_manifest_change(signed, change):
    installer, data, signature, public = signed
    with pytest.raises(ValueError, match="signature"):
        release.verify(data + change, signature, public, installer)


def test_wrong_public_key_is_rejected(signed):
    installer, data, signature, _ = signed
    other = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    with pytest.raises(ValueError, match="signature"):
        release.verify(data, signature, base64.b64encode(other).decode(), installer)


def test_same_size_wrong_installer_hash_is_rejected(signed):
    installer, data, signature, public = signed
    installer.write_bytes(b"x" * installer.stat().st_size)
    with pytest.raises(ValueError, match="SHA-256"):
        release.verify(data, signature, public, installer)


def test_wrong_installer_size_is_rejected(signed):
    installer, data, signature, public = signed
    installer.write_bytes(b"short")
    with pytest.raises(ValueError, match="size"):
        release.verify(data, signature, public, installer)


@pytest.mark.parametrize("version", ["0.2.0.dev0", "v0.2.0", "0.2", "01.2.0", "0.2.0\n"])
def test_unstable_or_ambiguous_version_is_rejected(version):
    with pytest.raises(ValueError, match="stable"):
        release.stable_version(version)


@pytest.mark.parametrize("field,value", [("channel", "preview"), ("minimumVersion", "0.3.0"),
                                         ("publishedAt", "2026-01-01"),
                                         ("releaseNotesUrl", "http://example.invalid/notes")])
def test_invalid_release_contract_is_rejected(signed, field, value):
    document = json.loads(signed[1])
    document[field] = value
    with pytest.raises(ValueError):
        release.validate(document)


def test_keygen_writes_only_chosen_files_and_refuses_overwrite(tmp_path, capsys):
    private, public = tmp_path / "private.txt", tmp_path / "public.txt"
    release.keygen(private, public)
    assert capsys.readouterr() == ("", "")
    assert {path.name for path in tmp_path.iterdir()} == {private.name, public.name}
    original = private.read_bytes()
    assert len(release.decode_key(original.decode())) == 32
    with pytest.raises(ValueError, match="new"):
        release.keygen(private, tmp_path / "another.txt")
    assert private.read_bytes() == original
    with pytest.raises(ValueError):
        release.keygen(tmp_path / "same.txt", tmp_path / "same.txt")


@pytest.mark.parametrize("missing", ["private", "public"])
def test_keygen_checks_both_parents_before_writing(tmp_path, missing):
    private, public = tmp_path / "private.txt", tmp_path / "public.txt"
    if missing == "private":
        private = tmp_path / "missing" / private.name
    else:
        public = tmp_path / "missing" / public.name
    with pytest.raises(ValueError, match="parent directories"):
        release.keygen(private, public)
    assert list(tmp_path.iterdir()) == []


def test_keygen_refuses_existing_public_destination_before_writing_private(tmp_path):
    private, public = tmp_path / "private.txt", tmp_path / "public.txt"
    public.write_text("existing public file", encoding="ascii")
    with pytest.raises(ValueError, match="new"):
        release.keygen(private, public)
    assert not private.exists()
    assert public.read_text(encoding="ascii") == "existing public file"


def test_keygen_public_write_failure_leaves_no_private_file(tmp_path, monkeypatch):
    private, public = tmp_path / "private.txt", tmp_path / "public.txt"

    def refuse(*args, **kwargs):
        raise OSError("Public destination is unavailable")

    monkeypatch.setattr(Path, "open", refuse)
    with pytest.raises(OSError, match="Public destination"):
        release.keygen(private, public)
    assert not private.exists()


def test_keygen_writes_public_before_attempting_private(tmp_path, monkeypatch):
    private, public = tmp_path / "private.txt", tmp_path / "public.txt"

    def refuse(*args, **kwargs):
        assert len(release.decode_key(public.read_text(encoding="ascii"))) == 32
        raise OSError("Private destination is unavailable")

    monkeypatch.setattr(release.os, "open", refuse)
    with pytest.raises(OSError, match="Private destination"):
        release.keygen(private, public)
    assert not private.exists()


@pytest.mark.parametrize("command", ["generate", "verify", "keygen"])
def test_non_signing_cli_reports_specific_errors(tmp_path, monkeypatch, capsys, command):
    path = str(tmp_path / "input")
    arguments = {
        "generate": ["--installer", path, "--version", "0.2.0", "--url", "https://example.invalid/setup.exe",
                     "--minimum-version", "0.2.0", "--release-notes-url", "https://example.invalid/notes", "--out", path],
        "verify": [path, "--signature", path, "--public-key", path],
        "keygen": ["--private-key", path, "--public-key", path],
    }

    def refuse(*args, **kwargs):
        raise ValueError("Specific release diagnostic")

    Path(path).write_bytes(b"synthetic input")
    monkeypatch.setattr(release, command, refuse)
    monkeypatch.setattr(sys, "argv", ["release_manifest.py", command, *arguments[command]])
    assert release.main() == 1
    assert capsys.readouterr() == ("", "Specific release diagnostic\n")


def test_signing_cli_never_echoes_an_invalid_key(tmp_path, signed):
    path = tmp_path / "release.json"
    path.write_bytes(signed[1])
    value = "synthetic-invalid-signing-material"
    command = [sys.executable, "installer/release_manifest.py", "sign", str(path),
               "--out", str(tmp_path / "release.json.sig")]
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, "RELEASE_ED25519_KEY": value},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    assert value not in result.stdout + result.stderr
    assert not (tmp_path / "release.json.sig").exists()
