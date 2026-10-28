"""Public error bodies and private diagnostic logging."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from starlette.responses import JSONResponse as BaseJSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pipeline.errors import ValidationError
from pipeline.encoder_ladder import PROBE_FAILURE_MESSAGE, PROBE_TIMEOUT_MESSAGE, UNAVAILABLE_MESSAGE
from pipeline.names import NameConflict

logger = logging.getLogger(__name__)
_UNSAFE = re.compile(
    r"[A-Za-z]:[\\/]|\\|(?<![A-Za-z0-9_.-])/(?=[^\s/])[^\s]*|Traceback"
    r"|\b(?:\w+(?:Error|Exception|Warning|Conflict)|NameNotFound|SystemExit|KeyboardInterrupt"
    r"|GeneratorExit|StopIteration|StopAsyncIteration|DeleteRefused|ScriptDraftUnavailable|SelectionRejected)\b"
)
_PUBLIC_FIELDS = frozenset({
    "revision", "currentRevision", "baseRevision", "ops", "limit", "maxBytes",
    "retryable", "reason", "currentStatus", "status",
})
_AUTHORED_HEADER = b"x-studio-authored-error"
_PERMISSION_MESSAGES = frozenset({
    (403, "loopback clients only"), (421, "untrusted host"),
    (403, "ambiguous browser context"), (403, "cross-site request"),
    (403, "origin not allowed"), (403, "browser context or internal token required"),
})

_ERRORS = {
    400: ("invalid_request", "The request is invalid."),
    401: ("unauthorized", "Authorization is required."),
    403: ("forbidden", "The request is not allowed."),
    404: ("not_found", "The requested item was not found."),
    405: ("method_not_allowed", "This request method is not allowed."),
    409: ("conflict", "The request conflicts with the current state."),
    413: ("payload_too_large", "The request exceeds the size limit."),
    415: ("unsupported_media_type", "The request content type is not supported."),
    421: ("misdirected_request", "The request host is not allowed."),
    422: ("validation_failed", "The request could not be validated."),
    429: ("rate_limited", "Too many requests. Please try again later."),
    502: ("upstream_error", "The provider could not complete the request."),
    503: ("unavailable", "The service is currently unavailable."),
    504: ("timeout", "The request timed out."),
}


def public_error(error: Exception, status: int) -> str:
    from pipeline.cast import CastError
    from pipeline.jobs import JobError, JobStateError
    from pipeline.library import DeleteRefused, LibraryError
    from pipeline.script_video import NarrationError
    from pipeline.stock import StockError
    from scripts.agent_host import ScriptDraftError

    logger.warning("Request failed", exc_info=(type(error), error, error.__traceback__))
    trusted = isinstance(error, (
        ValidationError, NameConflict, StockError, ScriptDraftError, CastError,
        JobError, JobStateError, LibraryError, DeleteRefused, NarrationError,
    ))
    message = str(error)
    if status < 500 and trusted and message and not _UNSAFE.search(message):
        return message
    return _ERRORS.get(status, ("internal_error", "Studio could not complete the request."))[1]


def _encoding_message(detail: object) -> str | None:
    for message in (UNAVAILABLE_MESSAGE, PROBE_TIMEOUT_MESSAGE, PROBE_FAILURE_MESSAGE):
        if detail in (message, f"scene render failed: {message}"):
            return message
    return None


def failure_message(detail: object, message: str = "Studio could not complete the request.") -> str:
    logger.warning("Private failure detail: %s", detail)
    return _encoding_message(detail) or message


def agent_error_data(data: dict) -> dict:
    from pipeline import addons, agent_credentials
    from studio import state

    messages = {
        "auth": agent_credentials.connection_message(state._claude_connection.snapshot()),
        "assistant_not_installed": addons.ASSISTANT_INCOMPLETE,
        "billing": "The co-pilot provider could not authorize this usage.",
        "rate_limit": "The co-pilot provider is busy. Try again later.",
        "runtime": "The co-pilot could not complete the request.",
    }
    raw_kind = data.get("kind")
    kind = raw_kind if isinstance(raw_kind, str) and raw_kind in messages else "runtime"
    detail = data.get("message")
    message = detail if isinstance(detail, str) and detail in {"turn_cancelled", "agent_host_shutdown", "agent_session_unavailable", "provider_unavailable"} else messages[kind]
    return {"kind": kind, "message": failure_message(detail, message)}


def error_body(content: Any, status: int, *, authored: bool = False) -> dict[str, Any]:
    code, fallback = _ERRORS.get(status, ("internal_error" if status >= 500 else "request_failed",
                                         "Studio could not complete the request."))
    original = content if isinstance(content, dict) else {}
    message = original.get("error")
    authored = authored or isinstance(message, str) and (status, message) in _PERMISSION_MESSAGES
    encoding_message = _encoding_message(message) if authored else None
    message = encoding_message or message
    if (status >= 500 and not encoding_message) or not authored or not isinstance(message, str) or not message or _UNSAFE.search(message):
        if message and message != fallback:
            logger.warning("Private error detail: %s", message)
        message = fallback
    body = {"error": message, "code": code}
    existing_code = original.get("code")
    if authored and isinstance(existing_code, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", existing_code) and not _UNSAFE.search(existing_code):
        body["code"] = existing_code
    for key, value in original.items():
        if key in {"error", "code"}:
            continue
        if key in _PUBLIC_FIELDS and not _UNSAFE.search(json.dumps(value, ensure_ascii=False)):
            body[key] = value
        else:
            logger.warning("Private error field %s: %s", key, value)
    return body


class JSONResponse(BaseJSONResponse):
    """Explicit response boundary for Studio-authored messages and domain errors."""

    def __init__(self, content: Any, status_code: int = 200, **kwargs: Any) -> None:
        if status_code >= 400:
            content = error_body(content, status_code, authored=True)
            headers = dict(kwargs.pop("headers", None) or {})
            headers[_AUTHORED_HEADER.decode()] = "1"
            kwargs["headers"] = headers
        super().__init__(content, status_code=status_code, **kwargs)


class ErrorResponses:
    """Normalize JSON errors, including errors emitted by permission middleware."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start: Message | None = None
        chunks: list[bytes] = []

        async def public_send(message: Message) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                if message["status"] >= 400 and b"application/json" in headers.get(b"content-type", b""):
                    start = message
                    return
            elif message["type"] == "http.response.body" and start is not None:
                chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                try:
                    content = json.loads(b"".join(chunks))
                except (ValueError, UnicodeDecodeError):
                    logger.warning("Error response contained malformed JSON")
                    content = {}
                authored = dict(start.get("headers", [])).get(_AUTHORED_HEADER) == b"1"
                response = BaseJSONResponse(error_body(content, start["status"], authored=authored), status_code=start["status"])
                headers = [(key, value) for key, value in start.get("headers", [])
                           if key not in {b"content-length", _AUTHORED_HEADER}]
                headers.append((b"content-length", str(len(response.body)).encode()))
                await send({**start, "headers": headers})
                await send({"type": "http.response.body", "body": response.body})
                return
            await send(message)

        await self.app(scope, receive, public_send)
