"""Synthetic shipped files must retain complete notice and SBOM ownership."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

from fastapi import FastAPI
import httpx
import jsonschema
import pytest
from referencing import Registry, Resource

from installer import licensing, sources
from installer.manifest import digest
from pipeline import addons
from scripts import sbom
from studio.routes import system


def put(root: Path, name: str, data: bytes = b"synthetic") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def payload(root: Path) -> tuple[dict[str, str], dict[str, dict]]:
    origins = {}
    pins = {}
    for name, files in {"python": ["python/python.exe", "python/LICENSE.txt"],
                        "node": ["node/node.exe", "node/LICENSE"],
                        "ffmpeg": ["ffmpeg/ffmpeg.exe", "ffmpeg/LICENSE.txt"]}.items():
        pins[name] = {"version": "1", "license": "MIT"}
        for path in files:
            put(root, path)
            origins[path] = "pinned input:" + name
    wheel = put(root, "addons/pip-1-py3-none-any.whl")
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("pip-1.dist-info/licenses/LICENSE.txt", "upstream licence")
    origins[wheel.relative_to(root).as_posix()] = "pinned input:pip"
    pins["pip"] = {"version": "1", "license": "MIT"}
    for path, data in {"LICENSE": b"product licence", "NOTICE": b"product notice",
                       "app/pipeline/version.py": b'__version__ = "1"'}.items():
        put(root, path, data)
        origins[path] = "exported path:" + path.removeprefix("app/")
    package = "python/Lib/site-packages/"
    files = {"example.py": "print('example')", "example-1.dist-info/METADATA":
             "Name: example\nVersion: 1\nLicense-Expression: MIT\n",
             "example-1.dist-info/licenses/LICENSE": "example licence"}
    files["example-1.dist-info/RECORD"] = "".join(name + ",,\n" for name in [*files, "example-1.dist-info/RECORD"])
    for path, text in files.items():
        put(root, package + path, text.encode())
        origins[package + path] = "pinned input:requirements-core.txt"
    return origins, pins


def validate_schema(document: dict) -> None:
    directory = Path(__file__).parent / "fixtures/cyclonedx"
    registry = Registry()
    for path in directory.glob("*.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        registry = registry.with_resource("http://cyclonedx.org/schema/" + path.name, Resource.from_contents(schema))
    schema = json.loads((directory / "bom-1.5.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft7Validator(schema, registry=registry).validate(document)


def test_schema_annotations_do_not_remove_named_properties():
    document = {"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
                "components": [{"type": "library", "name": "example", "description": "description"}]}
    validate_schema(document)
    document["components"][0]["description"] = 123
    with pytest.raises(jsonschema.ValidationError):
        validate_schema(document)


def test_generated_notices_and_sbom_cover_actual_installed_metadata(tmp_path):
    origins, pins = payload(tmp_path)
    package_inventory = "app/motion-core/node_modules/.package-lock.json"
    put(tmp_path, package_inventory, b'{"packages":{}}')
    origins[package_inventory] = "pinned input:motion-core/package-lock.json"
    licensing.generate(tmp_path, origins, pins, {"inputs": []}, tmp_path / "cache", offline=True)
    document = json.loads((tmp_path / licensing.SBOM_FILE).read_text())
    validate_schema(document)
    entries = json.loads((tmp_path / licensing.INVENTORY).read_text())["components"]
    licensing.validate_coverage(tmp_path, origins, entries, document)
    assert {entry["name"] for entry in entries} == {"python", "node", "ffmpeg", "pip", "example", "Cutgraph Studio"}
    notices = (tmp_path / licensing.NOTICE_FILE).read_text(encoding="utf-8")
    assert "LGPLv3" in notices
    assert "contains H.264 code and OpenH264 code (BSD-2-Clause)" in notices
    assert "H.264 may be covered by patents in some countries" in notices
    assert "No patent licence is granted" in notices
    assert str(tmp_path) not in json.dumps(document)
    assert all(component["version"] and component["hashes"] and component["licenses"] for component in document["components"])


def test_source_inputs_accept_safe_notice_names_and_reject_path_injection(tmp_path):
    path = tmp_path / "sources.json"
    item = {"name": "canvas-LICENSE", "sha256": "a" * 64, "url": "https://example.org/LICENSE",
            "version": "1", "license": "MIT"}
    path.write_text(json.dumps({"schemaVersion": 1, "inputs": [item]}))
    assert sources.load_inputs(path)["inputs"] == [item]
    item["name"] = "../LICENSE"
    path.write_text(json.dumps({"schemaVersion": 1, "inputs": [item]}))
    with pytest.raises(ValueError, match="safe names"):
        sources.load_inputs(path)


def test_ui_font_copies_retain_font_attribution(tmp_path):
    origins, pins = payload(tmp_path)
    directory = Path(__file__).resolve().parents[1] / "assets/fonts/inter"
    for source, relative in ((directory / "Inter-Regular.ttf", "app/assets/fonts/inter/Inter-Regular.ttf"),
                             (directory / "OFL.txt", "app/assets/fonts/inter/OFL.txt"),
                             (directory / "Inter-Regular.ttf", "app/ui/dist/assets/font.ttf")):
        target = put(tmp_path, relative)
        shutil.copyfile(source, target)
        origins[relative] = ("generated:ui build from tracked source and ui/package-lock.json"
                             if "/ui/dist/" in relative else "exported path:" + relative.removeprefix("app/"))
    licensing.generate(tmp_path, origins, pins, {"inputs": []}, tmp_path / "cache", offline=True)
    entries = json.loads((tmp_path / licensing.INVENTORY).read_text())["components"]
    font = next(item for item in entries if item["name"] == "Inter-Regular")
    assert font["license"] == "OFL-1.1"
    assert {row["path"] for row in font["files"]} == {
        "app/assets/fonts/inter/Inter-Regular.ttf", "app/assets/fonts/inter/OFL.txt", "app/ui/dist/assets/font.ttf"}
    product = next(item for item in entries if item["name"] == "Cutgraph Studio")
    assert not any(row["path"].endswith(".ttf") for row in product["files"])


@pytest.mark.parametrize("missing", ["notice", "component", "origin"])
def test_missing_notice_sbom_or_origin_fails_closed(tmp_path, missing):
    origins, pins = payload(tmp_path)
    licensing.generate(tmp_path, origins, pins, {"inputs": []}, tmp_path / "cache", offline=True)
    document = json.loads((tmp_path / licensing.SBOM_FILE).read_text())
    entries = json.loads((tmp_path / licensing.INVENTORY).read_text())["components"]
    if missing == "notice":
        (tmp_path / entries[0]["notices"][0]).unlink()
    elif missing == "component":
        document["components"] = document["components"][1:]
    else:
        put(tmp_path, "node/unowned.dll")
        origins["node/unowned.dll"] = "pinned input:node"
    with pytest.raises(ValueError, match="Missing"):
        licensing.validate_coverage(tmp_path, origins, entries, document)


def test_wheel_without_licence_text_is_rejected(tmp_path):
    origins, pins = payload(tmp_path)
    path = "python/Lib/site-packages/example-1.dist-info/licenses/LICENSE"
    (tmp_path / path).unlink()
    origins.pop(path)
    with pytest.raises(ValueError, match="Missing licence"):
        licensing.generate(tmp_path, origins, pins, {"inputs": []}, tmp_path / "cache", offline=True)


def test_source_archive_contains_exact_pinned_inputs_and_is_reproducible(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    data = {"binarySha256": "a" * 64, "ffmpegVersion": "n1", "recipeCommit": "b" * 40,
            "recipeEvidence": "https://example.org/build", "configure": "--enable-version3", "inputs": []}
    for name, license in (("ffmpeg-source", "LGPL-3.0-or-later"), ("ffmpeg-recipe", "MIT"),
                          ("ffmpeg-example", "LGPL-3.0-or-later"), ("ffmpeg-openh264", "BSD-2-Clause")):
        content = name.encode()
        sha = hashlib.sha256(content).hexdigest()
        (cache / (sha + "-" + name)).write_bytes(content)
        data["inputs"].append({"name": name, "url": "https://example.org/" + name,
                               "sha256": sha, "version": "1", "license": license,
                               "group": "ffmpeg", "correspondingSource": True})
    first = sources.produce(data, cache, tmp_path / "first", offline=True)
    second = sources.produce(data, cache, tmp_path / "second", offline=True)
    assert first == second
    artifact = tmp_path / "first" / sources.SOURCE_ARCHIVE
    assert first["artifact"]["sha256"] == digest(artifact)
    with zipfile.ZipFile(artifact) as archive:
        assert archive.read("configure.txt") == b"--enable-version3\n"
        records = json.loads(archive.read("manifest.json"))["sources"]
        assert {item["name"] for item in records} == {item["name"] for item in data["inputs"]}
        for item in records:
            assert archive.read(item["path"]) == item["name"].encode()
    data["inputs"][-1]["correspondingSource"] = False
    with pytest.raises(ValueError, match="All FFmpeg dependency sources"):
        sources.produce(data, cache, tmp_path / "incomplete", offline=True)
    data["unresolved"] = ["exact source unavailable"]
    with pytest.raises(ValueError, match="unresolved"):
        sources.produce(data, cache, tmp_path / "third", offline=True)


def test_committed_source_policy_includes_permissive_ffmpeg_dependencies():
    root = Path(__file__).resolve().parents[1]
    policy = sources.load_inputs(root / "installer/sources.json")
    dependencies = [item for item in policy["inputs"] if item["group"] in {"ffmpeg", "recipe"}]
    assert {item["name"] for item in dependencies} >= {"ffmpeg-source", "ffmpeg-recipe", "ffmpeg-openh264"}
    for item in dependencies:
        if item.get("noticeOnly"):
            assert not item.get("recipe") and not item["correspondingSource"]
        else:
            assert item["correspondingSource"], item["name"]
    openh264 = next(item for item in dependencies if item["name"] == "ffmpeg-openh264")
    assert openh264["license"] == "BSD-2-Clause" and openh264["correspondingSource"]


def test_transcription_notice_links_to_faster_whisper_license_at_locked_tag():
    for addon in addons.transcription_addons():
        links = dict(addon.licenses)
        url = links["faster-whisper (MIT)"]
        match = re.fullmatch(r"https://github.com/SYSTRAN/faster-whisper/blob/v([0-9.]+)/LICENSE", url)
        assert match is not None
        assert f"faster-whisper=={match[1]} " in addon.lock_path.read_text(encoding="utf-8")
        assert {"label": "faster-whisper (MIT)", "url": url} in addon.declaration()["licenses"]


def test_acceptance_binds_wording_and_model_versions(monkeypatch):
    addon = addons.transcription_addons()[0]
    original = addon.acceptance()
    monkeypatch.setattr(addons.models, "manifest", lambda: {"models": [{"revision": "new"}]})
    assert addon.acceptance() != original
    from dataclasses import replace
    assert replace(addon, description="Different terms").acceptance() != addon.acceptance()


def test_distribution_documents_are_fixed_to_bundle_files(tmp_path, monkeypatch):
    put(tmp_path, "THIRD-PARTY-NOTICES.txt", b"notices")
    put(tmp_path, "SBOM.cdx.json", b'{"bomFormat":"CycloneDX"}')
    monkeypatch.setattr(system.runtime_tools, "bundle_root", lambda: tmp_path)
    app = FastAPI()
    app.include_router(system.router)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://studio.test") as client:
            assert (await client.get("/api/distribution/notices")).text == "notices"
            assert (await client.get("/api/distribution/sbom")).json()["bomFormat"] == "CycloneDX"
            assert (await client.get("/api/distribution/other")).status_code == 422
            monkeypatch.setattr(system.runtime_tools, "bundle_root", lambda: None)
            assert (await client.get("/api/distribution/notices")).status_code == 404
    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["/host/file", "../file", "drive:file", "back\\slash"])
def test_bundle_sbom_refuses_host_paths(path):
    with pytest.raises(ValueError, match="relative"):
        sbom.bundle_document([{"ecosystem": "generic", "name": "example", "version": "1", "license": "MIT",
                               "files": [{"path": path, "sha256": "a" * 64}]}])
