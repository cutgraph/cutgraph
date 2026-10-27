"""Check required source SBOMs against the core dependency licence policy."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.sbom import NPM_PACKAGES, PYTHON_LOCKS, ROOT, SBOM_FILES, normalized_name


def license_problem(license_name: str, policy: dict[str, Any]) -> str | None:
    if re.search(r"\b(?:AGPL|SSPL|BUSL)(?:-|\b)|Affero|Server Side Public|Business Source|"
                 r"\bNC\b|non[ -]?commercial", license_name, re.IGNORECASE):
        return "disallowed licence"
    if re.search(r"(?<!L)\bGPL(?:-|\b)|GNU (?:General Public|GPL)", license_name, re.IGNORECASE):
        return "disallowed licence"
    aliases = policy["license_aliases"]
    license_name = aliases.get(license_name, license_name)
    tokens = re.findall(r"[A-Za-z0-9.+-]+|[()]", license_name)
    if "".join(tokens) != re.sub(r"\s+", "", license_name) or not tokens:
        return "unknown licence"
    allowed = set(policy["allowed_licenses"])
    exceptions = set(policy["allowed_lgpl_exceptions"])
    index = 0

    def primary() -> str | None:
        nonlocal index
        if index >= len(tokens):
            return "unknown licence"
        token = tokens[index]
        index += 1
        if token == "(":
            problem = expression()
            if index >= len(tokens) or tokens[index] != ")":
                return "unknown licence"
            index += 1
            return problem
        if token in {"AND", "OR", "WITH", ")"}:
            return "unknown licence"
        if token in {"LGPL-2.0", "LGPL-2.0+", "LGPL-2.0-only", "LGPL-2.0-or-later",
                     "LGPL-2.1", "LGPL-2.1+", "LGPL-2.1-only", "LGPL-2.1-or-later",
                     "LGPL-3.0", "LGPL-3.0+", "LGPL-3.0-only", "LGPL-3.0-or-later"}:
            if index + 1 < len(tokens) and tokens[index] == "WITH" and tokens[index + 1] in exceptions:
                index += 2
                return None
            return "LGPL without an approved exception"
        if token not in allowed:
            return "unknown licence"
        return None

    def expression() -> str | None:
        nonlocal index
        problem = primary()
        while index < len(tokens) and tokens[index] in {"AND", "OR"}:
            index += 1
            next_problem = primary()
            problem = problem or next_problem
        return problem

    problem = expression()
    if index != len(tokens):
        return "unknown licence"
    return problem


def load_policy(path: Path) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict) or policy.get("version") != 1:
        raise ValueError("unsupported licence policy")
    for name in ("allowed_licenses", "allowed_lgpl_exceptions"):
        values = policy.get(name)
        if not isinstance(values, list) or not all(isinstance(item, str) and item.strip() for item in values):
            raise ValueError(f"invalid policy field: {name}")
    aliases = policy.get("license_aliases")
    if not isinstance(aliases, dict) or not all(
        isinstance(key, str) and isinstance(value, str) and value in policy["allowed_licenses"]
        for key, value in aliases.items()
    ):
        raise ValueError("invalid licence aliases")
    entries = policy.get("exceptions")
    if not isinstance(entries, list):
        raise ValueError("exceptions must be a list")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(field), str) and entry[field].strip()
            for field in ("purl", "observed_license", "verified_license", "source", "reason")
        ):
            raise ValueError("each exception needs a purl, observed and verified licences, source and written reason")
        if not entry["purl"].startswith(("pkg:pypi/", "pkg:npm/")) or "@" not in entry["purl"]:
            raise ValueError("exceptions must identify an exact package version")
        if not entry["source"].startswith("https://"):
            raise ValueError("exceptions must cite an upstream HTTPS source URL")
        if entry["purl"] in seen:
            raise ValueError("duplicate licence exception")
        seen.add(entry["purl"])
    return policy


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def component_license(component: Any, sbom: str) -> str:
    """The component's licence label; any malformed or ambiguous record fails the whole check."""
    if not isinstance(component, dict) or not all(_text(component.get(key)) for key in ("purl", "name", "version")):
        raise ValueError(f"component without a purl, name and version: {sbom}")
    match = re.fullmatch(r"pkg:(pypi|npm)/(.+)@([^@/]+)", component["purl"])
    if not match:
        raise ValueError(f"invalid component purl in {sbom}: {component['purl']}")
    name = normalized_name(component["name"]) if match[1] == "pypi" else component["name"]
    if (unquote(match[2]), unquote(match[3])) != (name, component["version"]):
        raise ValueError(f"component name or version does not match its purl in {sbom}: {component['purl']}")
    licenses = component.get("licenses")
    if not isinstance(licenses, list) or not licenses:
        raise ValueError(f"component without licences in {sbom}: {component['purl']}")
    values = []
    for entry in licenses:
        if not isinstance(entry, dict) or len(entry) != 1:
            raise ValueError(f"ambiguous licence entry in {sbom}: {component['purl']}")
        if "expression" in entry and _text(entry["expression"]):
            values.append(entry["expression"])
            continue
        info = entry.get("license")
        if not isinstance(info, dict) or len([key for key in ("id", "name") if key in info]) != 1:
            raise ValueError(f"ambiguous licence entry in {sbom}: {component['purl']}")
        value = info.get("id", info.get("name"))
        if not _text(value):
            raise ValueError(f"empty licence in {sbom}: {component['purl']}")
        values.append(value)
    return values[0] if len(values) == 1 else " AND ".join(f"({value})" for value in values)


def check(directory: Path, policy: dict[str, Any]) -> tuple[list[dict[str, str]], int]:
    exceptions = {item["purl"]: item for item in policy["exceptions"]}
    tiers = {f"{Path(lock).stem}.cdx.json": tier for lock, tier in PYTHON_LOCKS.items()}
    tiers.update({f"{name}.cdx.json": "core" for name in NPM_PACKAGES})
    findings = []
    total = 0
    for name in SBOM_FILES:
        doc = json.loads((directory / name).read_text(encoding="utf-8"))
        if doc.get("bomFormat") != "CycloneDX" or doc.get("specVersion") != "1.5":
            raise ValueError(f"invalid CycloneDX SBOM: {name}")
        components = doc.get("components")
        if not isinstance(components, list) or not components:
            raise ValueError(f"missing components: {name}")
        tier = tiers[name]
        for component in components:
            total += 1
            license_name = component_license(component, name)
            problem = license_problem(license_name, policy)
            if not problem:
                continue
            exception = exceptions.get(component["purl"])
            accepted = exception is not None and exception["observed_license"] == license_name
            findings.append({
                "sbom": name, "tier": tier, "purl": component["purl"], "license": license_name,
                "problem": problem, "status": "excepted" if accepted else "reported" if tier == "optional" else "blocked",
                "reason": exception["reason"] if accepted and exception else "",
            })
    return findings, total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sbom-dir", type=Path, default=ROOT / "dist" / "sbom")
    parser.add_argument("--policy", type=Path, default=ROOT / "licenses.policy.json")
    parser.add_argument("--report", type=Path, help="write machine-readable findings")
    args = parser.parse_args(argv)
    try:
        findings, total = check(args.sbom_dir, load_policy(args.policy))
        blocked = sum(item["status"] == "blocked" for item in findings)
        if args.report:
            args.report.write_text(json.dumps({"components": total, "blocked": blocked, "findings": findings},
                                             indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        print(f"error: {exc}")
        return 1
    for finding in findings:
        print(f"{finding['tier']} {finding['status']}: {finding['purl']} [{finding['license']}] {finding['problem']}")
        if finding["reason"]:
            print(f"  {finding['reason']}")
    print(f"Checked {total} components; {blocked} core policy violations.")
    return int(blocked > 0)


if __name__ == "__main__":
    raise SystemExit(main())
