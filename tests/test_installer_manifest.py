"""Synthetic payloads must carry specific registered origins."""
import json

import pytest

from installer import manifest


def payload(tmp_path, name="app/runtime.py"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic")
    return path


@pytest.mark.parametrize("origin", ["generated:anything", "pinned input:unknown",
                                   "exported path:other.py", "arbitrary"])
def test_rejects_fabricated_origins(tmp_path, origin):
    payload(tmp_path)
    with pytest.raises(ValueError, match="Unregistered"):
        manifest.inventory(tmp_path, {"app/runtime.py": origin})


def test_pinned_origin_must_match_registered_destination(tmp_path):
    payload(tmp_path, "node/node.exe")
    with pytest.raises(ValueError, match="Unregistered"):
        manifest.inventory(tmp_path, {"node/node.exe": "pinned input:python"})
    manifest.write_manifest(tmp_path, {"node/node.exe": "pinned input:node"})
    manifest.verify_manifest(tmp_path)


def test_generated_artifact_cannot_register_arbitrary_app_code(tmp_path):
    payload(tmp_path)
    with pytest.raises(ValueError, match="Unregistered"):
        manifest.inventory(tmp_path, {"app/runtime.py": "generated:embedded import paths"})


def test_generated_directory_does_not_admit_an_undeclared_file(tmp_path):
    relative = "app/ui/dist/index.html"
    payload(tmp_path, relative)
    manifest.write_manifest(tmp_path, {relative: "generated:ui build from tracked source and ui/package-lock.json"})
    payload(tmp_path, "app/ui/dist/extra.js")
    with pytest.raises(ValueError, match="Unregistered"):
        manifest.verify_manifest(tmp_path)


def test_certificate_exception_is_specific_to_the_locked_ca_bundle(tmp_path):
    for relative in ("app/cacert.pem", "python/Lib/site-packages/other/cacert.pem"):
        with pytest.raises(ValueError, match="certificate"):
            manifest.check_path(relative)
    relative = "python/Lib/site-packages/certifi/cacert.pem"
    payload(tmp_path, relative)
    manifest.write_manifest(tmp_path, {relative: "pinned input:requirements-core.txt"})
    assert manifest.verify_manifest(tmp_path)["files"][0]["origin"] == "pinned input:requirements-core.txt"


@pytest.mark.parametrize("field,value", [("size", 0), ("schemaVersion", 2)])
def test_manifest_rejects_forged_size_or_unsupported_schema(tmp_path, field, value):
    payload(tmp_path)
    result = manifest.write_manifest(tmp_path, {"app/runtime.py": "exported path:runtime.py"})
    result[field] = value
    (tmp_path / manifest.MANIFEST).write_text(json.dumps(result))
    with pytest.raises(ValueError, match="differ|version"):
        manifest.verify_manifest(tmp_path)


@pytest.mark.parametrize("relative", ["app/CON.txt", "app/CONIN$", "app/conout$.txt", "app/trailing.",
                                      "app/control\x01.txt", "app/\uff21.txt", *(
                                          f"app/invalid{character}.txt" for character in '<>:"|?*')])
def test_manifest_rejects_windows_aliases_and_noncanonical_names(relative):
    with pytest.raises(ValueError, match="Unsafe"):
        manifest.check_path(relative)


@pytest.mark.parametrize("document", [None, [], "text", 1, {"schemaVersion": 1},
                                      {"schemaVersion": 1, "size": 0, "files": None},
                                      {"schemaVersion": 1, "size": 0, "files": [None]},
                                      {"schemaVersion": 1, "size": 0, "files": [{}]}])
def test_malformed_manifest_raises_value_error(tmp_path, document):
    (tmp_path / manifest.MANIFEST).write_text(json.dumps(document))
    with pytest.raises(ValueError, match="manifest|version"):
        manifest.verify_manifest(tmp_path)


@pytest.mark.parametrize("field", ["path", "origin", "sha256", "size"])
@pytest.mark.parametrize("value", [None, [], {}])
def test_malformed_manifest_row_raises_value_error(tmp_path, field, value):
    payload(tmp_path)
    document = manifest.write_manifest(tmp_path, {"app/runtime.py": "exported path:runtime.py"})
    document["files"][0][field] = value
    (tmp_path / manifest.MANIFEST).write_text(json.dumps(document))
    with pytest.raises(ValueError, match="Malformed"):
        manifest.verify_manifest(tmp_path)
