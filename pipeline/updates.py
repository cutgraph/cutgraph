"""Stable release checks and verified downloads; only the native launcher runs Setup."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import json
from pathlib import Path
import re
import subprocess
import threading
import time
from urllib.parse import urljoin, urlsplit
import uuid

import httpx

import config
from pipeline import version
from pipeline.runtime_bundle import BUNDLE_MANIFEST

DAY = 24 * 60 * 60
BUSY = {"checking", "downloading", "verifying", "installing"}
exit_requested = threading.Event()


def bundle_launcher() -> Path | None:
    root = config.CODE_ROOT.parent
    launcher = root / "Cutgraph Studio.exe"
    return launcher if (root / BUNDLE_MANIFEST).is_file() and launcher.is_file() else None


def _atomic(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


class Updates:
    def __init__(self, home: Path, repository: str, public_key: str, current: str,
                 *, launcher: Path | None = None, clock=time.time, transport=None):
        self.root = home / "updates"
        self.repository, self.public_key, self.current = repository, public_key, current
        self.launcher, self.clock, self.transport = launcher, clock, transport
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.worker: threading.Thread | None = None
        self.scheduler: threading.Thread | None = None
        self.document: dict | None = None
        self.data = b""
        self.signature = b""
        self.state = "not_configured"
        self.reason = "Updates not configured."
        self.saved: dict = {"automatic": True}
        try:
            if self.root.is_symlink() or self.root.is_junction():
                raise ValueError("The updates folder must not be a link.")
            value = json.loads((self.root / "state.json").read_text(encoding="utf-8"))
            if isinstance(value, dict):
                self.saved = value
            else:
                raise ValueError("Invalid update settings.")
            if (type(self.saved.get("automatic", True)) is not bool
                    or type(self.saved.get("lastCheck", 0)) not in {float, int}):
                raise ValueError("Invalid update settings.")
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            # A damaged preference must not silently opt a person back into networking.
            self.saved = {"automatic": False}
            self.state, self.reason = "failed", "Update settings could not be read."
        if self.configured() and self.saved.get("manifest"):
            try:
                self.data = base64.b64decode(self.saved["manifest"], validate=True)
                self.signature = base64.b64decode(self.saved["signature"], validate=True)
                self._verify()
                self._available()
            except (ValueError, TypeError, KeyError):
                self.state, self.reason = "failed", "Cached release verification failed."

    def configured(self) -> bool:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository):
            return False
        try:
            from installer.release_manifest import decode_key
            return bool(any(decode_key(self.public_key)))
        except ValueError:
            return False

    def _directory(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or self.root.is_junction():
            raise ValueError("The updates folder must not be a link.")

    def _save(self) -> None:
        self._directory()
        _atomic(self.root / "state.json", self.saved)

    def snapshot(self) -> dict:
        with self.lock:
            return {"state": self.state, "reason": self.reason, "automatic": self.saved.get("automatic") is not False,
                    "currentVersion": self.current, "version": self.document["version"] if self.document else None,
                    "releaseNotesUrl": self.document["releaseNotesUrl"] if self.document else None,
                    "canInstall": bool(self.saved.get("automatic") is not False and self.launcher and self.document and self.state == "available" and self._supported()),
                    "lastCheck": self.saved.get("lastCheck")}

    def set_automatic(self, enabled: bool) -> dict:
        with self.lock:
            self.saved["automatic"] = enabled
            self._save()
        self.check()
        return self.snapshot()

    def _url(self, value: str) -> None:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname not in {
                "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
                or parsed.username or parsed.password or parsed.port not in {None, 443} or parsed.fragment):
            raise ValueError("Update downloads must use GitHub HTTPS release assets.")

    @contextmanager
    def _response(self, url: str):
        with httpx.Client(transport=self.transport, trust_env=False, timeout=20,
                          headers={"User-Agent": f"Cutgraph-Studio/{self.current}"}) as client:
            for _ in range(6):
                self._url(url)
                if self.stop.is_set() or self.saved.get("automatic") is False:
                    raise ValueError("Update checks are disabled.")
                client.cookies.clear()
                with client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers["location"])
                        continue
                    response.raise_for_status()
                    yield response
                    return
            raise ValueError("Too many release redirects.")

    def _fetch(self, url: str, limit: int) -> bytes:
        data = bytearray()
        with self._response(url) as response:
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("Release metadata exceeds its size limit.")
        return bytes(data)

    def _verify(self, installer: Path | None = None) -> dict:
        from installer.release_manifest import stable_version, verify
        document = verify(self.data, self.signature, self.public_key, installer)
        current = stable_version(re.sub(r"\.dev[0-9]+$", "", self.current))
        target = stable_version(document["version"])
        if target < current or target < stable_version(self.saved.get("highestVersion", "0.0.0")):
            raise ValueError("Refusing a release downgrade.")
        for url in (document["installer"]["url"], document["releaseNotesUrl"]):
            self._url(url)
            if not url.startswith(f"https://github.com/{self.repository}/releases/"):
                raise ValueError("Release links do not belong to the configured repository.")
        if not urlsplit(document["installer"]["url"]).path.endswith(".exe"):
            raise ValueError("The release asset must be a Windows installer.")
        self.document = document
        return document

    def _supported(self) -> bool:
        from installer.release_manifest import stable_version
        assert self.document is not None
        return stable_version(re.sub(r"\.dev[0-9]+$", "", self.current)) >= stable_version(self.document["minimumVersion"])

    def _available(self) -> None:
        assert self.document is not None
        self.state = "up_to_date" if self.document["version"] == self.current else "available"
        self.reason = ("Up to date." if self.state == "up_to_date" else
                       "This version requires a manual update from the release notes." if not self._supported() else
                       "Update available." if self.launcher else "Update available. Update this source checkout manually.")

    def _begin(self, action, state: str) -> bool:
        if self.worker and self.worker.is_alive() or self.stop.is_set():
            return False
        self.state, self.reason = state, ""
        self.worker = threading.Thread(target=self._run, args=(action,), name="studio-updates", daemon=True)
        self.worker.start()
        return True

    def _run(self, action) -> None:
        try:
            action()
        except (OSError, ValueError, TypeError, KeyError, httpx.HTTPError, subprocess.SubprocessError) as error:
            with self.lock:
                self.state = "failed"
                self.reason = str(error) if isinstance(error, ValueError) else "Update transfer failed. Check your connection and try again after the daily limit."

    def check(self) -> dict:
        with self.lock:
            if self.saved.get("automatic") is False:
                self.state, self.reason = "disabled", "Update checks are disabled."
            elif not self.configured():
                self.state, self.reason = "not_configured", "Updates not configured."
            elif "lastCheck" not in self.saved or self.clock() - self.saved["lastCheck"] >= DAY:
                if not self.worker or not self.worker.is_alive():
                    # Persist attempts before the request, including failures and restarts.
                    self.saved["lastCheck"] = self.clock()
                    self._save()
                    self._begin(self._check, "checking")
            elif self.state == "disabled" and self.document:
                self._available()
            return self.snapshot()

    def _check(self) -> None:
        base = f"https://github.com/{self.repository}/releases/latest/download/"
        self.data = self._fetch(base + "release.json", 64 * 1024)
        self.signature = self._fetch(base + "release.json.sig", 64)
        with self.lock:
            document = self._verify()
            self.saved.update(manifest=base64.b64encode(self.data).decode(),
                              signature=base64.b64encode(self.signature).decode(), highestVersion=document["version"])
            self._save()
            self._available()

    def install(self) -> dict:
        with self.lock:
            if self.saved.get("automatic") is False or not self.configured():
                raise ValueError("Enable configured update checks before downloading.")
            if not self.launcher or self.state != "available" or not self._supported():
                raise ValueError("Use the release notes to update this installation manually.")
            self._begin(self._download, "downloading")
            return self.snapshot()

    def _download(self) -> None:
        document = self._verify()
        self._directory()
        partial = self.root / (uuid.uuid4().hex + ".part")
        asset = document["installer"]
        destination = self.root / ("installer-" + asset["sha256"] + ".exe")
        try:
            size = 0
            with partial.open("xb") as output, self._response(asset["url"]) as response:
                for chunk in response.iter_bytes(1024 * 1024):
                    if self.stop.is_set() or self.saved.get("automatic") is False:
                        raise ValueError("Update download stopped.")
                    size += len(chunk)
                    if size > asset["size"]:
                        raise ValueError("Installer exceeds its signed size.")
                    output.write(chunk)
            with self.lock:
                self.state = "verifying"
            self._verify(partial)
            # Authenticode publisher verification remains disabled until releases are signed.
            partial.replace(destination)
            request_id = uuid.uuid4().hex
            _atomic(self.root / "pending.json", {"id": request_id, "path": str(destination.resolve()),
                    "sha256": asset["sha256"], "size": asset["size"], "version": document["version"]})
            assert self.launcher is not None
            result = subprocess.run([str(self.launcher), "--request-update"], capture_output=True, timeout=30,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise ValueError("The launcher refused the update. Close Studio and update manually.")
            with self.lock:
                self.state, self.reason = "installing", "Studio is closing to install the verified update."
            exit_requested.set()
        finally:
            partial.unlink(missing_ok=True)

    def start(self) -> None:
        self.check()
        def schedule():
            while not self.stop.wait(60):
                self.check()
        self.scheduler = threading.Thread(target=schedule, name="studio-update-clock", daemon=True)
        self.scheduler.start()

    def close(self) -> None:
        self.stop.set()
        for thread in (self.scheduler, self.worker):
            if thread:
                thread.join(timeout=25)


_service: Updates | None = None


def service() -> Updates:
    global _service
    if _service is None:
        _service = Updates(config.STATE_HOME, config.UPDATE_REPOSITORY, config.UPDATE_PUBLIC_KEY,
                           version.__version__, launcher=bundle_launcher())
    return _service


def shutdown() -> None:
    global _service
    if _service:
        _service.close()
        _service = None
