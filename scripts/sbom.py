"""Generate offline CycloneDX 1.5 inventories from Python and npm locks."""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import hashlib
import re
import shutil
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, cast
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
PYTHON_LOCKS = {
    "requirements-core.txt": "core",
    "requirements-agent.txt": "core",
    "requirements-local-ai-nvidia.txt": "optional",
    "requirements-local-ai-cpu.txt": "optional",
}
NPM_PACKAGES = ("ui", "motion-core")
SBOM_FILES = tuple(f"{Path(name).stem}.cdx.json" for name in PYTHON_LOCKS) + tuple(
    f"{name}.cdx.json" for name in NPM_PACKAGES
)
_CLASSIFIERS = {
    "MIT License": "MIT",
    "Apache Software License": "Apache-2.0",
    "ISC License (ISCL)": "ISC",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "Python Software Foundation License": "PSF-2.0",
    "The Unlicense (Unlicense)": "Unlicense",
}


def resolve_license(info: Mapping[str, str] | None) -> str:
    """Prefer PEP 639 expressions, then specific classifiers, then legacy text."""
    if info is None:
        return "UNKNOWN"
    expression = info.get("License-Expression", "").strip()
    if expression:
        return expression
    classifiers = sorted({
        value.rsplit(" :: ", 1)[-1].strip()
        for key, value in info.items()
        if key.lower() == "classifier" and value.startswith("License :: ")
        and value.rsplit(" :: ", 1)[-1].strip() != "OSI Approved"
    })
    if classifiers:
        return " AND ".join(_CLASSIFIERS.get(value, value) for value in classifiers)
    return info.get("License", "").strip() or "UNKNOWN"


def normalized_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _component(ecosystem: str, name: str, version: str, license_name: str) -> dict[str, Any]:
    purl = f"pkg:{ecosystem}/{quote(name, safe='/')}@{quote(version, safe='')}"
    return {
        "type": "library", "bom-ref": purl, "name": name, "version": version,
        "purl": purl, "licenses": [{"license": {"name": license_name}}],
    }


def python_components(lock: Path) -> list[dict[str, Any]]:
    entries: list[str] = []
    pending = ""
    for raw in lock.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pending += line.removesuffix("\\").rstrip() + " "
        if not line.endswith("\\"):
            entries.append(pending.strip())
            pending = ""
    if pending:
        raise ValueError(f"unterminated lock entry in {lock.name}")
    components: dict[str, dict[str, Any]] = {}
    for entry in entries:
        requirement, _, hashes = entry.partition("--hash=")
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s;]+)(?:\s*;(.+))?\s*", requirement)
        if not match or not re.fullmatch(r"sha256:[0-9a-f]{64}(?:\s+--hash=sha256:[0-9a-f]{64})*", hashes):
            raise ValueError(f"expected a pinned, SHA-256 locked requirement in {lock.name}")
        name, version = normalized_name(match[1]), match[2]
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            dist = None
        # Another version's metadata says nothing reliable about the locked one, so it reads as UNKNOWN.
        current = dist is not None and dist.version == version
        license_name = resolve_license(cast(Mapping[str, str], dist.metadata) if dist and current else None)
        component = _component("pypi", name, version, license_name)
        component["hashes"] = [
            {"alg": "SHA-256", "content": digest}
            for digest in sorted(set(re.findall(r"sha256:([0-9a-f]{64})", hashes)))
        ]
        if match[3]:
            component["properties"] = [{"name": "environment-marker", "value": match[3].strip()}]
        key = component["purl"]
        if key in components:
            raise ValueError(f"duplicate locked component: {key}")
        components[key] = component
    return [components[key] for key in sorted(components)]


def _npm_license(value: Any) -> str:
    if isinstance(value, str):
        return value.strip() or "UNKNOWN"
    if isinstance(value, dict):
        return _npm_license(value.get("type"))
    return "UNKNOWN"


def npm_components(directory: Path) -> list[dict[str, Any]]:
    lock = json.loads((directory / "package-lock.json").read_text(encoding="utf-8"))
    if lock.get("lockfileVersion") not in (2, 3) or not isinstance(lock.get("packages"), dict):
        raise ValueError("npm inventory requires a version 2 or 3 package lock")
    components: dict[str, dict[str, Any]] = {}
    for location, entry in sorted(lock["packages"].items()):
        # devOptional packages still install without dev dependencies (optional peers of production code).
        if not location or entry.get("dev"):
            continue
        if not location.startswith("node_modules/") or ".." in Path(location).parts or "\\" in location:
            raise ValueError(f"unsupported npm package location: {location}")
        if entry.get("link") or not entry.get("version"):
            raise ValueError(f"npm component is not pinned: {location}")
        name = entry.get("name") or location.rsplit("node_modules/", 1)[-1]
        license_name = _npm_license(entry.get("license"))
        package = directory / location / "package.json"
        if license_name == "UNKNOWN" and package.is_file():
            installed = json.loads(package.read_text(encoding="utf-8"))
            if installed.get("name") == name and installed.get("version") == entry["version"]:
                license_name = _npm_license(installed.get("license"))
        component = _component("npm", name, entry["version"], license_name)
        hashes = []
        for integrity in entry.get("integrity", "").split():
            algorithm, separator, digest = integrity.partition("-")
            expected_size = {"sha1": 20, "sha256": 32, "sha384": 48, "sha512": 64}.get(algorithm)
            if not separator or expected_size is None:
                raise ValueError(f"unsupported npm integrity: {location}")
            try:
                raw_digest = base64.b64decode(digest, validate=True)
            except binascii.Error as exc:
                raise ValueError(f"invalid npm integrity: {location}") from exc
            if len(raw_digest) != expected_size:
                raise ValueError(f"invalid npm digest size: {location}")
            hashes.append({"alg": "SHA-" + algorithm[3:], "content": raw_digest.hex()})
        if not hashes:
            raise ValueError(f"missing npm integrity: {location}")
        component["hashes"] = sorted(hashes, key=lambda item: (item["alg"], item["content"]))
        key = component["purl"]
        if key in components and components[key] != component:
            raise ValueError(f"conflicting npm component metadata: {key}")
        components[key] = component
    return [components[key] for key in sorted(components)]


def document(name: str, tier: str, components: list[dict[str, Any]], timestamp: bool = False) -> dict[str, Any]:
    info: dict[str, Any] = {
        "component": {"type": "application", "name": name},
        "properties": [{"name": "license-policy-tier", "value": tier}],
    }
    if timestamp:
        info["timestamp"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
        "metadata": info, "components": sorted(components, key=lambda item: item["purl"]),
    }


def bundle_document(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Inventory measured shipped files, independently of the build host's packages."""
    components = []
    for entry in entries:
        if not entry.get("license") or entry["license"] == "UNKNOWN" or not entry.get("files"):
            raise ValueError("Every shipped component needs licence and file evidence")
        component = _component(entry["ecosystem"], entry["name"], entry["version"], entry["license"])
        component["type"] = entry.get("type", "library")
        # A tree hash binds both names and bytes, including multi-file runtime distributions.
        files = sorted(entry["files"], key=lambda row: row["path"])
        for row in files:
            path = PurePosixPath(row["path"])
            if path.is_absolute() or ".." in path.parts or "\\" in row["path"] or ":" in row["path"]:
                raise ValueError("SBOM file evidence must use bundle-relative paths")
            if not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]):
                raise ValueError("SBOM file evidence requires SHA-256")
        tree = "".join(row["path"] + "\0" + row["sha256"] + "\n" for row in files)
        component["hashes"] = [{"alg": "SHA-256", "content": hashlib.sha256(tree.encode()).hexdigest()}]
        component["properties"] = [{"name": "bundle:file-evidence", "value": json.dumps(files, sort_keys=True)}]
        if entry.get("notices"):
            component["properties"].append({"name": "bundle:notices", "value": json.dumps(entry["notices"])})
        components.append(component)
    refs = [item["bom-ref"] for item in components]
    if len(refs) != len(set(refs)):
        raise ValueError("Duplicate shipped SBOM component")
    return document("Cutgraph Studio distribution", "installer", components)


def validate_bundle_document(document: dict, schemas: dict[str, dict]) -> None:
    """Validate offline against the CycloneDX schema constraints."""
    import jsonschema
    from referencing import Registry, Resource

    registry = Registry()
    for name, schema in schemas.items():
        registry = registry.with_resource("http://cyclonedx.org/schema/" + name, Resource.from_contents(schema))
    jsonschema.Draft7Validator(schemas["bom-1.5.schema.json"], registry=registry).validate(document)


def generate(root: Path, output: Path, timestamp: bool = False, model_manifest: Path | None = None) -> list[Path]:
    documents = {
        f"{Path(name).stem}.cdx.json": document(name, tier, python_components(root / name), timestamp)
        for name, tier in PYTHON_LOCKS.items()
    }
    documents.update({
        f"{name}.cdx.json": document(name, "core", npm_components(root / name), timestamp)
        for name in NPM_PACKAGES
    })
    if model_manifest is not None:
        json.loads(model_manifest.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, content in sorted(documents.items()):
        path = output / name
        path.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        paths.append(path)
    target = output / "model-manifest.json"
    if model_manifest is not None:
        if model_manifest.resolve() != target.resolve():
            shutil.copyfile(model_manifest, target)
        paths.append(target)
    else:
        # A manifest left by an earlier run would describe models these SBOMs never named.
        target.unlink(missing_ok=True)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "sbom")
    parser.add_argument("--timestamp", action="store_true", help="include the current UTC time")
    manifest = parser.add_mutually_exclusive_group(required=True)
    manifest.add_argument("--model-manifest", type=Path, help="copy the supplied model manifest into the output")
    manifest.add_argument("--no-model-manifest", action="store_true",
                          help="explicitly produce incomplete evidence without a model manifest")
    args = parser.parse_args(argv)
    try:
        paths = generate(ROOT, args.output_dir, args.timestamp, args.model_manifest)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    for path in paths:
        if path.name.endswith(".cdx.json"):
            count = len(json.loads(path.read_text(encoding="utf-8"))["components"])
            print(f"{path.name}: {count} components")
        else:
            print(path.name)
    if args.no_model_manifest:
        print("No model manifest (--no-model-manifest): this is not complete release evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
