"""Offline checks for the Markdown and network disclosures in the public tree."""
from pathlib import Path, PurePosixPath
import re

import pytest

from scripts.public_markdown import _anchors, _link_errors, reachable_files

ROOT = Path(__file__).resolve().parents[1]
HOST_EXEMPTIONS = {
    "json-schema.org": "Schema dialect identifier; the product does not fetch it.",
    "www.w3.org": "SVG/XML namespace identifiers; the product does not fetch them.",
}




def _public_files(root: Path = ROOT) -> set[str]:
    return reachable_files(root)


def test_public_markdown_links_and_heading_anchors():
    public = _public_files()
    assert {"README.md", "docs/getting-started.md", "docs/architecture.md",
            "docs/security-model.md", "docs/privacy.md", "docs/models.md"} <= public
    errors = [error for name in sorted(public) if name.endswith(".md")
              for error in _link_errors(name, (ROOT / name).read_text(encoding="utf-8"), public, ROOT)]
    assert not errors, "\n".join(errors)


def test_architecture_repository_paths_exist():
    text = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
    paths = re.findall(r"`([^`]+)`", text)
    repository_paths = [value for value in paths if re.fullmatch(r"[\w./-]+", value)
                        and ("/" in value or value.endswith(".py"))]
    assert repository_paths
    assert all((ROOT / value).exists() for value in repository_paths), repository_paths


def _source_hosts(text: str) -> set[str]:
    hosts = {match[1].lower() for match in re.finditer(r"https?://(\[[0-9a-fA-F:]+\]|[\w.-]+)", text)}
    hosts |= {match[1].lower() for match in re.finditer(
        r'''["']((?:[a-zA-Z0-9-]+\.)+(?:com|org|net|co|io|dev|edu|app|gov))["']''', text)}
    return hosts - {"127.0.0.1", "localhost", "[::1]"}


def test_product_outbound_hosts_are_disclosed():
    public = {path.relative_to(ROOT).as_posix()
              for folder in ("pipeline", "studio", "scripts", "ui/src", "motion-core/src", "motion-core/bin")
              for path in (ROOT / folder).rglob("*") if path.is_file()}
    documented = set(re.findall(r"`([a-z0-9.-]+\.[a-z]{2,})`", (ROOT / "docs/privacy.md").read_text(encoding="utf-8")))
    findings = []
    for name in sorted(public):
        parts = PurePosixPath(name).parts
        if parts[0] not in {"pipeline", "studio", "scripts", "ui", "motion-core"}:
            continue
        if parts[0] == "ui" and not name.startswith("ui/src/"):
            continue
        if parts[0] == "motion-core" and not name.startswith(("motion-core/src/", "motion-core/bin/")):
            continue
        if "test" in parts or "tests" in parts or ".test." in name or Path(name).name.startswith("test_"):
            continue
        if Path(name).suffix not in {".py", ".ts", ".tsx", ".js", ".mjs", ".css", ".html", ".svg", ".md"}:
            continue
        for host in _source_hosts((ROOT / name).read_text(encoding="utf-8")):
            if host not in documented and host not in HOST_EXEMPTIONS:
                findings.append(f"{name}: {host}")
    assert all(HOST_EXEMPTIONS.values())
    assert not findings, "Undisclosed outbound hosts:\n" + "\n".join(findings)


def test_public_selection_follows_links_and_cycles(tmp_path):
    (tmp_path / "README.md").write_text("[guide](guide.md#details)\n")
    (tmp_path / "guide.md").write_text("# Details\n[home](README.md)\n")
    (tmp_path / "unlinked.md").write_text("[missing](absent.md)\n")
    assert reachable_files(tmp_path, ("README.md",)) == {"README.md", "guide.md"}


@pytest.mark.parametrize("label", ["guide", "the [guide]"])
@pytest.mark.parametrize("link", ["absent.md", "guide.md#absent", "../outside.md"])
def test_public_selection_rejects_invalid_reachable_links(tmp_path, link, label):
    (tmp_path / "README.md").write_text(f"[{label}]({link})\n")
    (tmp_path / "guide.md").write_text("# Details\n")
    with pytest.raises(AssertionError):
        reachable_files(tmp_path, ("README.md",))


def test_github_heading_slugs_and_collisions():
    text = "# Hello, *World*!\n## Hello World\n## Hello World-1\n## `API_key` / café\nSetext\n======\n```md\n# Hidden\n```\n"
    assert _anchors(text) == {"hello-world", "hello-world-1", "hello-world-1-1", "api_key--café", "setext"}


def test_links_images_references_and_missing_anchors_are_checked(tmp_path):
    target = tmp_path / "guide.md"
    target.write_text("# Present heading\n")
    text = ('[good](guide.md#present-heading) ![bad](missing.png)\n'
            '[reference][guide]\n[guide]: guide.md#absent\n'
            '<a href="private.md">private</a>\n'
            '`[ignored](ignored.md)`\n```md\n[ignored](ignored.md)\n```\n')
    errors = _link_errors("README.md", text, {"README.md", "guide.md"}, tmp_path)
    assert any("missing.png" in error for error in errors)
    assert any("missing heading anchor" in error for error in errors)
    assert any("private.md" in error for error in errors)
    assert not any("ignored.md" in error for error in errors)


def test_host_scan_finds_urls_csp_and_bare_domain_literals():
    assert _source_hosts('https://api.example.net/v1; img-src https://cdn.example.org; "example.com"') == {
        "api.example.net", "cdn.example.org", "example.com"}
    assert not _source_hosts("http://127.0.0.1:1234 http://localhost:1234 http://[::1]:1234")
