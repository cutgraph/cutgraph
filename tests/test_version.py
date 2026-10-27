import asyncio
import json
from pathlib import Path
import runpy
import tomllib

import httpx
import pytest

from pipeline import version
from scripts import studio_app


def test_version_matches_package_metadata() -> None:
    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert version.__version__ == metadata["project"]["version"]
    npm_version = version.__version__.replace(".dev", "-dev.")
    for package in ("ui", "motion-core"):
        package_metadata = json.loads((root / package / "package.json").read_text(encoding="utf-8"))
        assert package_metadata["version"] == npm_version
        lock = json.loads((root / package / "package-lock.json").read_text(encoding="utf-8"))
        assert lock["version"] == lock["packages"][""]["version"] == npm_version


def test_version_import_needs_no_metadata_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args, **kwargs) -> str:
        raise FileNotFoundError

    monkeypatch.setattr(Path, "read_text", unavailable)
    assert runpy.run_path(version.__file__)["__version__"] == version.__version__


def test_version_api_returns_single_source() -> None:
    async def request() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=studio_app.app), base_url="http://test",
        ) as client:
            return await client.get("/api/version")

    response = asyncio.run(request())
    assert response.status_code == 200
    assert response.json() == {"version": version.__version__}
