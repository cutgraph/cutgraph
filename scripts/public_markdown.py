"""Offline relative-link traversal for the public Markdown entry documents."""
import html
from pathlib import Path, PurePosixPath
import re
import unicodedata
from urllib.parse import unquote, urlsplit

ENTRY_DOCUMENTS = (
    "README.md", "AGENTS.md", "CONTRIBUTING.md", "SECURITY.md",
    "CODE_OF_CONDUCT.md", "TRADEMARK.md", "CHANGELOG.md",
)


def _without_fences(text: str) -> str:
    lines = []
    fence = ""
    length = 0
    for line in text.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker and not fence:
            fence, length = marker[1][0], len(marker[1])
        elif fence:
            if re.fullmatch(rf" {{0,3}}{re.escape(fence)}{{{length},}}\s*", line):
                fence = ""
        else:
            lines.append(line)
    return "\n".join(lines)


def _inline_text(text: str) -> str:
    text = re.sub(r"!?\[([^]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]^_`{|}~])", r"\1", text)
    return html.unescape(text.replace("`", "").replace("*", ""))


def _anchors(text: str) -> set[str]:
    lines = _without_fences(text).splitlines()
    headings = []
    for index, line in enumerate(lines):
        heading = re.match(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            headings.append(heading[1])
        elif index and re.fullmatch(r" {0,3}(?:=+|-+)\s*", line) and lines[index - 1].strip():
            headings.append(lines[index - 1].strip())
    slugs = set()
    for heading in headings:
        label = _inline_text(heading).lower()
        base = "".join(char for char in label if char in " -_" or unicodedata.category(char)[0] in "LMN")
        base = base.replace(" ", "-")
        slug, suffix = base, 0
        while slug in slugs:
            suffix += 1
            slug = f"{base}-{suffix}"
        slugs.add(slug)
    return slugs


def _links(text: str) -> list[str]:
    text = re.sub(r"(`+).*?\1", "", _without_fences(text))
    target = r'(<[^>\n]+>|(?:[^\s()]|\([^()]*\))+)'
    references = {" ".join(match[1].lower().split()): match[2].strip("<>") for match in
                  re.finditer(r"(?m)^ {0,3}\[([^]]+)\]:\s*" + target, text)}
    text = re.sub(r"(?m)^ {0,3}\[[^]]+\]:.*$", "", text)
    # Link text may hold one level of balanced brackets, as in `[the [guide]](guide.md)`.
    label = r"(?:[^\[\]\n]|\[[^\[\]\n]*\])"
    links = [match[1].strip("<>") for match in re.finditer(
        r'!?\[' + label + r'*\]\(\s*' + target + r'''(?:\s+(?:"[^"\n]*"|'[^'\n]*'|\([^\n]*\)))?\s*\)''', text)]
    links.extend(references.values())
    for match in re.finditer(r"!?\[(" + label + r"+)\]\[([^]\n]*)\]", text):
        name = " ".join((match[2] or match[1]).lower().split())
        assert name in references, f"Undefined link reference: {name}"
        links.append(references[name])
    for match in re.finditer(r"(?<!!)\[(" + label + r"+)\](?![\[(])", text):
        name = " ".join(match[1].lower().split())
        if name in references:
            links.append(references[name])
    links.extend(match[1] for match in re.finditer(r'<(?:a|img)\b[^>]*(?:href|src)=["\']([^"\']+)', text))
    return links


def _link_errors(name: str, text: str, public: set[str], root: Path) -> list[str]:
    errors = []
    for link in _links(text):
        url = urlsplit(html.unescape(link))
        if url.scheme or url.netloc:
            continue
        target = (root / PurePosixPath(name).parent / unquote(url.path)).resolve() if url.path else root / name
        if target.is_dir():
            target /= "README.md"
        try:
            relative = target.relative_to(root.resolve()).as_posix()
        except ValueError:
            errors.append(f"{name}: link escapes public tree: {link}")
            continue
        if relative not in public or not target.is_file():
            errors.append(f"{name}: target is not a reachable file: {link}")
        elif url.fragment and unquote(url.fragment) not in _anchors(target.read_text(encoding="utf-8")):
            errors.append(f"{name}: missing heading anchor: {link}")
    return errors


def reachable_files(root: Path, entries: tuple[str, ...] = ENTRY_DOCUMENTS) -> set[str]:
    root = root.resolve()
    pending = list(entries)
    reached: set[str] = set()
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        path = root / name
        assert path.is_file(), f"Missing public document: {name}"
        reached.add(name)
        if path.suffix.casefold() != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        for link in _links(text):
            url = urlsplit(html.unescape(link))
            if url.scheme or url.netloc:
                continue
            target = (path.parent / unquote(url.path)).resolve() if url.path else path
            if target.is_dir():
                target /= "README.md"
            assert target.is_relative_to(root), f"{name}: link escapes public tree: {link}"
            assert target.is_file(), f"{name}: missing target: {link}"
            if url.fragment:
                assert unquote(url.fragment) in _anchors(target.read_text(encoding="utf-8")), (
                    f"{name}: missing heading anchor: {link}"
                )
            pending.append(target.relative_to(root).as_posix())
    return reached
