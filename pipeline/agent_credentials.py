"""Protected API credentials and connection readiness for the co-pilot."""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import re
import sys
import threading
import traceback
import urllib.request
from types import TracebackType
from typing import Any
from urllib.parse import quote, quote_plus

import httpx

import config
from pipeline import addons, secret_store
from pipeline.errors import ValidationError

CONNECT_MESSAGE = "Connect your key in Settings to ask for edits."
UNVERIFIED_MESSAGE = "Key not verified. Open Settings to verify your Anthropic API key."
VERIFYING_MESSAGE = "Verifying your key…"
ENVIRONMENT_MESSAGE = "ANTHROPIC_API_KEY is set in this environment and takes precedence"
_API_HOST = "api.anthropic.com"
_REDACTED = "[redacted]"
_known_secrets: set[str] = set()
_secret_pattern: re.Pattern[str] | None = None
# Other services' keys (stock providers) are redacted from log and stderr output only: they may be short enough
# that substituting them in agent data would corrupt ordinary text.
_log_secrets: set[str] = set()
_log_pattern: re.Pattern[str] | None = None
_lock = threading.RLock()


def connection_message(status: dict) -> str:
    if not status["connected"]:
        return CONNECT_MESSAGE
    return VERIFYING_MESSAGE if status["state"] == "checking" else UNVERIFIED_MESSAGE


def _pattern(secrets: set[str]) -> re.Pattern[str]:
    representations = {representation for secret in secrets
                       for representation in (secret, repr(secret)[1:-1], json.dumps(secret)[1:-1],
                                              quote(secret, safe=""), quote_plus(secret))}
    # The marker matches first and replaces itself, so text redacted at several layers stays intact
    # even when a short stock key occurs inside the marker.
    return re.compile("|".join(re.escape(item) for item in
                               [_REDACTED, *sorted(representations, key=len, reverse=True)]))


def _remember_key(value: str) -> None:
    global _secret_pattern
    with _lock:
        if value in _known_secrets:
            return
        _known_secrets.add(value)
        _secret_pattern = _pattern(_known_secrets)


def remember_log_secret(value: str) -> None:
    """Redact `value`, in every encoding a log line might carry, from all log and stderr output."""
    global _log_pattern
    with _lock:
        if value in _log_secrets:
            return
        _log_secrets.add(value)
        _log_pattern = _pattern(_log_secrets)


def redact(text: str) -> str:
    with _lock:
        pattern = _secret_pattern
    return pattern.sub(_REDACTED, text) if pattern else text


def redact_data(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {redact_data(key): redact_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_data(item) for item in value]
    return value


def _redact_output(text: str) -> str:
    """Redact the assistant key and every other service key from text bound for logs or stderr."""
    text = redact(text)
    with _lock:
        pattern = _log_pattern
    return pattern.sub(_REDACTED, text) if pattern else text


# Log records keep their raw values; every text rendering of one is redacted instead: the base
# Formatter, a handler's own formatter (which may override format entirely), the message a handler
# without a formatter reads, and the report Handler.handleError prints for a failed record.
_format = logging.Formatter.format
_handler_format = logging.Handler.format
_get_message = logging.LogRecord.getMessage
_LOGGING_DIRECTORY = os.path.dirname(logging.__file__)


def _secret_format(self: logging.Formatter, record: logging.LogRecord) -> str:
    return _redact_output(_format(self, record))


def _secret_handler_format(self: logging.Handler, record: logging.LogRecord) -> str:
    text = _handler_format(self, record)
    return _redact_output(text) if isinstance(text, str) else text


def _secret_message(self: logging.LogRecord) -> str:
    return _redact_output(_get_message(self))


def _write_stderr(text: str) -> None:
    stream = sys.stderr
    if stream is None:
        return
    try:
        stream.write(_redact_output(text))
        stream.flush()
    except OSError:
        pass


def _handle_error(self: logging.Handler, record: logging.LogRecord) -> None:
    """The standard library's report, redacted: it names the failed record's raw message and arguments."""
    if not (logging.raiseExceptions and sys.stderr):
        return
    error = sys.exception()
    report = ["--- Logging error ---\n", *traceback.format_exception(error), "Call stack:\n"]
    frame = error.__traceback__.tb_frame if error and error.__traceback__ else None
    while frame and os.path.dirname(frame.f_code.co_filename) == _LOGGING_DIRECTORY:
        frame = frame.f_back
    report.extend(traceback.format_stack(frame) if frame else
                  [f"Logged from file {record.filename}, line {record.lineno}\n"])
    try:
        report.append("Message: %r\nArguments: %s\n" % (record.msg, record.args))
    except RecursionError:
        raise
    except Exception:
        report.append("Unable to print the message and arguments - possible type mismatch? "
                      "Use %s for numeric values.\n")
    _write_stderr("".join(report))


def _excepthook(kind: type[BaseException], error: BaseException, trace: TracebackType | None) -> None:
    _write_stderr("".join(traceback.format_exception(kind, error, trace)))


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    if args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread is not None else threading.get_ident()
    _write_stderr(f"Exception in thread {name}:\n"
                  + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))


def _unraisablehook(unraisable: sys.UnraisableHookArgs) -> None:
    report = []
    if unraisable.object is not None:
        try:
            described = repr(unraisable.object)
        except Exception:
            described = "<object repr() failed>"
        report.append(f"{unraisable.err_msg or 'Exception ignored in'}: {described}\n")
    elif unraisable.err_msg:
        report.append(f"{unraisable.err_msg}:\n")
    report.extend(traceback.format_exception(unraisable.exc_type, unraisable.exc_value, unraisable.exc_traceback))
    _write_stderr("".join(report))


def _install_exception_hooks() -> None:
    # A hook something else installed keeps its own output; the interpreter's defaults print raw text.
    if sys.excepthook is sys.__excepthook__:
        sys.excepthook = _excepthook
    if threading.excepthook is threading.__excepthook__:
        threading.excepthook = _thread_excepthook
    if sys.unraisablehook is sys.__unraisablehook__:
        sys.unraisablehook = _unraisablehook


logging.Formatter.format = _secret_format
logging.Handler.format = _secret_handler_format
logging.Handler.handleError = _handle_error
logging.LogRecord.getMessage = _secret_message
_install_exception_hooks()


def _valid_key(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[\x21-\x7e]{20,512}", value) is not None


def resolve_key() -> str | None:
    value = os.environ.get("ANTHROPIC_API_KEY") or secret_store.get("anthropic-api-key")
    if not isinstance(value, str) or not _valid_key(value):
        return None
    _remember_key(value)
    return value


def require_key() -> str:
    key = resolve_key()
    if not key:
        raise ValidationError(CONNECT_MESSAGE)
    return key


def set_key(value: object) -> None:
    if not _valid_key(value):
        raise ValidationError("API keys must be printable ASCII without whitespace (20-512 characters).")
    assert isinstance(value, str)
    _remember_key(value)
    secret_store.set("anthropic-api-key", value)


def key_fingerprint(key: str | None) -> str | None:
    return hashlib.sha256(key.encode()).hexdigest() if key else None


def sdk_environment(key: str | None = None) -> dict[str, str]:
    key = key if key is not None else require_key()
    directory = config.RUNTIME_DIR / "claude-agent"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        _protect_directory(str(directory))
    else:
        directory.chmod(0o700)
    return {
        **{
            name: "" for name in os.environ
            if name.upper().startswith(("ANTHROPIC_", "CLAUDE_"))
            or name.upper().endswith(("_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_PASS", "_PAT", "_CREDENTIALS",
                                      "_USER", "_USERNAME"))
            or any(part in name.upper() for part in ("SECRET", "PASSWORD", "TOKEN"))
        },
        "ANTHROPIC_API_KEY": key,
        "CLAUDE_CONFIG_DIR": str(directory),
        "CLAUDE_SECURESTORAGE_CONFIG_DIR": str(directory),
        "ANTHROPIC_CONFIG_DIR": str(directory),
        "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
        "CLAUDE_CODE_OAUTH_TOKEN": "",
        "ANTHROPIC_AUTH_TOKEN": "",
        "ANTHROPIC_BASE_URL": "",
    }


def _protect_directory(path: str, dacl: str = "D:P(A;OICI;FA;;;OW)") -> None:
    loader = getattr(ctypes, "WinDLL")
    security = loader("advapi32", use_last_error=True)
    kernel = loader("kernel32", use_last_error=True)
    convert = security.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    convert.restype = ctypes.c_int
    protect = security.SetFileSecurityW
    protect.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p]
    protect.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    # A protected inheritable DACL grants access only to the directory's owner.
    if not convert(dacl, 1, ctypes.byref(descriptor), None):
        raise secret_store.SecretStoreError("could not protect agent configuration")
    try:
        if not protect(path, 0x80000004, descriptor):
            raise secret_store.SecretStoreError("could not protect agent configuration")
    finally:
        kernel.LocalFree(descriptor)


def _api_proxy() -> str | None:
    """The environment proxy for the API host, from the standard variables the bundled CLI also honours,
    so Verify takes the route a turn takes. Read explicitly because trust_env stays off: ambient netrc
    credentials and other environment settings never join a request that carries the key."""
    proxies = urllib.request.getproxies_environment()
    proxy = proxies.get("https") or proxies.get("http")
    # With environment proxies present, proxy_bypass consults NO_PROXY rather than OS settings.
    return None if not proxy or urllib.request.proxy_bypass(_API_HOST) else proxy


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=5.0, trust_env=False, follow_redirects=False, proxy=_api_proxy())


class ClaudeConnection:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._fingerprint: str | None = None
        self._state = "not_connected"
        self._message: str | None = None
        self._generation = 0
        self._source: str | None = None
        self._rejected = False
        self._verification_started = False
        self._identity: tuple | None = None

    def _refresh_metadata(self) -> None:
        environment = os.environ.get("ANTHROPIC_API_KEY")
        metadata = secret_store.metadata("anthropic-api-key") if not environment else None
        identity = ("environment", key_fingerprint(environment)) if environment else (
            ("settings", *metadata) if metadata else None)
        if identity != self._identity:
            self._identity = identity
            self._fingerprint = None
            self._state = "unknown" if identity else "not_connected"
            self._message = None
            self._rejected = self._verification_started = False
            self._generation += 1
        self._source = identity[0] if identity else None
        if environment and not _valid_key(environment):
            self._state = "not_connected"

    def _refresh(self) -> str | None:
        try:
            self._refresh_metadata()
            key = resolve_key()
        except secret_store.SecretStoreError:
            self._generation += 1
            self._fingerprint = None
            self._state, self._message = "error", "Could not read Claude credentials."
            return None
        if key and self._source is None:
            self._source = "settings"
        fingerprint = key_fingerprint(key)
        if fingerprint != self._fingerprint:
            self._fingerprint = fingerprint
            self._state = "unknown" if key else "not_connected"
            self._message = None
            self._rejected = False
            self._verification_started = False
            self._generation += 1
        elif not key:
            self._state, self._message = "not_connected", None
        return key

    def _result(self) -> dict:
        connected = self._source is not None and self._state != "not_connected"
        result = {"connected": connected, "state": self._state, "source": self._source}
        if self._message:
            result["message"] = self._message
        return result

    def status(self, *, verify: bool = True) -> dict:
        if not verify or not addons.available("assistant"):
            return self.snapshot()
        with self._lock:
            key = self._refresh()
            if key and not self._verification_started:
                self._verification_started = True
                self._state = "checking"
                threading.Thread(target=self._verify_snapshot, args=(key, self._generation),
                                 name="agent-key-verification", daemon=True).start()
            return self._result()

    def snapshot(self) -> dict:
        """Report presence and known verification state without starting a network request."""
        with self._lock:
            self._refresh_metadata()
            return self._result()

    def verify(self) -> dict:
        if not addons.available("assistant"):
            return self.snapshot()
        with self._lock:
            key = self._refresh()
            if not key or self._rejected:
                return self._result()
            self._generation += 1
            generation = self._generation
            self._verification_started = True
            self._state, self._message = "checking", None
        return self._verify_snapshot(key, generation)

    def reject(self, fingerprint: str | None) -> None:
        """Invalidate cached readiness after an authenticated turn is refused."""
        with self._lock:
            key = self._refresh()
            if not key or fingerprint != self._fingerprint:
                return
            self._generation += 1
            self._state, self._message = "error", "That API key was rejected."
            self._verification_started = True

    def _verify_snapshot(self, key: str, generation: int) -> dict:
        rejected = False
        try:
            with _new_client() as client:
                response = client.get(f"https://{_API_HOST}/v1/models", headers={
                    "x-api-key": key, "anthropic-version": "2023-06-01",
                })
            if response.status_code == 200:
                state, message = "ready", None
            elif response.status_code in (401, 403):
                state, message = "error", "That API key was rejected."
                rejected = True
            else:
                state, message = "error", "Anthropic could not verify the API key."
        # ValueError/ImportError: an unusable proxy setting (unknown scheme, SOCKS without its extra).
        except (httpx.HTTPError, ValueError, ImportError):
            state, message = "error", "Couldn't reach Anthropic."
        with self._lock:
            self._refresh_metadata()
            if generation == self._generation:
                self._state, self._message = state, message
                self._rejected = rejected
            return self._result()

    def connect(self, value: object) -> dict:
        with self._lock:
            if os.environ.get("ANTHROPIC_API_KEY"):
                raise ValidationError(ENVIRONMENT_MESSAGE)
            set_key(value)
        return self.verify()

    def disconnect(self) -> dict:
        with self._lock:
            if os.environ.get("ANTHROPIC_API_KEY"):
                raise ValidationError(ENVIRONMENT_MESSAGE)
            secret_store.delete("anthropic-api-key")
            self._generation += 1
            self._fingerprint = None
            self._state, self._message = "not_connected", None
            return self.status()
