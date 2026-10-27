"""Browser request isolation for the local Studio, before any route or body parsing."""
from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

INTERNAL_TOKEN = secrets.token_urlsafe(32)
BOUND_PORT = 8765
EXTRA_HOSTS: frozenset[str] = frozenset()
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' blob: data: https://images.pexels.com https://cdn.pixabay.com https://pixabay.com https://i.vimeocdn.com; "
        "media-src 'self' blob: data: https://videos.pexels.com https://cdn.pixabay.com; "
        "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def configure(port: int) -> None:
    global BOUND_PORT
    BOUND_PORT = port
    _build_allowlists()


def set_extra_hosts(hosts: frozenset[str]) -> None:
    global EXTRA_HOSTS
    EXTRA_HOSTS = frozenset(host.lower() for host in hosts)
    _build_allowlists()


def _build_allowlists() -> None:
    global _ALLOWED_HOSTS, _ALLOWED_ORIGINS
    names = {"127.0.0.1", "localhost", "[::1]"}
    hosts = {f"{name}:{BOUND_PORT}" for name in names}
    if BOUND_PORT == 80:
        hosts.update(names)
    hosts.update(EXTRA_HOSTS)
    origins = {f"http://{authority}" for authority in hosts}
    dev_origin = os.environ.get("CUTGRAPH_DEV_ORIGIN", "").lower().rstrip("/")
    try:
        dev = urlsplit(dev_origin)
        if (dev.scheme in {"http", "https"} and dev.hostname in {"127.0.0.1", "localhost", "::1"} and dev.port
                and not dev.username and not dev.password and not dev.path
                and not dev.query and not dev.fragment):
            hosts.add(dev.netloc.lower())
            origins.add(dev_origin)
    except ValueError:
        pass
    _ALLOWED_HOSTS, _ALLOWED_ORIGINS = frozenset(hosts), frozenset(origins)


configure(BOUND_PORT)


class WebSecurityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        downstream_send = send

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).update(SECURITY_HEADERS)
            await downstream_send(message)

        send = secure_send

        client = scope.get("client")
        try:
            peer = ipaddress.ip_address(client[0]) if client else None
        except ValueError:
            peer = None
        if isinstance(peer, ipaddress.IPv6Address) and peer.ipv4_mapped is not None:
            peer = peer.ipv4_mapped
        if peer is None or not peer.is_loopback:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse({"error": "loopback clients only"}, status_code=403)(scope, receive, send)
            return

        headers: dict[bytes, list[bytes]] = {}
        for key, value in scope["headers"]:
            headers.setdefault(key.lower(), []).append(value)

        def header(name: bytes) -> str | None:
            values = headers.get(name)
            return values[0].decode("latin-1") if values else None

        host = header(b"host")
        status, error = 403, ""
        if len(headers.get(b"host", [])) != 1 or (host or "").lower() not in _ALLOWED_HOSTS:
            status, error = 421, "untrusted host"
        elif any(len(headers.get(name, [])) > 1 for name in (
            b"origin", b"sec-fetch-site", b"sec-fetch-mode", b"sec-fetch-dest", b"x-cutgraph-internal",
        )):
            error = "ambiguous browser context"
        else:
            site = header(b"sec-fetch-site")
            navigation = (scope["type"] == "http" and scope["method"] == "GET"
                          and (scope["path"] in {"/", "/editor"} or scope["path"].startswith("/editor/"))
                          and header(b"sec-fetch-mode") == "navigate"
                          and header(b"sec-fetch-dest") == "document")
            exempt = navigation and site in {"cross-site", "same-site"}
            if site is not None and site not in {"same-origin", "none"} and not exempt:
                error = "cross-site request"
            elif scope["type"] == "websocket" or scope["method"] in UNSAFE_METHODS:
                origin = header(b"origin")
                if origin is not None and origin not in _ALLOWED_ORIGINS:
                    error = "origin not allowed"
                elif origin is None and (scope["type"] == "websocket" or site is None):
                    # Isolate browser contexts; this does not authenticate local processes.
                    token = headers.get(b"x-cutgraph-internal", [b""])[0]
                    if not hmac.compare_digest(token, INTERNAL_TOKEN.encode("ascii")):
                        error = "browser context or internal token required"
        if error:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await JSONResponse({"error": error}, status_code=status)(scope, receive, send)
            return
        await self.app(scope, receive, send)
