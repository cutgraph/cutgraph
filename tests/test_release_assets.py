"""Release collection refuses mismatched payload receipts and hashes every public asset."""
import json

import pytest

from installer.manifest import digest
from installer import release_assets as assets


@pytest.fixture
def output(tmp_path):
    source = tmp_path / "installer"
    source.mkdir()
    name = "CutgraphStudio-0.2.0-setup.exe"
    for filename in (name, *assets.UNSIGNED_FILES):
        (source / filename).write_bytes(b"synthetic artifact")
    receipt = {"installer": name, "unsigned": True, "size": (source / name).stat().st_size,
               "sha256": digest(source / name),
               "ffmpegSource": {"size": (source / assets.SOURCE_ARCHIVE).stat().st_size,
                                "sha256": digest(source / assets.SOURCE_ARCHIVE)}}
    (source / "installer-build.json").write_text(json.dumps(receipt))
    return source


def test_collector_keeps_only_release_assets_and_checksums_every_asset(output, tmp_path):
    (output / "unrelated.txt").write_text("excluded")
    target = tmp_path / "assets"
    names = assets.collect(output, target)
    assert set(names) == {"CutgraphStudio-0.2.0-setup.exe", *assets.UNSIGNED_FILES}
    assert {path.name for path in target.iterdir()} == set(names)
    assert "ffmpeg-corresponding-source.zip" in names
    (target / "release.json").write_text("synthetic manifest")
    (target / "release.json.sig").write_bytes(b"synthetic signature")
    assets.checksums(target)
    content = (target / "SHA256SUMS.txt").read_text()
    for path in target.iterdir():
        if path.name != "SHA256SUMS.txt":
            assert f"{digest(path)}  {path.name}\n" in content
    assert "SHA256SUMS.txt" not in content
    assets.checksums(target)
    assert (target / "SHA256SUMS.txt").read_text() == content


@pytest.mark.parametrize("filename", ["CutgraphStudio-0.2.0-setup.exe", assets.SOURCE_ARCHIVE])
def test_corrupted_release_asset_is_refused(output, tmp_path, filename):
    (output / filename).write_bytes(b"modified artifact")
    with pytest.raises(ValueError, match="does not match"):
        assets.collect(output, tmp_path / "assets")
    assert not (tmp_path / "assets").exists()


@pytest.mark.parametrize("filename", ["THIRD-PARTY-NOTICES.txt", "ffmpeg-corresponding-source.zip"])
def test_missing_notices_or_source_are_refused(output, tmp_path, filename):
    (output / filename).unlink()
    with pytest.raises(ValueError, match="Missing"):
        assets.collect(output, tmp_path / "assets")


def test_checksum_refuses_directories(output):
    (output / "unexpected").mkdir()
    with pytest.raises(ValueError, match="regular files"):
        assets.checksums(output)
