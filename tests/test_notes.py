"""Atomic NOTES.md append. Entry format + no-lost-entry under interleaved appends."""
from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest

import config
from pipeline import notes

_PREFIX = "zznote"


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    def _wipe() -> None:
        if config.NOTES_DIR.exists():
            for f in config.NOTES_DIR.glob(f"{_PREFIX}*"):
                f.unlink(missing_ok=True)
    _wipe()
    yield
    _wipe()


def test_append_writes_entry_format() -> None:
    slug = f"{_PREFIX}_fmt"
    notes.append(slug, "Tighten the intro", "Removed 3 filler restarts before the hook.")
    text = notes.read(slug)
    assert text.startswith("## ")
    assert "— Claude: Tighten the intro" in text
    assert "Removed 3 filler restarts before the hook." in text
    assert text.endswith("\n\n")


def test_missing_notes_reads_empty() -> None:
    assert notes.read(f"{_PREFIX}_none") == ""


def test_interleaved_appends_all_survive() -> None:
    slug = f"{_PREFIX}_race"
    n = 25
    barrier = threading.Barrier(n)

    def _worker(i: int) -> None:
        barrier.wait()
        notes.append(slug, f"entry-{i}", f"body-{i}")

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    text = notes.read(slug)
    for i in range(n):
        assert f"— Claude: entry-{i}" in text, f"lost entry {i}"
    assert text.count("## ") == n


def test_bad_slug_rejected() -> None:
    with pytest.raises(ValueError):
        notes.append("../evil", "t", "b")


def test_accept_choose_reject_entries_use_the_claude_header() -> None:
    """discipline: accept ('what I did & why'), choose ('choose:<kind>' / 'picked <label>'), and
    reject ('rejected: <title>' / reason) all land through the same `## <ISO> — Claude: <title>` header
    with the title the studio routes use. Mirrors the route conventions (studio_app accept/choose/reject)."""
    slug = f"{_PREFIX}_disc"
    # accept — the route appends the proposal title + rationale (api_proposal_status, status=="accepted")
    notes.append(slug, "Tighten the intro", "Removed 3 filler restarts before the hook.")
    # choose — the choose route appends f"choose:{kind}" / f"picked {label}"
    notes.append(slug, "choose:hook", "picked Cold cut to the payoff")
    # reject — the status route appends f"rejected: {title}" / reason
    notes.append(slug, "rejected: Cut the pause", "keep the pause")

    text = notes.read(slug)
    assert "— Claude: Tighten the intro" in text
    assert "Removed 3 filler restarts before the hook." in text
    assert "— Claude: choose:hook" in text
    assert "picked Cold cut to the payoff" in text
    assert "— Claude: rejected: Cut the pause" in text
    assert "keep the pause" in text
    assert text.count("## ") == 3
