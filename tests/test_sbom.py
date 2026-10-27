"""Offline dependency inventory and fail-closed licence policy checks."""
from __future__ import annotations

import base64
import json
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, cast

import pytest

from scripts import license_policy, sbom


ROOT = Path(__file__).resolve().parents[1]


def _metadata(*fields: tuple[str, str]) -> Mapping[str, str]:
    message = Message()
    for key, value in fields:
        message[key] = value
    return cast(Mapping[str, str], message)


def _npm_lock(directory: Path, packages: dict[str, Any]) -> None:
    directory.mkdir(parents=True)
    (directory / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": {"": {"name": "example"}, **packages}}),
        encoding="utf-8",
    )


def _entry(license_name: str = "MIT", **fields: Any) -> dict[str, Any]:
    return {"version": "1.0.0", "license": license_name,
            "integrity": "sha512-" + base64.b64encode(b"x" * 64).decode(), **fields}


def _policy() -> dict[str, Any]:
    return license_policy.load_policy(ROOT / "licenses.policy.json")


def _policy_documents(directory: Path, core: str = "MIT", optional: str = "UNKNOWN") -> None:
    for name in sbom.SBOM_FILES:
        tier = "optional" if "local-ai" in name else "core"
        component = {"type": "library", "name": "example", "version": "1",
                     "purl": "pkg:pypi/example@1", "licenses": [{"expression": optional if tier == "optional" else core}]}
        (directory / name).write_text(json.dumps(sbom.document(name, tier, [component])), encoding="utf-8")


def test_license_resolution_order_and_repeated_classifiers() -> None:
    info = _metadata(("License", "legacy"), ("License-Expression", "MIT OR Apache-2.0"),
                     ("Classifier", "License :: OSI Approved :: BSD License"))
    assert sbom.resolve_license(info) == "MIT OR Apache-2.0"
    assert sbom.resolve_license(_metadata(
        ("License", "legacy"), ("Classifier", "License :: OSI Approved :: MIT License"),
        ("Classifier", "License :: OSI Approved :: Apache Software License"),
        ("Classifier", "License :: OSI Approved"),
    )) == "Apache-2.0 AND MIT"
    proprietary = sbom.resolve_license(_metadata(
        ("Classifier", "License :: OSI Approved :: MIT License"), ("Classifier", "License :: Other/Proprietary License"),
    ))
    assert proprietary == "MIT AND Other/Proprietary License"
    assert license_policy.license_problem(proprietary, _policy()) is not None
    assert sbom.resolve_license(_metadata(("License", " ISC "))) == "ISC"
    assert sbom.resolve_license(_metadata(("License", " "))) == "UNKNOWN"
    assert sbom.resolve_license(None) == "UNKNOWN"


def test_python_inventory_uses_lock_versions_hashes_and_markers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = tmp_path / "requirements.txt"
    lock.write_text("# dependency inventory\nExample_Pkg==2.0 ; python_version >= '3.12' \\\n"
                    " --hash=sha256:" + "b" * 64 + " \\\n --hash=sha256:" + "a" * 64 + "\n"
                    "absent==1.0 --hash=sha256:" + "c" * 64 + "\n"
                    "stale==3.0 --hash=sha256:" + "d" * 64 + "\n", encoding="utf-8")

    def distribution(name: str) -> Any:
        if name == "absent":
            raise sbom.metadata.PackageNotFoundError(name)
        version = "1.0" if name == "stale" else "2.0"
        return SimpleNamespace(version=version, metadata=_metadata(("License-Expression", "MIT")))

    monkeypatch.setattr(sbom.metadata, "distribution", distribution)
    components = sbom.python_components(lock)
    assert [item["purl"] for item in components] == [
        "pkg:pypi/absent@1.0", "pkg:pypi/example-pkg@2.0", "pkg:pypi/stale@3.0"]
    assert [item["licenses"][0]["license"]["name"] for item in components] == ["UNKNOWN", "MIT", "UNKNOWN"]
    assert components[1]["hashes"] == [{"alg": "SHA-256", "content": char * 64} for char in ("a", "b")]
    assert components[1]["properties"] == [{"name": "environment-marker", "value": "python_version >= '3.12'"}]


@pytest.mark.parametrize("entry", ["example>=1", "example==1", "-r another.txt", "example==1 \\",
                                   "example==1 --hash=sha256:bad"])
def test_python_inventory_rejects_incomplete_locks(tmp_path: Path, entry: str) -> None:
    lock = tmp_path / "requirements.txt"
    lock.write_text(entry, encoding="utf-8")
    with pytest.raises(ValueError):
        sbom.python_components(lock)


def test_npm_inventory_covers_production_optional_and_nested_packages(tmp_path: Path) -> None:
    _npm_lock(tmp_path / "ui", {
        "node_modules/@example/widget": _entry(),
        "node_modules/widget/node_modules/nested": _entry("Apache-2.0"),
        "node_modules/optional": _entry(optional=True),
        "node_modules/dev": _entry("GPL-3.0-only", dev=True),
        "node_modules/dev-optional": _entry(devOptional=True),
        "node_modules/fallback": _entry(""),
    })
    package = tmp_path / "ui/node_modules/fallback/package.json"
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({"name": "fallback", "version": "1.0.0", "license": {"type": "ISC"}}),
                       encoding="utf-8")
    components = sbom.npm_components(tmp_path / "ui")
    assert [item["purl"] for item in components] == [
        "pkg:npm/%40example/widget@1.0.0", "pkg:npm/dev-optional@1.0.0", "pkg:npm/fallback@1.0.0",
        "pkg:npm/nested@1.0.0", "pkg:npm/optional@1.0.0",
    ]
    assert components[0]["hashes"] == [{"alg": "SHA-512", "content": (b"x" * 64).hex()}]
    assert components[2]["licenses"] == [{"license": {"name": "ISC"}}]
    package.write_text(json.dumps({"name": "fallback", "version": "other", "license": "MIT"}), encoding="utf-8")
    assert sbom.npm_components(tmp_path / "ui")[2]["licenses"] == [{"license": {"name": "UNKNOWN"}}]


@pytest.mark.parametrize("fields", [{"integrity": "sha512-bad"}, {"integrity": ""}, {"link": True}])
def test_npm_inventory_rejects_missing_or_invalid_integrity(tmp_path: Path, fields: dict[str, Any]) -> None:
    _npm_lock(tmp_path / "ui", {"node_modules/example": _entry(**fields)})
    with pytest.raises(ValueError):
        sbom.npm_components(tmp_path / "ui")


def test_generation_is_deterministic_and_copies_model_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "source"
    root.mkdir()
    for name in sbom.PYTHON_LOCKS:
        (root / name).write_text("example==1 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    for name in sbom.NPM_PACKAGES:
        _npm_lock(root / name, {"node_modules/example": _entry()})
    monkeypatch.setattr(sbom.metadata, "distribution", lambda _: SimpleNamespace(
        version="1", metadata=_metadata(("License-Expression", "MIT")),
    ))
    manifest = root / "models.json"
    manifest.write_text('{"models": []}\n', encoding="utf-8")
    paths = sbom.generate(root, tmp_path / "out", model_manifest=manifest)
    original = {path.name: path.read_bytes() for path in paths}
    assert set(original) == {
        "requirements-core.cdx.json", "requirements-agent.cdx.json",
        "requirements-local-ai-nvidia.cdx.json", "requirements-local-ai-cpu.cdx.json",
        "ui.cdx.json", "motion-core.cdx.json", "model-manifest.json",
    }
    assert original["model-manifest.json"] == manifest.read_bytes()
    assert original == {path.name: path.read_bytes() for path in sbom.generate(root, tmp_path / "out", model_manifest=manifest)}
    for path in paths[:-1]:
        doc = json.loads(path.read_bytes())
        assert (doc["bomFormat"], doc["specVersion"], doc["version"]) == ("CycloneDX", "1.5", 1)
        assert "timestamp" not in doc["metadata"]
        tier = "optional" if "local-ai" in path.name else "core"
        assert doc["metadata"]["properties"] == [{"name": "license-policy-tier", "value": tier}]
    stamped = sbom.generate(root, tmp_path / "out", timestamp=True)
    assert json.loads(stamped[0].read_bytes())["metadata"]["timestamp"].endswith("Z")
    assert not (tmp_path / "out" / "model-manifest.json").exists()


@pytest.mark.parametrize("value", ["GPL-3.0-only", "AGPL-3.0-or-later", "SSPL-1.0", "BUSL-1.1",
                                   "CC-BY-NC-4.0", "Noncommercial", "non-commercial", "LGPL-2.1-only",
                                   "UNKNOWN", "invented", "MIT OR GPL-2.0-only", "MIT AND UNKNOWN",
                                   "MIT OR", "MIT WITH invented", "LGPL-invented WITH GCC-exception-3.1"])
def test_policy_rejects_restricted_unknown_and_malformed_licenses(value: str) -> None:
    assert license_policy.license_problem(value, _policy()) is not None


@pytest.mark.parametrize("value", ["MIT", "MIT-CMU", "(MIT OR Apache-2.0) AND BSD-3-Clause",
                                   "LGPL-3.0-only WITH LGPL-3.0-linking-exception"])
def test_policy_accepts_known_licenses_and_lgpl_exception(value: str) -> None:
    assert license_policy.license_problem(value, _policy()) is None


def test_policy_gates_core_but_only_reports_optional(tmp_path: Path) -> None:
    _policy_documents(tmp_path)
    findings, total = license_policy.check(tmp_path, _policy())
    assert total == 6
    assert {(item["sbom"], item["tier"], item["status"]) for item in findings} == {
        ("requirements-local-ai-nvidia.cdx.json", "optional", "reported"),
        ("requirements-local-ai-cpu.cdx.json", "optional", "reported"),
    }
    _policy_documents(tmp_path, core="GPL-3.0-only")
    findings, _ = license_policy.check(tmp_path, _policy())
    assert sum(item["status"] == "blocked" for item in findings) == 4
    (tmp_path / "ui.cdx.json").unlink()
    with pytest.raises(FileNotFoundError):
        license_policy.check(tmp_path, _policy())


def test_allowlist_requires_written_evidence_and_exact_license(tmp_path: Path) -> None:
    _policy_documents(tmp_path, core="UNKNOWN")
    policy = _policy()
    policy["exceptions"] = [{"purl": "pkg:pypi/example@1", "observed_license": "UNKNOWN", "verified_license": "MIT",
                             "source": "https://example.org/LICENSE", "reason": "Installed licence file states MIT."}]
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    loaded = license_policy.load_policy(path)
    assert all(item["status"] == "excepted" for item in license_policy.check(tmp_path, loaded)[0])
    _policy_documents(tmp_path, core="GPL-3.0-only")
    assert any(item["status"] == "blocked" for item in license_policy.check(tmp_path, loaded)[0])
    policy["exceptions"][0]["reason"] = " "
    path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(ValueError, match="written reason"):
        license_policy.load_policy(path)


def test_cli_exit_codes_and_report(tmp_path: Path) -> None:
    _policy_documents(tmp_path)
    report = tmp_path / "report.json"
    args = ["--sbom-dir", str(tmp_path), "--policy", str(ROOT / "licenses.policy.json"), "--report", str(report)]
    assert license_policy.main(args) == 0
    assert json.loads(report.read_bytes())["blocked"] == 0
    _policy_documents(tmp_path, core="UNKNOWN")
    assert license_policy.main(args) == 1
    assert json.loads(report.read_bytes())["blocked"] == 4
    (tmp_path / "ui.cdx.json").unlink()
    assert license_policy.main(args) == 1


@pytest.mark.parametrize("change", [
    {"licenses": [{"expression": "MIT", "license": {"id": "GPL-3.0-only"}}]},
    {"licenses": [{"license": {"id": "MIT", "name": "GPL-3.0-only"}}]},
    {"licenses": [{"license": {"name": " "}}]},
    {"licenses": []},
    {"licenses": None},
    {"purl": ""},
    {"purl": "example"},
    {"purl": "pkg:cargo/example@1"},
    {"version": None},
    {"version": "99.0"},
    {"name": "other"},
])
def test_policy_fails_closed_on_malformed_or_ambiguous_components(tmp_path: Path, change: dict[str, Any]) -> None:
    _policy_documents(tmp_path)
    path = tmp_path / "ui.cdx.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["components"][0].update(change)
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError):
        license_policy.check(tmp_path, _policy())
    assert license_policy.main(["--sbom-dir", str(tmp_path), "--policy", str(ROOT / "licenses.policy.json")]) == 1


def test_multiple_licence_entries_keep_each_expression_whole(tmp_path: Path) -> None:
    _policy_documents(tmp_path)
    path = tmp_path / "ui.cdx.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["components"][0]["licenses"] = [{"expression": "MIT OR GPL-3.0-only"}, {"license": {"id": "MIT"}}]
    path.write_text(json.dumps(doc), encoding="utf-8")
    findings, _ = license_policy.check(tmp_path, _policy())
    assert [item["license"] for item in findings if item["sbom"] == "ui.cdx.json"] == ["(MIT OR GPL-3.0-only) AND (MIT)"]


def test_generator_requires_an_explicit_model_manifest_choice(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        sbom.main(["--output-dir", str(tmp_path)])
    with pytest.raises(SystemExit):
        sbom.main(["--output-dir", str(tmp_path), "--no-model-manifest", "--model-manifest", str(tmp_path / "m.json")])
