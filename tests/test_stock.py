"""pipeline.stock (Pexels + Pixabay search/import) and its studio_app routes.

Every provider call is recorded: `stock._new_client` is the one seam these tests monkeypatch to an
`httpx.MockTransport`, so nothing here ever reaches the network. Fixtures under
tests/fixtures/stock/ mirror the real Pexels/Pixabay search response shapes. Downloads reuse
test_library.py's ffmpeg-lavfi convention (a tiny real mp4/jpg) so library.import_asset's real
ffprobe/thumbnail path is exercised rather than mocked.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar
from urllib.parse import quote, quote_plus

import httpx
import pytest

import config
from pipeline import library
from pipeline import stock, secret_store
from scripts import studio_app

T = TypeVar("T")

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "stock"
RouteMap = dict[tuple[str, str], "dict | httpx.Response"]


def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=studio_app.app), base_url="http://studio.test")


@pytest.fixture()
def studio_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "outputs" / "projects")
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(config, "RUN_LOG", tmp_path / "run.log")
    config.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    stock._pixabay_cache.clear()
    return tmp_path


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _handler(routes: RouteMap) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        action = routes.get((request.url.host, request.url.path))
        if action is None:
            return httpx.Response(404, json={"error": f"unmapped {request.url}"})
        if isinstance(action, httpx.Response):
            return action
        return httpx.Response(200, json=action)
    return handle


def _mock(monkeypatch: pytest.MonkeyPatch, routes: RouteMap) -> None:
    monkeypatch.setattr(stock, "_new_client", lambda: httpx.Client(transport=httpx.MockTransport(_handler(routes))))


def _mock_raising(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise exc
    monkeypatch.setattr(stock, "_new_client", lambda: httpx.Client(transport=httpx.MockTransport(handle)))


_ALL_ROUTES: RouteMap = {
    ("api.pexels.com", "/videos/search"): _fixture("pexels_video_search.json"),
    ("api.pexels.com", "/v1/search"): _fixture("pexels_photo_search.json"),
    ("pixabay.com", "/api/videos/"): _fixture("pixabay_video_search.json"),
    ("pixabay.com", "/api/"): _fixture("pixabay_photo_search.json"),
}


def _make_video(dest: Path, *, duration: float = 0.5) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=size=64x64:rate=10:duration={duration}",
         "-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=8000", "-t", str(duration),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(dest)],
        check=True, capture_output=True, text=True,
    )


def _make_image(dest: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=blue:size=32x32", "-frames:v", "1", str(dest)],
        check=True, capture_output=True, text=True,
    )


def _video_item(url: str = "https://videos.pexels.com/video-files/123/clip.mp4") -> dict:
    return {
        "id": "pexels:video:123", "provider": "pexels", "kind": "video", "downloadUrl": url,
        "author": "Jane Doe", "authorUrl": "https://www.pexels.com/@jane",
        "sourceUrl": "https://www.pexels.com/video/123/", "license": "Pexels License",
    }


# ── search: merge + dedupe across both providers ─────────────────────────────────

def test_search_merges_both_providers_and_picks_1080p_or_smaller(studio_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEXELS_API_KEY", "pexels-key")
    monkeypatch.setenv("PIXABAY_API_KEY", "pixabay-key")
    _mock(monkeypatch, _ALL_ROUTES)

    result = stock.search("garden", kind="all", page=1)

    assert result["errors"] == {}
    assert result["hasMore"] is True
    assert result["page"] == 1
    ids = [item["id"] for item in result["results"]]
    assert len(ids) == len(set(ids))
    assert len(result["results"]) == 6  # 2 pexels video + 1 pexels photo + 2 pixabay video + 1 pixabay photo

    by_id = {item["id"]: item for item in result["results"]}
    uhd_video = by_id["pexels:video:900001"]
    assert uhd_video["height"] == 1080  # never the 4096x2160 "uhd" file
    assert uhd_video["downloadUrl"] == "https://videos.pexels.com/video-files/900001/900001-hd_1920_1080.mp4"
    assert uhd_video["author"] == "Sample Photographer"
    assert uhd_video["license"] == stock._PEXELS_LICENSE

    photo = by_id["pexels:photo:900003"]
    assert photo["downloadUrl"].startswith("https://images.pexels.com/photos/900003/")
    assert photo["durationS"] is None

    pixabay_4k = by_id["pixabay:video:900005"]
    assert pixabay_4k["height"] == 1080  # never the 3840x2160 "large" file
    assert pixabay_4k["downloadUrl"] == "https://cdn.pixabay.com/video/2021/06/12/900005-medium.mp4"
    assert pixabay_4k["thumbUrl"] == "https://i.vimeocdn.com/video/example-clip_295x166.jpg"

    pixabay_photo = by_id["pixabay:photo:900006"]
    assert pixabay_photo["author"] == "example-photo"
    assert pixabay_photo["license"] == stock._PIXABAY_LICENSE


def test_search_works_with_either_key_alone(studio_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIXABAY_API_KEY", "pixabay-key")
    _mock(monkeypatch, _ALL_ROUTES)

    result = stock.search("garden")

    assert result["errors"] == {}
    assert result["results"]
    assert all(item["provider"] == "pixabay" for item in result["results"])


def test_search_kind_filter_only_calls_that_kind(studio_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEXELS_API_KEY", "pexels-key")
    _mock(monkeypatch, _ALL_ROUTES)

    result = stock.search("garden", kind="photo")

    assert result["results"]
    assert all(item["kind"] == "photo" for item in result["results"])


def test_search_requires_a_configured_provider(studio_state: Path) -> None:
    with pytest.raises(stock.StockError) as exc:
        stock.search("garden")
    assert exc.value.code == "not_configured"
    assert exc.value.status_code == 400


def test_search_requires_a_nonempty_query(studio_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEXELS_API_KEY", "key")
    with pytest.raises(stock.StockError) as exc:
        stock.search("   ")
    assert exc.value.code == "empty_query"


# ── search: provider errors (401 / 429 / timeout) are per-provider, not fatal ────

def test_search_unauthorized_provider_reports_friendly_error_others_still_work(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PEXELS_API_KEY", "good-key")
    monkeypatch.setenv("PIXABAY_API_KEY", "bad-key")
    routes = dict(_ALL_ROUTES)
    routes[("pixabay.com", "/api/videos/")] = httpx.Response(401, json={"error": "invalid key"})
    routes[("pixabay.com", "/api/")] = httpx.Response(401, json={"error": "invalid key"})
    _mock(monkeypatch, routes)

    result = stock.search("garden")

    assert result["errors"]["pixabay"] == stock._ERROR_MESSAGES["unauthorized"]
    assert result["results"]
    assert all(item["provider"] == "pexels" for item in result["results"])


def test_search_rate_limited_provider_reports_friendly_error(studio_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEXELS_API_KEY", "key")
    _mock(monkeypatch, {
        ("api.pexels.com", "/videos/search"): httpx.Response(429, json={}),
        ("api.pexels.com", "/v1/search"): httpx.Response(429, json={}),
    })

    result = stock.search("garden")

    assert result["errors"]["pexels"] == stock._ERROR_MESSAGES["rate_limited"]
    assert result["results"] == []


def test_search_timeout_reports_friendly_error(studio_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEXELS_API_KEY", "key")
    _mock_raising(monkeypatch, httpx.TimeoutException("timed out"))

    result = stock.search("garden")

    assert result["errors"]["pexels"] == stock._ERROR_MESSAGES["timeout"]
    assert result["results"] == []


# ── keys: env vs Settings-file, never leaked ─────────────────────────────────────

def test_provider_status_reflects_env_key(studio_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert stock.provider_status() == {"pexels": {"configured": False}, "pixabay": {"configured": False}}
    monkeypatch.setenv("PEXELS_API_KEY", "abc")
    status = stock.provider_status()
    assert status["pexels"]["configured"] is True
    assert status["pixabay"]["configured"] is False


def test_set_keys_persists_under_state_home_and_is_never_returned(studio_state: Path) -> None:
    status = stock.set_keys({"pexels": "super-secret-pexels-key"})
    assert status == {"pexels": {"configured": True}, "pixabay": {"configured": False}}
    for _key, value in status.items():
        assert value == {"configured": True} or value == {"configured": False}
    assert "super-secret-pexels-key" not in json.dumps(status)

    assert not (studio_state / "runtime" / "stock-keys.json").exists()
    assert secret_store.get("pexels-api-key") == "super-secret-pexels-key"
    assert stock.provider_status()["pexels"]["configured"] is True


def test_set_keys_clears_a_stored_key(studio_state: Path) -> None:
    stock.set_keys({"pixabay": "temp-key"})
    assert stock.provider_status()["pixabay"]["configured"] is True
    stock.set_keys({"pixabay": None})
    assert stock.provider_status()["pixabay"]["configured"] is False


def test_set_keys_rejects_non_string_value(studio_state: Path) -> None:
    with pytest.raises(ValueError):
        stock.set_keys({"pexels": 12345})


def test_env_key_wins_over_stored_key(studio_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stock.set_keys({"pexels": "stored-key"})
    monkeypatch.setenv("PEXELS_API_KEY", "env-key")
    assert stock._resolve_keys()["pexels"] == "env-key"


def test_legacy_keys_migrate_to_protected_store(studio_state: Path) -> None:
    path = stock._keys_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pexels": "legacy-photo-key", "pixabay": "legacy-video-key"}), encoding="utf-8")
    assert all(value["configured"] for value in stock.provider_status().values())
    assert not path.exists()
    assert secret_store.get("pexels-api-key") == "legacy-photo-key"
    assert secret_store.get("pixabay-api-key") == "legacy-video-key"


def test_failed_migration_keeps_plaintext_and_logs_only_generic_message(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    path = stock._keys_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pexels": "legacy-secret-value"}), encoding="utf-8")

    def fail(name: str, value: str) -> None:
        raise secret_store.SecretStoreError(value)

    monkeypatch.setattr(secret_store, "set", fail)
    assert stock.provider_status()["pexels"]["configured"] is False
    assert path.exists()
    assert "Could not migrate stock credentials." in caplog.text
    assert "legacy-secret-value" not in caplog.text


@pytest.mark.parametrize("failure", ["malformed", "unreadable", "undeletable"])
def test_legacy_key_failures_do_not_block_updates_or_restore_cleared_keys(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: str,
) -> None:
    path = stock._keys_path()
    path.parent.mkdir(parents=True)
    legacy = {"pexels": "legacy-private-photo-key", "pixabay": "legacy-private-video-key"}
    path.write_text("{invalid" if failure == "malformed" else json.dumps(legacy), encoding="utf-8")
    read_text = Path.read_text
    unlink = Path.unlink

    def read(self: Path, *args, **kwargs):
        if self == path and failure == "unreadable":
            raise PermissionError(legacy["pexels"])
        return read_text(self, *args, **kwargs)

    def delete(self: Path, *args, **kwargs):
        if self == path and failure == "undeletable":
            raise PermissionError(legacy["pexels"])
        return unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(Path, "unlink", delete)
    assert stock.set_keys({"pexels": "updated-photo-key"})["pexels"]["configured"] is True
    assert stock._resolve_keys()["pexels"] == "updated-photo-key"
    assert stock.set_keys({"pexels": None})["pexels"]["configured"] is False
    assert path.exists()
    if failure == "undeletable":
        assert "pexels" not in json.loads(read_text(path, encoding="utf-8"))
        assert stock._resolve_keys()["pixabay"] == legacy["pixabay"]
        assert stock.set_keys({"pixabay": ""})["pixabay"]["configured"] is False
        assert json.loads(read_text(path, encoding="utf-8")) == {}
    else:
        # A repaired legacy file still cannot restore the provider cleared while it was unreadable.
        path.write_text(json.dumps(legacy), encoding="utf-8")
        monkeypatch.setattr(Path, "read_text", read_text)
        assert stock.provider_status()["pexels"]["configured"] is False
    assert "Could not migrate stock credentials." in caplog.text
    assert all(value not in caplog.text for value in legacy.values())


def test_clearing_key_survives_legacy_rewrite_failure(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    path = stock._keys_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pexels": "legacy-private-key"}), encoding="utf-8")
    unlink = Path.unlink
    replace = stock.os.replace

    def delete(self: Path, *args, **kwargs):
        if self == path:
            raise PermissionError("legacy-private-key")
        return unlink(self, *args, **kwargs)

    def rename(source, destination):
        if destination == path:
            raise PermissionError("legacy-private-key")
        return replace(source, destination)

    monkeypatch.setattr(Path, "unlink", delete)
    monkeypatch.setattr(stock.os, "replace", rename)
    assert stock.set_keys({"pexels": None})["pexels"]["configured"] is False
    assert json.loads(path.read_text(encoding="utf-8")) == {"pexels": "legacy-private-key"}
    assert not list(path.parent.glob(".stock-keys-*"))
    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(stock.os, "replace", replace)
    assert stock.provider_status()["pexels"]["configured"] is False
    assert not path.exists()
    assert "legacy-private-key" not in caplog.text


@pytest.mark.parametrize("value", ["private\nkey", "private\rkey", "private\tkey", " private", "private ",
                                       "private key", "private\x7fkey", "privat\u00e9-key", "x" * 513])
def test_stock_key_charset_rejected_without_echo(
    studio_state: Path, value: str, caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario():
        async with _client() as client:
            return await client.post("/api/stock/keys", json={"pexels": value})

    response = _run(scenario())
    assert response.status_code == 400
    assert response.json() == {"error": "Stock keys must be printable ASCII without whitespace (up to 512 characters).", "code": 'invalid_request'}
    assert not stock.provider_status()["pexels"]["configured"]
    assert value not in response.text + caplog.text
    assert repr(value)[1:-1] not in response.text + caplog.text


def test_newline_key_never_reaches_real_httpx_client_or_logs(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    key = "private-photo\nheader-key"
    caplog.set_level(logging.DEBUG)
    requests = []

    def on_request(request: httpx.Request) -> None:
        requests.append(request)
        pytest.fail("Invalid credentials must be refused before a request")

    with httpx.Client(trust_env=False, event_hooks={"request": [on_request]}) as client:
        monkeypatch.setattr(stock, "_new_client", lambda: client)
        with pytest.raises(ValueError) as exc:
            stock.set_keys({"pexels": key})
        monkeypatch.setenv("PEXELS_API_KEY", key)
        assert stock.provider_status()["pexels"]["configured"] is False
        with pytest.raises(stock.StockError) as search_error:
            stock.search("sample", kind="photo")
        assert search_error.value.code == "not_configured"
    assert requests == []
    assert "Ignoring invalid stock credentials." in caplog.text
    for value in (key, repr(key)[1:-1], quote(key, safe=""), quote_plus(key)):
        assert value not in caplog.text + str(exc.value) + str(search_error.value)


def test_a_rejected_unicode_key_leaves_logging_and_search_working(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ValueError):
        stock.set_keys({"pexels": "bad\ud800key"})
    monkeypatch.setenv("PEXELS_API_KEY", "pexels-key")
    monkeypatch.setenv("PIXABAY_API_KEY", "pixabay-key")
    _mock(monkeypatch, _ALL_ROUTES)
    assert stock.search("garden", kind="all", page=1)["errors"] == {}
    assert "pexels-key" not in caplog.text


@pytest.mark.parametrize("source", ["settings", "environment"])
def test_stock_keys_are_redacted_at_the_log_output_boundary(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, source: str,
) -> None:
    from pipeline import agent_credentials

    key = f"stock-{source}-key/with+symbols"
    if source == "settings":
        stock.set_keys({"pixabay": key})
    else:
        monkeypatch.setenv("PIXABAY_API_KEY", key)
        assert stock.provider_status()["pixabay"]["configured"]
    caplog.set_level(logging.DEBUG)
    logging.getLogger("httpx").info("HTTP Request: GET https://pixabay.com/api/?key=%s", quote_plus(key))
    logging.getLogger("pipeline.unrelated").warning("%r %s", key, quote(key, safe=""))
    assert caplog.text.count("[redacted]") == 3
    for value in (key, repr(key)[1:-1], quote(key, safe=""), quote_plus(key)):
        assert value not in caplog.text
    # Redaction happens when a record is formatted, never by mutating records in a logger filter.
    assert not any(type(f).__module__ == stock.__name__ for f in logging.getLogger("httpx").filters)
    # Stock keys stay out of assistant-data redaction, where a short key would corrupt ordinary text.
    assert agent_credentials.redact(key) == key


def test_invalid_env_key_is_not_configured_even_with_stored_key(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    stock.set_keys({"pexels": "stored-key"})
    monkeypatch.setenv("PEXELS_API_KEY", "invalid env key")
    assert stock.provider_status()["pexels"]["configured"] is False
    assert "Ignoring invalid stock credentials." in caplog.text
    assert "invalid env key" not in caplog.text


def test_printable_ascii_key_boundary_is_supported(studio_state: Path) -> None:
    value = "".join(chr(code) for code in range(0x21, 0x7f))
    key = (value * 6)[:512]
    assert stock.set_keys({"pexels": key})["pexels"]["configured"] is True
    assert stock._resolve_keys()["pexels"] == key


def test_pixabay_cache_normalizes_query_expires_and_bounds_size(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIXABAY_API_KEY", "cache-key")
    now = [0.0]
    calls: list[tuple[str, int, int, str]] = []
    monkeypatch.setattr(stock.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(stock, "PIXABAY_CACHE_SIZE", 2)

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.params["q"], int(request.url.params["page"]),
                      int(request.url.params["per_page"]), request.url.path))
        return httpx.Response(200, json=_fixture("pixabay_photo_search.json"))

    monkeypatch.setattr(stock, "_new_client", lambda: httpx.Client(transport=httpx.MockTransport(handle)))
    first = stock.search("  Sample   QUERY ", kind="photo")
    first["results"][0]["author"] = "modified"
    now[0] = stock.PIXABAY_CACHE_TTL - 1
    cached = stock.search("sample query", kind="photo")
    assert len(calls) == 1
    assert cached["results"][0]["author"] == "example-photo"
    now[0] = stock.PIXABAY_CACHE_TTL
    stock.search("sample query", kind="photo")
    assert len(calls) == 2
    stock.search("sample query", kind="photo", page=2)
    stock.search("sample query", kind="photo", per_page=6)
    assert len(calls) == 4
    assert len(stock._pixabay_cache) == 2
    stock.search("sample query", kind="photo")
    assert len(calls) == 5
    stock.search("sample query", kind="video")
    assert len(calls) == 6


@pytest.mark.parametrize("status", [401, 429, 500])
def test_pixabay_cache_never_caches_errors(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch, status: int,
) -> None:
    monkeypatch.setenv("PIXABAY_API_KEY", "cache-key")
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, json={"error": "unavailable"})

    monkeypatch.setattr(stock, "_new_client", lambda: httpx.Client(transport=httpx.MockTransport(handle)))
    for _ in range(2):
        assert "pixabay" in stock.search("example", kind="photo")["errors"]
    assert len(calls) == 2
    assert not stock._pixabay_cache


def test_search_preserves_attribution_links_for_each_kind(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PEXELS_API_KEY", "photo-key")
    monkeypatch.setenv("PIXABAY_API_KEY", "video-key")
    _mock(monkeypatch, _ALL_ROUTES)
    items = stock.search("example")["results"]
    for item in items:
        assert item["author"]
        assert item["authorUrl"].startswith(f"https://{'www.' if item['provider'] == 'pexels' else ''}{item['provider']}.com/")
        assert item["sourceUrl"].startswith(f"https://{'www.' if item['provider'] == 'pexels' else ''}{item['provider']}.com/")
    photo = next(item for item in items if item["provider"] == "pexels" and item["kind"] == "photo")
    assert photo["sourceUrl"] == _fixture("pexels_photo_search.json")["photos"][0]["url"]


def test_keys_never_appear_in_responses_logs_exceptions_or_job_records(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path,
) -> None:
    from pipeline import jobs

    keys = {"pexels": "private-photo-key/+", "pixabay": "private-video-key/+"}
    caplog.set_level(logging.DEBUG)
    src = tmp_path / "sample.mp4"
    _make_video(src)
    routes = dict(_ALL_ROUTES)
    routes[("videos.pexels.com", "/video-files/123/clip.mp4")] = httpx.Response(
        200, content=src.read_bytes(), headers={"content-type": "video/mp4"},
    )
    _mock(monkeypatch, routes)

    async def scenario() -> list[httpx.Response]:
        async with _client() as client:
            responses = [await client.post("/api/stock/keys", json=keys), await client.get("/api/stock/providers")]
            responses.append(await client.get("/api/stock/search", params={"q": "sample", "kind": "photo"}))
            stock._pixabay_cache.clear()
            routes[("pixabay.com", "/api/")] = httpx.Response(401, text=keys["pixabay"])
            responses.append(await client.get("/api/stock/search", params={"q": "sample", "kind": "photo"}))
            workspace = library.create_workspace("Sample workspace")
            responses.append(await client.post(f"/api/workspaces/{workspace['id']}/stock/import", json=_video_item()))
            return responses

    responses = _run(scenario())
    assert [response.status_code for response in responses] == [200, 200, 200, 200, 201]
    _mock_raising(monkeypatch, httpx.TimeoutException(keys["pexels"]))
    error_result = stock.search("sample", kind="photo")
    with pytest.raises(stock.StockError) as exc:
        stock.import_stock_asset(library.create_workspace("Sample errors")["id"], _video_item())
    evidence = [caplog.text, json.dumps(error_result), str(exc.value), json.dumps(jobs.all_persisted())]
    evidence.extend(response.text for response in responses)
    if config.RUN_LOG.exists():
        evidence.append(config.RUN_LOG.read_text(encoding="utf-8"))
    for value in keys.values():
        for representation in (value, quote(value, safe=""), quote_plus(value)):
            assert all(representation not in text for text in evidence)


def test_run_log_never_receives_a_stock_key(studio_state: Path) -> None:
    stock.set_keys({"pexels": "super-secret-pexels-key"})
    assert not config.RUN_LOG.exists() or "super-secret-pexels-key" not in config.RUN_LOG.read_text(encoding="utf-8")


# ── import: download → library, content-hash dedupe, attribution ────────────────

def test_import_stock_asset_downloads_and_records_attribution(studio_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = library.create_workspace("Broll")
    src = tmp_path / "src.mp4"
    _make_video(src)
    payload = src.read_bytes()
    _mock(monkeypatch, {
        ("videos.pexels.com", "/video-files/123/clip.mp4"):
            httpx.Response(200, content=payload, headers={"content-type": "video/mp4"}),
    })

    asset, deduped = stock.import_stock_asset(workspace["id"], _video_item())

    assert deduped is False
    assert asset["kind"] == "video"
    assert asset["attribution"] == {
        "provider": "pexels", "author": "Jane Doe", "authorUrl": "https://www.pexels.com/@jane",
        "sourceUrl": "https://www.pexels.com/video/123/", "license": "Pexels License",
    }
    file_path = library.asset_file_path(workspace["id"], asset["id"])
    assert file_path is not None and file_path.read_bytes() == payload


def test_import_stock_asset_dedupes_identical_bytes(studio_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = library.create_workspace("Broll")
    src = tmp_path / "src.mp4"
    _make_video(src)
    payload = src.read_bytes()
    _mock(monkeypatch, {
        ("videos.pexels.com", "/video-files/123/clip.mp4"):
            httpx.Response(200, content=payload, headers={"content-type": "video/mp4"}),
    })

    first, deduped1 = stock.import_stock_asset(workspace["id"], _video_item())
    second, deduped2 = stock.import_stock_asset(workspace["id"], {**_video_item(), "id": "pexels:video:999"})

    assert deduped1 is False
    assert deduped2 is True
    assert first["id"] == second["id"]
    assert len(library.list_assets(workspace["id"])) == 1


def test_import_asset_without_attribution_omits_the_field(studio_state: Path, tmp_path: Path) -> None:
    workspace = library.create_workspace("Broll")
    src = tmp_path / "photo.png"
    _make_image(src)
    asset, _ = library.import_asset(workspace["id"], src, display_name="photo.png")
    assert "attribution" not in asset


def test_import_stock_asset_rejects_untrusted_host(studio_state: Path) -> None:
    workspace = library.create_workspace("Broll")
    item = {**_video_item(url="https://evil.example.com/malware.mp4")}
    with pytest.raises(stock.StockError) as exc:
        stock.import_stock_asset(workspace["id"], item)
    assert exc.value.code == "invalid_source"


def test_import_stock_asset_rejects_missing_fields(studio_state: Path) -> None:
    workspace = library.create_workspace("Broll")
    with pytest.raises(stock.StockError) as exc:
        stock.import_stock_asset(workspace["id"], {"provider": "pexels", "kind": "video"})
    assert exc.value.code == "invalid_item"


def test_import_stock_asset_unknown_workspace_raises(studio_state: Path) -> None:
    with pytest.raises(FileNotFoundError):
        stock.import_stock_asset("does-not-exist", _video_item())


# ── import: errors (401 / 429 / timeout / too large) ─────────────────────────────

def test_import_stock_asset_download_unauthorized(studio_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = library.create_workspace("Broll")
    _mock(monkeypatch, {("videos.pexels.com", "/video-files/123/clip.mp4"): httpx.Response(401, json={})})
    with pytest.raises(stock.StockError) as exc:
        stock.import_stock_asset(workspace["id"], _video_item())
    assert exc.value.code == "unauthorized"


def test_import_stock_asset_download_rate_limited(studio_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = library.create_workspace("Broll")
    _mock(monkeypatch, {("videos.pexels.com", "/video-files/123/clip.mp4"): httpx.Response(429, json={})})
    with pytest.raises(stock.StockError) as exc:
        stock.import_stock_asset(workspace["id"], _video_item())
    assert exc.value.code == "rate_limited"


def test_import_stock_asset_download_timeout(studio_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = library.create_workspace("Broll")
    _mock_raising(monkeypatch, httpx.TimeoutException("timed out"))
    with pytest.raises(stock.StockError) as exc:
        stock.import_stock_asset(workspace["id"], _video_item())
    assert exc.value.code == "timeout"


def test_import_stock_asset_download_too_large_is_capped(studio_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = library.create_workspace("Broll")
    monkeypatch.setattr(stock, "MAX_DOWNLOAD_BYTES", 16)
    _mock(monkeypatch, {
        ("videos.pexels.com", "/video-files/123/clip.mp4"):
            httpx.Response(200, content=b"x" * 4096, headers={"content-type": "video/mp4"}),
    })
    with pytest.raises(stock.StockError) as exc:
        stock.import_stock_asset(workspace["id"], _video_item())
    assert exc.value.code == "download_too_large"


# ── HTTP: /api/stock/* routes ─────────────────────────────────────────────────────

def test_http_stock_providers_and_keys_never_leak_the_value(studio_state: Path) -> None:
    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with _client() as client:
            posted = await client.post("/api/stock/keys", json={"pexels": "super-secret-value"})
            got = await client.get("/api/stock/providers")
            return posted, got

    posted, got = _run(scenario())
    assert posted.status_code == 200
    assert posted.json() == {"pexels": {"configured": True}, "pixabay": {"configured": False}}
    assert "super-secret-value" not in posted.text
    assert got.status_code == 200
    assert got.json()["pexels"]["configured"] is True
    assert "super-secret-value" not in got.text


def test_http_stock_search_merges_and_returns_200(studio_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEXELS_API_KEY", "key")
    _mock(monkeypatch, _ALL_ROUTES)

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.get("/api/stock/search", params={"q": "garden", "kind": "all"})

    resp = _run(scenario())
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 3  # pexels only: 2 video + 1 photo
    assert body["hasMore"] is True


def test_http_stock_search_without_a_configured_provider_400(studio_state: Path) -> None:
    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.get("/api/stock/search", params={"q": "garden"})

    resp = _run(scenario())
    assert resp.status_code == 400
    assert resp.json()["error"]


def test_http_stock_search_provider_error_status_propagates(studio_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEXELS_API_KEY", "key")
    monkeypatch.setenv("PIXABAY_API_KEY", "key")
    _mock(monkeypatch, {
        ("api.pexels.com", "/videos/search"): httpx.Response(429, json={}),
        ("api.pexels.com", "/v1/search"): httpx.Response(429, json={}),
        ("pixabay.com", "/api/videos/"): httpx.Response(429, json={}),
        ("pixabay.com", "/api/"): httpx.Response(429, json={}),
    })

    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.get("/api/stock/search", params={"q": "garden"})

    resp = _run(scenario())
    assert resp.status_code == 200  # every provider failed, but that's still a (empty) 200 with errors
    body = resp.json()
    assert body["results"] == []
    assert set(body["errors"]) == {"pexels", "pixabay"}


def test_http_stock_import_creates_asset_and_appears_in_listing(
    studio_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "src.mp4"
    _make_video(src)
    payload = src.read_bytes()
    _mock(monkeypatch, {
        ("videos.pexels.com", "/video-files/123/clip.mp4"):
            httpx.Response(200, content=payload, headers={"content-type": "video/mp4"}),
    })

    async def scenario() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        async with _client() as client:
            created = await client.post("/api/workspaces", json={"name": "Broll"})
            workspace_id = created.json()["id"]
            imported = await client.post(f"/api/workspaces/{workspace_id}/stock/import", json=_video_item())
            listed = await client.get(f"/api/workspaces/{workspace_id}/assets")
            return created, imported, listed

    created, imported, listed = _run(scenario())
    assert created.status_code == 201
    assert imported.status_code == 201
    body = imported.json()
    assert body["deduped"] is False
    assert body["asset"]["attribution"]["provider"] == "pexels"
    assert body["asset"]["attribution"]["sourceUrl"] == "https://www.pexels.com/video/123/"
    assert len(listed.json()["assets"]) == 1


def test_http_stock_import_unknown_workspace_404(studio_state: Path) -> None:
    async def scenario() -> httpx.Response:
        async with _client() as client:
            return await client.post("/api/workspaces/does-not-exist/stock/import", json=_video_item())

    assert _run(scenario()).status_code == 404


def test_http_stock_import_invalid_item_400(studio_state: Path) -> None:
    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with _client() as client:
            created = await client.post("/api/workspaces", json={"name": "Broll"})
            bad = await client.post(
                f"/api/workspaces/{created.json()['id']}/stock/import", json={"provider": "pexels"},
            )
            return created, bad

    created, bad = _run(scenario())
    assert created.status_code == 201
    assert bad.status_code == 400


def test_every_rendered_stock_url_is_allowed_by_the_content_security_policy(
    studio_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from urllib.parse import urlsplit

    from pipeline import web_security

    policy = dict(part.strip().split(" ", 1) for part in web_security.SECURITY_HEADERS["Content-Security-Policy"].split(";"))
    monkeypatch.setenv("PEXELS_API_KEY", "pexels-key")
    monkeypatch.setenv("PIXABAY_API_KEY", "pixabay-key")
    _mock(monkeypatch, _ALL_ROUTES)
    results = stock.search("garden", kind="all", page=1)["results"]
    assert results
    for item in results:
        for field, directive in (("thumbUrl", "img-src"), ("previewUrl", "img-src" if item["kind"] == "photo" else "media-src")):
            url = item.get(field)
            if url:
                origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
                assert origin in policy[directive].split(), f"{item['id']} {field} {origin} not in {directive}"
