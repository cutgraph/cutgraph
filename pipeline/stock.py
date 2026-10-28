"""Pexels and Pixabay stock search, protected credentials and attributed library imports.

Keys come from `PEXELS_API_KEY` / `PIXABAY_API_KEY` in the environment, which wins, or from Settings, stored through
`pipeline.secret_store`; `provider_status` reports only whether a key is configured. Search normalizes both providers
into one `StockItem` and dedupes by `provider:kind:id`; an import re-downloads the chosen rendition into a workspace
library with its attribution.

`_new_client()` is the seam tests replace with an `httpx.MockTransport`.
Only HTTPS URLs on the providers' hosts are downloaded.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
import uuid
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Literal
from urllib.parse import urlparse

import httpx

import config
from pipeline import agent_credentials, library, secret_store
from pipeline.errors import ValidationError

StockKind = Literal["video", "photo"]
SearchKind = Literal["all", "video", "photo"]

_PROVIDERS = ("pexels", "pixabay")
_PROVIDER_ENV = {"pexels": "PEXELS_API_KEY", "pixabay": "PIXABAY_API_KEY"}
_KEYS_FILE = "stock-keys.json"
_KEYS_LOCK = RLock()
_PIXABAY_CACHE_LOCK = RLock()
PIXABAY_CACHE_TTL = 24 * 60 * 60
PIXABAY_CACHE_SIZE = 128
_pixabay_cache: OrderedDict[tuple[str, str, int, int], tuple[float, tuple[list[dict], bool]]] = OrderedDict()
_logger = logging.getLogger(__name__)

RESULTS_PER_PAGE = 12
MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024  # generous for a single ≤1080p clip or photo
_SEARCH_TIMEOUT = httpx.Timeout(connect=8.0, read=15.0, write=8.0, pool=8.0)
_DOWNLOAD_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)

_DEFAULT_DOWNLOAD_HOSTS = ("pexels.com", "pixabay.com")

_PEXELS_LICENSE = "Pexels License — free to use, no attribution required"
_PIXABAY_LICENSE = "Pixabay License — free for commercial use, no attribution required"

_ERROR_MESSAGES = {
    "not_configured": "Add your Pexels or Pixabay key in Settings.",
    "empty_query": "Type something to search.",
    "invalid_kind": "That search type isn't supported.",
    "unauthorized": "That API key was rejected. Check it in Settings.",
    "rate_limited": "Stock search is rate-limited right now. Try again in a bit.",
    "timeout": "The stock provider timed out. Try again.",
    "network_error": "Couldn't reach the stock provider.",
    "provider_error": "The stock provider returned an error.",
    "invalid_item": "That stock item looked malformed.",
    "invalid_source": "That download link isn't from a supported provider.",
    "download_failed": "The download failed.",
    "download_too_large": "That file is too large to import.",
}

_EXT_BY_CONTENT_TYPE = {
    "video/mp4": ".mp4",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_ALLOWED_EXTS = {".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png", ".webp"}


class StockError(RuntimeError):
    """A browser-safe stock-search/import failure: `code` is stable, `message` is toast-ready."""

    def __init__(self, code: str, status_code: int = 502):
        message = _ERROR_MESSAGES.get(code, code)
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.message = message


def _new_client() -> httpx.Client:
    return httpx.Client()


# ── keys: env (authoritative) + a local Settings-file override ──────────────────

def _keys_path() -> Path:
    return config.RUNTIME_DIR / _KEYS_FILE


def _valid_key(value: str) -> bool:
    return re.fullmatch(r"[\x21-\x7e]{1,512}", value) is not None


def _read_stored_keys() -> dict[str, str]:
    with _KEYS_LOCK:
        path = _keys_path()
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("invalid legacy secret file")
                for provider in _PROVIDERS:
                    value = raw.get(provider)
                    if isinstance(value, str) and value:
                        if not _valid_key(value):
                            raise ValueError("invalid legacy credential")
                        agent_credentials.remember_log_secret(value)
                        name = f"{provider}-api-key"
                        if secret_store.get(name) is None:
                            secret_store.set(name, value)
                path.unlink()
            except (OSError, ValueError, secret_store.SecretStoreError):
                _logger.warning("Could not migrate stock credentials.")
        stored = {}
        for provider in _PROVIDERS:
            try:
                value = secret_store.get(f"{provider}-api-key")
            except secret_store.SecretStoreError:
                _logger.warning("Could not read stock credentials.")
                continue
            if value:
                stored[provider] = value
        return stored


def _resolve_keys() -> dict[str, str]:
    stored = _read_stored_keys()
    keys: dict[str, str] = {}
    for provider, env_name in _PROVIDER_ENV.items():
        value = os.environ.get(env_name, "") or stored.get(provider, "")
        if value:
            if not _valid_key(value):
                _logger.warning("Ignoring invalid stock credentials.")
                continue
            agent_credentials.remember_log_secret(value)
            keys[provider] = value
    return keys


def provider_status() -> dict[str, dict[str, bool]]:
    keys = _resolve_keys()
    return {provider: {"configured": provider in keys} for provider in _PROVIDERS}


def set_keys(patch: object) -> dict[str, dict[str, bool]]:
    """Update protected stock credentials; return only configuration status."""
    if not isinstance(patch, dict):
        raise ValidationError("invalid keys payload")
    for provider in _PROVIDERS:
        if provider not in patch:
            continue
        value = patch[provider]
        if value is not None and (not isinstance(value, str) or (value and not _valid_key(value))):
            raise ValidationError("Stock keys must be printable ASCII without whitespace (up to 512 characters).")
    with _KEYS_LOCK:
        _read_stored_keys()
        for provider in _PROVIDERS:
            if provider not in patch:
                continue
            value = patch[provider] or ""
            if value:
                agent_credentials.remember_log_secret(value)
                secret_store.set(f"{provider}-api-key", value)
            else:
                _remove_legacy_key(provider)
                if _keys_path().exists():
                    # An empty protected value prevents a surviving legacy file from restoring a cleared key.
                    secret_store.set(f"{provider}-api-key", "")
                else:
                    secret_store.delete(f"{provider}-api-key")
    return provider_status()


def _remove_legacy_key(provider: str) -> None:
    path = _keys_path()
    tmp: Path | None = None
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("invalid legacy secret file")
        raw.pop(provider, None)
        if not raw:
            try:
                path.unlink()
                return
            except OSError:
                _logger.warning("Could not delete legacy stock credentials.")
        fd, filename = tempfile.mkstemp(prefix=".stock-keys-", dir=path.parent)
        tmp = Path(filename)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(raw, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except (OSError, ValueError):
        _logger.warning("Could not update legacy stock credentials.")
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _stock_item(
    *, provider: str, kind: StockKind, provider_id: object, thumb_url: str | None,
    preview_url: str | None, download_url: str, width: object, height: object,
    duration_s: object, author: object, author_url: object, source_url: object, license_note: str,
) -> dict:
    def _int(v: object) -> int | None:
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str) and v.strip():
            try:
                return int(float(v))
            except ValueError:
                return None
        return None

    return {
        "id": f"{provider}:{kind}:{provider_id}",
        "provider": provider,
        "kind": kind,
        "thumbUrl": thumb_url,
        "previewUrl": preview_url or thumb_url,
        "downloadUrl": download_url,
        "width": _int(width),
        "height": _int(height),
        "durationS": round(float(duration_s), 1) if isinstance(duration_s, (int, float)) else None,
        "author": (str(author).strip() if author else "") or f"{provider.title()} contributor",
        "authorUrl": author_url or None,
        "sourceUrl": source_url,
        "license": license_note,
    }


def _get(http: httpx.Client, url: str, *, params: dict, headers: dict | None = None) -> httpx.Response:
    try:
        resp = http.get(url, params=params, headers=headers, timeout=_SEARCH_TIMEOUT)
    except httpx.TimeoutException:
        raise StockError("timeout", 504) from None
    except httpx.HTTPError:
        raise StockError("network_error", 502) from None
    if resp.status_code in (401, 403):
        raise StockError("unauthorized", 401)
    if resp.status_code == 429:
        raise StockError("rate_limited", 429)
    if resp.status_code >= 400:
        raise StockError("provider_error", 502)
    return resp


def _pick_by_height(candidates: list[dict], *, url_key: str) -> dict | None:
    """The tallest rendition at or under 1080p, else the shortest available (never nothing)."""
    usable = [c for c in candidates if c.get(url_key) and isinstance(c.get("height"), (int, float))]
    if not usable:
        return None
    fitting = [c for c in usable if c["height"] <= 1080]
    return max(fitting, key=lambda c: c["height"]) if fitting else min(usable, key=lambda c: c["height"])


def _search_pexels(http: httpx.Client, key: str, query: str, kind: StockKind, page: int, per_page: int) -> tuple[list[dict], bool]:
    base = "https://api.pexels.com"
    headers = {"Authorization": key}
    if kind == "video":
        resp = _get(http, f"{base}/videos/search",
                     params={"query": query, "page": page, "per_page": per_page}, headers=headers)
        data = resp.json()
        items = []
        for v in data.get("videos") or []:
            best = _pick_by_height(v.get("video_files") or [], url_key="link")
            if best is None:
                continue
            user = v.get("user") or {}
            items.append(_stock_item(
                provider="pexels", kind="video", provider_id=v.get("id"), thumb_url=v.get("image"),
                preview_url=best.get("link"), download_url=best["link"], width=best.get("width"),
                height=best.get("height"), duration_s=v.get("duration"), author=user.get("name"),
                author_url=user.get("url"), source_url=v.get("url"), license_note=_PEXELS_LICENSE,
            ))
        return items, bool(data.get("next_page"))
    resp = _get(http, f"{base}/v1/search",
                params={"query": query, "page": page, "per_page": per_page}, headers=headers)
    data = resp.json()
    items = []
    for p in data.get("photos") or []:
        src = p.get("src") or {}
        download = src.get("large2x") or src.get("large") or src.get("original")
        if not download:
            continue
        items.append(_stock_item(
            provider="pexels", kind="photo", provider_id=p.get("id"), thumb_url=src.get("medium") or src.get("small"),
            preview_url=src.get("large") or download, download_url=download, width=p.get("width"),
            height=p.get("height"), duration_s=None, author=p.get("photographer"),
            author_url=p.get("photographer_url"), source_url=p.get("url"), license_note=_PEXELS_LICENSE,
        ))
    total = data.get("total_results") or 0
    return items, (page * per_page) < int(total)


def _pixabay_author_url(user: object, user_id: object) -> str | None:
    return f"https://pixabay.com/users/{user}-{user_id}/" if user and user_id else None


def _fetch_pixabay(http: httpx.Client, key: str, query: str, kind: StockKind, page: int, per_page: int) -> tuple[list[dict], bool]:
    base = "https://pixabay.com"
    per_page = max(per_page, 3)  # Pixabay rejects per_page < 3
    if kind == "video":
        resp = _get(http, f"{base}/api/videos/",
                     params={"key": key, "q": query, "page": page, "per_page": per_page})
        data = resp.json()
        items = []
        for h in data.get("hits") or []:
            renditions = [{"url": v.get("url"), "width": v.get("width"), "height": v.get("height")}
                          for v in (h.get("videos") or {}).values() if isinstance(v, dict)]
            best = _pick_by_height(renditions, url_key="url")
            if best is None:
                continue
            thumb = (f"https://i.vimeocdn.com/video/{h['picture_id']}_295x166.jpg"
                     if h.get("picture_id") else best["url"])
            items.append(_stock_item(
                provider="pixabay", kind="video", provider_id=h.get("id"), thumb_url=thumb,
                preview_url=((h.get("videos") or {}).get("small") or {}).get("url") or best["url"],
                download_url=best["url"], width=best.get("width"), height=best.get("height"),
                duration_s=h.get("duration"), author=h.get("user"),
                author_url=_pixabay_author_url(h.get("user"), h.get("user_id")),
                source_url=h.get("pageURL"), license_note=_PIXABAY_LICENSE,
            ))
        total = data.get("totalHits") or data.get("total") or 0
        return items, (page * per_page) < int(total)
    resp = _get(http, f"{base}/api/",
                params={"key": key, "q": query, "page": page, "per_page": per_page})
    data = resp.json()
    items = []
    for h in data.get("hits") or []:
        download = h.get("largeImageURL") or h.get("webformatURL")
        if not download:
            continue
        items.append(_stock_item(
            provider="pixabay", kind="photo", provider_id=h.get("id"), thumb_url=h.get("webformatURL"),
            preview_url=h.get("largeImageURL") or h.get("webformatURL"), download_url=download,
            width=h.get("imageWidth"), height=h.get("imageHeight"), duration_s=None, author=h.get("user"),
            author_url=_pixabay_author_url(h.get("user"), h.get("user_id")),
            source_url=h.get("pageURL"), license_note=_PIXABAY_LICENSE,
        ))
    total = data.get("totalHits") or data.get("total") or 0
    return items, (page * per_page) < int(total)


def _search_pixabay(http: httpx.Client, key: str, query: str, kind: StockKind, page: int, per_page: int) -> tuple[list[dict], bool]:
    query = " ".join(query.split()).casefold()
    per_page = max(per_page, 3)
    cache_key = (kind, query, page, per_page)
    with _PIXABAY_CACHE_LOCK:
        now = time.monotonic()
        cached = _pixabay_cache.get(cache_key)
        if cached is not None and now - cached[0] < PIXABAY_CACHE_TTL:
            _pixabay_cache.move_to_end(cache_key)
            return deepcopy(cached[1])
        _pixabay_cache.pop(cache_key, None)
        result = _fetch_pixabay(http, key, query, kind, page, per_page)
        _pixabay_cache[cache_key] = (time.monotonic(), deepcopy(result))
        while len(_pixabay_cache) > PIXABAY_CACHE_SIZE:
            _pixabay_cache.popitem(last=False)
        return result


_SEARCH_FNS = {"pexels": _search_pexels, "pixabay": _search_pixabay}


def search(query: str, *, kind: SearchKind = "all", page: int = 1, per_page: int | None = None) -> dict:
    """Merged, deduplicated results from every provider with a configured key.

    A provider error is caught per (provider, kind) call and surfaced in `errors` rather than
    failing the whole request, so one bad key or a rate limit on one provider never hides the
    other provider's results.
    """
    query = (query or "").strip()
    if not query:
        raise StockError("empty_query", 400)
    if kind not in ("all", "video", "photo"):
        raise StockError("invalid_kind", 400)
    keys = _resolve_keys()
    if not keys:
        raise StockError("not_configured", 400)
    per_page = per_page or RESULTS_PER_PAGE
    kinds: tuple[StockKind, ...] = ("video", "photo") if kind == "all" else (kind,)
    results: list[dict] = []
    errors: dict[str, str] = {}
    has_more = False
    http = _new_client()
    try:
        for provider, fn in _SEARCH_FNS.items():
            key = keys.get(provider)
            if not key:
                continue
            for k in kinds:
                try:
                    items, more = fn(http, key, query, k, page, per_page)
                except StockError as e:
                    errors[provider] = e.message
                    continue
                except (ValueError, TypeError, KeyError):
                    errors[provider] = _ERROR_MESSAGES["provider_error"]
                    continue
                results.extend(items)
                has_more = has_more or more
    finally:
        http.close()
    seen: set[str] = set()
    deduped = []
    for item in results:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        deduped.append(item)
    return {"results": deduped, "page": page, "hasMore": has_more, "errors": errors}


# ── import: download the item's chosen rendition into a workspace library ───────

def _validate_download_url(url: object) -> str:
    if not isinstance(url, str) or not url:
        raise StockError("invalid_item", 400)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    host_ok = any(host == h or host.endswith(f".{h}") for h in _DEFAULT_DOWNLOAD_HOSTS)
    if parsed.scheme != "https" or not host_ok:
        raise StockError("invalid_source", 400)
    return url


_REQUIRED_ITEM_FIELDS = ("provider", "kind", "downloadUrl", "author", "sourceUrl", "license")


def _validate_item(item: object) -> dict:
    if not isinstance(item, dict):
        raise StockError("invalid_item", 400)
    for field in _REQUIRED_ITEM_FIELDS:
        if not item.get(field):
            raise StockError("invalid_item", 400)
    if item["provider"] not in _PROVIDERS or item["kind"] not in ("video", "photo"):
        raise StockError("invalid_item", 400)
    _validate_download_url(item["downloadUrl"])
    return item


def _guess_ext(item: dict, content_type: str | None) -> str:
    if content_type:
        ext = _EXT_BY_CONTENT_TYPE.get(content_type.split(";")[0].strip().lower())
        if ext:
            return ext
    suffix = Path(urlparse(item["downloadUrl"]).path).suffix.lower()
    if suffix in _ALLOWED_EXTS:
        return suffix
    return ".mp4" if item["kind"] == "video" else ".jpg"


def _download(url: str, dest: Path) -> str | None:
    http = _new_client()
    try:
        try:
            with http.stream("GET", url, timeout=_DOWNLOAD_TIMEOUT) as resp:
                if resp.status_code in (401, 403):
                    raise StockError("unauthorized", 401)
                if resp.status_code == 429:
                    raise StockError("rate_limited", 429)
                if not 200 <= resp.status_code < 300:
                    raise StockError("download_failed", 502)
                content_type = resp.headers.get("content-type")
                total = 0
                with dest.open("wb") as fh:
                    for chunk in resp.iter_bytes():
                        total += len(chunk)
                        if total > MAX_DOWNLOAD_BYTES:
                            raise StockError("download_too_large", 413)
                        fh.write(chunk)
        except httpx.TimeoutException:
            raise StockError("timeout", 504) from None
        except httpx.HTTPError:
            raise StockError("network_error", 502) from None
    finally:
        http.close()
    return content_type


def import_stock_asset(workspace_id: str, item: object) -> tuple[dict, bool]:
    """Download `item`'s chosen rendition and content-address it into the workspace library.

    Mirrors `scripts.studio_app._import_library_uploads`: a temp file that is always cleaned up
    (library.import_asset consumes it on success, moving or discarding it either way).
    """
    validated = _validate_item(item)
    if library.get_workspace(workspace_id) is None:
        raise FileNotFoundError(f"unknown workspace: {workspace_id}")
    tmp_dir = config.RUNTIME_DIR / "stock-downloads"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f".stock-{uuid.uuid4().hex}"
    try:
        content_type = _download(validated["downloadUrl"], tmp)
        ext = _guess_ext(validated, content_type)
        provider_label = validated["provider"].title()
        author = validated["author"]
        display_name = f"{provider_label} - {author}{ext}"
        attribution = {
            "provider": validated["provider"],
            "author": author,
            "authorUrl": validated.get("authorUrl"),
            "sourceUrl": validated["sourceUrl"],
            "license": validated["license"],
        }
        return library.import_asset(workspace_id, tmp, display_name=display_name, attribution=attribution)
    finally:
        tmp.unlink(missing_ok=True)
