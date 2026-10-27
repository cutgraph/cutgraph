from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.types import Receive, Scope, Send
from starlette.websockets import WebSocket, WebSocketDisconnect

import config
from pipeline import web_security
from scripts import agent_host, mcp_gpu, patch_project, smoke_copilot, studio_app
from studio.app import routes as app_routes
from studio.routes import editor

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
UNSAFE_ROUTES = [
    (method, route.path)
    for route in app_routes()
    if isinstance(route, Route)
    for method in sorted((route.methods or set()) & UNSAFE)
]
HTTP_ROUTES = [
    (method, route.path)
    for route in app_routes()
    if isinstance(route, Route)
    for method in sorted(route.methods or set())
]
LOOPBACK_PEERS = ["127.0.0.1", "127.0.0.2", "127.255.255.255", "::1", "::ffff:127.0.0.1", "::ffff:127.0.0.2"]
UNTRUSTED_PEERS = ["192.0.2.10", "10.0.0.5", "0.0.0.0", "::", "2001:db8::1", "::ffff:192.0.2.10",
                   "localhost", "testclient", None]


@pytest.fixture(autouse=True)
def production_security(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CUTGRAPH_TEST_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("CUTGRAPH_DEV_ORIGIN", raising=False)
    monkeypatch.setattr(web_security, "EXTRA_HOSTS", frozenset())
    monkeypatch.setattr(web_security, "BOUND_PORT", 8765)
    monkeypatch.setattr(web_security, "_ALLOWED_HOSTS", web_security._ALLOWED_HOSTS)
    monkeypatch.setattr(web_security, "_ALLOWED_ORIGINS", web_security._ALLOWED_ORIGINS)
    web_security.configure(8765)


def request(method: str, path: str, headers: dict[str, str] | list[tuple[str, str]],
            peer: str | None = "127.0.0.1", **kwargs) -> httpx.Response:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if peer is None:
            scope.pop("client", None)
        await studio_app.app(scope, receive, send)

    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(peer or "127.0.0.1", 123)),
            base_url="http://127.0.0.1:8765",
        ) as client:
            return await client.request(method, path, headers=headers, **kwargs)

    return asyncio.run(send())


@pytest.mark.parametrize("method,path", HTTP_ROUTES)
@pytest.mark.parametrize("peer", UNTRUSTED_PEERS)
def test_every_route_rejects_non_loopback_peers(method: str, path: str, peer: str | None) -> None:
    assert HTTP_ROUTES
    path = re.sub(r"\{[^}]+\}", "guard_probe", path)
    response = request(method, path, {
        "Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765",
        "Sec-Fetch-Site": "same-origin", "X-Cutgraph-Internal": web_security.INTERNAL_TOKEN,
    }, peer=peer, content=b"{}")
    assert response.status_code == 403
    assert response.json() == {"error": "loopback clients only", "code": 'forbidden'}


@pytest.mark.parametrize("peer", LOOPBACK_PEERS)
def test_loopback_peers_can_read_and_write(peer: str) -> None:
    assert request("GET", "/api/health", {}, peer=peer).status_code == 200
    response = request("POST", "/api/jobs", {"Origin": "http://127.0.0.1:8765"},
                       peer=peer, json={"kind": "source_broll"})
    assert response.status_code == 200
    assert response.json()["available"] is False


@pytest.mark.parametrize("peer", [*LOOPBACK_PEERS, *UNTRUSTED_PEERS])
def test_websocket_checks_peer_before_routing(peer: str | None) -> None:
    routed = False
    messages = []

    async def downstream(scope, receive, send) -> None:
        nonlocal routed
        routed = True

    async def receive() -> dict:
        pytest.fail("rejected handshake must not read from the connection")

    async def send(message) -> None:
        messages.append(message)

    scope = {
        "type": "websocket", "path": "/socket", "client": (peer, 123) if peer else None,
        "headers": [(b"host", b"127.0.0.1:8765"), (b"origin", b"http://127.0.0.1:8765"),
                    (b"sec-fetch-site", b"same-origin"),
                    (b"x-cutgraph-internal", web_security.INTERNAL_TOKEN.encode("ascii"))],
    }
    if peer is None:
        scope.pop("client")
    asyncio.run(web_security.WebSecurityMiddleware(downstream)(scope, receive, send))
    assert routed == (peer in LOOPBACK_PEERS)
    assert messages == ([] if routed else [{"type": "websocket.close", "code": 1008}])


@pytest.mark.parametrize("method,path", UNSAFE_ROUTES)
@pytest.mark.parametrize("headers,status,error", [
    ({"Host": "evil.test:8765"}, 421, "untrusted host"),
    ({"Sec-Fetch-Site": "cross-site"}, 403, "cross-site request"),
    ({"Sec-Fetch-Site": "same-site"}, 403, "cross-site request"),
    ({"Origin": "http://evil.test"}, 403, "origin not allowed"),
    ({"Content-Type": "text/plain"}, 403, "browser context or internal token required"),
    ({}, 403, "browser context or internal token required"),
])
def test_every_unsafe_route_rejects_attacks(method, path, headers, status, error) -> None:
    assert UNSAFE_ROUTES
    path = re.sub(r"\{[^}]+\}", "guard_probe", path)
    response = request(method, path, headers, content=b"{}")
    assert response.status_code == status
    assert response.json() == {"error": error, "code": 'misdirected_request' if status == 421 else 'forbidden'}


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.test", "Sec-Fetch-Site": "cross-site",
     "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "empty", "Content-Type": "text/plain"},
    {"Origin": "http://evil.test", "Sec-Fetch-Site": "cross-site",
     "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
     "Content-Type": "multipart/form-data; boundary=probe"},
    {"Host": "evil.test:8765", "Origin": "http://evil.test:8765", "Sec-Fetch-Site": "same-origin"},
])
def test_browser_write_attacks(headers) -> None:
    response = request("POST", "/api/jobs", headers, content=b"untrusted upload")
    assert response.status_code in {403, 421}
    assert response.json()["error"] in {"cross-site request", "untrusted host"}


def test_cross_site_iframe_is_blocked() -> None:
    response = request("GET", "/editor", {
        "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe",
    })
    assert response.status_code == 403
    assert response.json() == {"error": "cross-site request", "code": 'forbidden'}


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "[::1]:8765"])
def test_loopback_hosts_and_origins_work(host: str) -> None:
    response = request("POST", "/api/jobs", {"Host": host, "Origin": f"http://{host}"},
                       json={"kind": "source_broll"})
    assert response.status_code == 200
    assert response.json()["available"] is False


@pytest.mark.parametrize("host", ["evil.test:8765", "127.0.0.1:9999", "localhost.evil.test",
                                 "studio.test", "testserver", "test", "t"])
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "POST"])
def test_untrusted_hosts_rejected_on_all_methods(host: str, method: str) -> None:
    response = request(method, "/api/health", {"Host": host})
    assert response.status_code == 421


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_fetch_metadata_isolation_on_all_methods(method: str, site: str) -> None:
    response = request(method, "/api/health", {"Sec-Fetch-Site": site})
    assert response.status_code == 403


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_top_level_navigation_get_allowed(site: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "index.html").write_text("<html>editor</html>", encoding="utf-8")
    monkeypatch.setattr(editor, "UI_DIST", tmp_path)
    response = request("GET", "/editor", {
        "Sec-Fetch-Site": site, "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
    })
    assert response.status_code == 200
    assert response.text == "<html>editor</html>"


@pytest.mark.parametrize("headers", [
    {"Origin": "http://127.0.0.1:8765"},
    {"Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "none"},
])
def test_browser_context_allows_writes(headers: dict[str, str]) -> None:
    response = request("POST", "/api/jobs", headers, json={"kind": "source_broll"})
    assert response.status_code == 200


@pytest.mark.parametrize("origin", ["null", "https://127.0.0.1:8765", "http://127.0.0.1:9999",
                                   "http://127.0.0.1:8765/", "http://127.0.0.1:8765.evil.test"])
def test_invalid_origin_cannot_use_internal_token(origin: str) -> None:
    response = request("POST", "/api/jobs", {
        "Origin": origin, "X-Cutgraph-Internal": web_security.INTERNAL_TOKEN,
    })
    assert response.status_code == 403
    assert response.json() == {"error": "origin not allowed", "code": 'forbidden'}


@pytest.mark.parametrize("site", ["cross-site", "same-site", "", "same-origin, cross-site"])
def test_invalid_metadata_cannot_use_internal_token(site: str) -> None:
    response = request("POST", "/api/jobs", {
        "Sec-Fetch-Site": site, "Origin": "http://127.0.0.1:8765",
        "X-Cutgraph-Internal": web_security.INTERNAL_TOKEN,
    })
    assert response.status_code == 403


def test_internal_token_allows_headerless_write_and_invalid_token_fails() -> None:
    response = request("POST", "/api/jobs", {"X-Cutgraph-Internal": web_security.INTERNAL_TOKEN},
                       json={"kind": "source_broll"})
    assert response.status_code == 200
    for token in ("", "wrong", web_security.INTERNAL_TOKEN + "x"):
        response = request("POST", "/api/jobs", {"X-Cutgraph-Internal": token})
        assert response.status_code == 403
        assert response.json() == {"error": "browser context or internal token required", "code": "forbidden"}


def test_bound_port() -> None:
    web_security.configure(9123)
    for host in ("127.0.0.1:9123", "localhost:9123", "[::1]:9123"):
        response = request("POST", "/api/jobs", {"Host": host, "Origin": f"http://{host}"},
                           json={"kind": "source_broll"})
        assert response.status_code == 200
    assert request("GET", "/api/health", {"Host": "127.0.0.1:8765"}).status_code == 421
    assert request("GET", "/api/health", {"Host": "192.0.2.10:9123"}).status_code == 421


def test_dev_origin_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTGRAPH_DEV_ORIGIN", "http://127.0.0.1:5173")
    web_security.configure(8765)
    response = request("POST", "/api/jobs", {"Host": "127.0.0.1:5173", "Origin": "http://127.0.0.1:5173"},
                       json={"kind": "source_broll"})
    assert response.status_code == 200
    assert request("GET", "/api/health", {"Host": "127.0.0.1:5174"}).status_code == 421


def test_same_origin_project_edit_is_saved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    project = {"schemaVersion": 1, "name": "guard_probe", "fps": 30, "width": 1920, "height": 1080,
               "revision": 0, "tracks": []}
    headers = {"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"}
    saved = request("PUT", "/api/project/guard_probe", headers, json={"project": project, "baseRevision": 0})
    assert saved.status_code == 200
    edited = request("PATCH", "/api/project/guard_probe", headers, json={
        "ops": [{"op": "add", "path": "/title", "value": "Edited video"}], "baseRevision": 1,
    })
    assert edited.status_code == 200
    loaded = request("GET", "/api/project/guard_probe", {})
    assert loaded.json()["project"]["title"] == "Edited video"
    assert json.loads((tmp_path / "guard_probe.json").read_text(encoding="utf-8"))["revision"] == 2


@pytest.mark.parametrize("content_type", [None, "text/plain", "multipart/form-data; boundary=probe"])
@pytest.mark.parametrize("method", sorted(UNSAFE))
def test_allowed_writes_do_not_require_json(method: str, content_type: str | None) -> None:
    app = FastAPI()
    app.add_middleware(web_security.WebSecurityMiddleware)

    async def echo(request: Request) -> dict:
        return {"body": (await request.body()).decode()}

    app.add_api_route("/write", echo, methods=[method])

    async def send() -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://127.0.0.1:8765") as client:
            headers = {"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"}
            if content_type:
                headers["Content-Type"] = content_type
            response = await client.request(method, "/write", headers=headers, content=b"edit")
            assert response.status_code == 200
            assert response.json() == {"body": "edit"}

    asyncio.run(send())


def test_agent_options_pass_internal_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CUTGRAPH_INTERNAL_TOKEN", "stale-token")
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", SimpleNamespace(ClaudeAgentOptions=lambda **kw: kw))
    monkeypatch.setattr(agent_host, "_bundled_cli", lambda: "synthetic-cli")
    monkeypatch.setattr(agent_host, "build_editor_server", lambda ctx: {})
    ctx = agent_host.ToolCtx("guard_probe", lambda: (None, 0), lambda: None, lambda: 30, lambda event: None)
    options = agent_host.build_options(tmp_path, ctx)
    assert isinstance(options, dict)
    gpu_env = options["mcp_servers"]["gpu"]["env"]
    assert "CUTGRAPH_INTERNAL_TOKEN" not in gpu_env
    assert Path(gpu_env["CUTGRAPH_INTERNAL_TOKEN_FILE"]).read_text(encoding="utf-8") == web_security.INTERNAL_TOKEN


def test_mcp_bridge_reaches_jobs_with_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_file = tmp_path / "internal-token"
    token_file.write_text(web_security.INTERNAL_TOKEN, encoding="utf-8")
    monkeypatch.setenv("CUTGRAPH_INTERNAL_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("CUTGRAPH_STUDIO_URL", "http://127.0.0.1:8765")

    class InProcessClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, url, *, json, headers):
            assert headers == {"X-Cutgraph-Internal": web_security.INTERNAL_TOKEN}
            return request(method, url, headers, json=json)

    monkeypatch.setattr(mcp_gpu.httpx, "Client", lambda **kwargs: InProcessClient())
    result = json.loads(mcp_gpu._request("POST", "/api/jobs", {"kind": "source_broll"}))
    assert result["_status"] == 200
    assert result["available"] is False


@pytest.fixture
def websocket_client() -> Iterator[TestClient]:
    async def echo(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("connected")
        await websocket.close()

    app = web_security.WebSecurityMiddleware(Starlette(routes=[WebSocketRoute("/socket", echo)]))
    with TestClient(app, base_url="http://127.0.0.1:8765", headers={"Host": "127.0.0.1:8765"},
                    client=("127.0.0.1", 123)) as client:
        yield client


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.test"},
    {"Origin": "null"},
    {"Origin": "http://127.0.0.1:9999"},
    {"Origin": "http://evil.test", "X-Cutgraph-Internal": web_security.INTERNAL_TOKEN},
    {"Host": "evil.test:8765", "Origin": "http://127.0.0.1:8765"},
    {"Host": "evil.test:8765", "X-Cutgraph-Internal": web_security.INTERNAL_TOKEN},
    {"Host": "127.0.0.1:9999", "Origin": "http://127.0.0.1:8765"},
    {},
    {"Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "none"},
    {"X-Cutgraph-Internal": "wrong"},
])
def test_websocket_rejects_untrusted_handshake(websocket_client: TestClient, headers: dict[str, str]) -> None:
    with pytest.raises(WebSocketDisconnect) as closed:
        with websocket_client.websocket_connect("/socket", headers=headers):
            pytest.fail("untrusted handshake was accepted")
    assert closed.value.code == 1008


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "[::1]:8765"])
def test_websocket_allows_loopback_origin(websocket_client: TestClient, host: str) -> None:
    with websocket_client.websocket_connect("/socket", headers={"Host": host, "Origin": f"http://{host}"}) as socket:
        assert socket.receive_text() == "connected"


def test_websocket_allows_internal_token(websocket_client: TestClient) -> None:
    with websocket_client.websocket_connect("/socket", headers={
        "X-Cutgraph-Internal": web_security.INTERNAL_TOKEN,
    }) as socket:
        assert socket.receive_text() == "connected"


def test_websocket_allows_dev_origin(websocket_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTGRAPH_DEV_ORIGIN", "http://127.0.0.1:5173")
    web_security.configure(8765)
    with websocket_client.websocket_connect("/socket", headers={
        "Host": "127.0.0.1:5173", "Origin": "http://127.0.0.1:5173",
    }) as socket:
        assert socket.receive_text() == "connected"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
def test_portless_loopback_host_is_rejected(host: str) -> None:
    response = request("GET", "/api/health", {"Host": host})
    assert response.status_code == 421


@pytest.mark.parametrize("origin", ["http://127.0.0.1", "http://localhost", "http://[::1]"])
def test_portless_loopback_origin_is_rejected(origin: str) -> None:
    response = request("POST", "/api/jobs", {"Origin": origin}, json={"kind": "source_broll"})
    assert response.status_code == 403
    assert response.json() == {"error": "origin not allowed", "code": 'forbidden'}


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("site", ["", "Same-Origin", "NONE", "same-origin, none", "unknown"])
def test_invalid_fetch_metadata_is_rejected_on_all_methods(method: str, site: str) -> None:
    response = request(method, "/api/health", {
        "Sec-Fetch-Site": site, "Origin": "http://127.0.0.1:8765",
        "X-Cutgraph-Internal": web_security.INTERNAL_TOKEN,
    })
    assert response.status_code == 403
    if method == "HEAD":
        assert response.content == b""
    else:
        assert response.json() == {"error": "cross-site request", "code": "forbidden"}


@pytest.mark.parametrize("site", ["cross-site", "same-site", "", "Same-Origin", "unknown"])
@pytest.mark.parametrize("path", ["/api/health", "/api/jobs", "/media/probe", "/editorial", "/editor-probe"])
def test_navigation_to_non_editor_path_is_rejected(site: str, path: str) -> None:
    response = request("GET", path, {
        "Sec-Fetch-Site": site, "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
    })
    assert response.status_code == 403
    assert response.json() == {"error": "cross-site request", "code": 'forbidden'}


@pytest.mark.parametrize("site", ["", "Same-Origin", "unknown", "same-origin, none"])
def test_invalid_metadata_is_not_exempt_editor_navigation(site: str) -> None:
    response = request("GET", "/editor", {
        "Sec-Fetch-Site": site, "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
    })
    assert response.status_code == 403


@pytest.mark.parametrize("dev_origin", ["http://evil.test:5173", "http://192.0.2.10:5173"])
def test_non_loopback_dev_origin_is_rejected(monkeypatch: pytest.MonkeyPatch, dev_origin: str) -> None:
    monkeypatch.setenv("CUTGRAPH_DEV_ORIGIN", dev_origin)
    web_security.configure(8765)
    response = request("POST", "/api/jobs", {"Origin": dev_origin}, json={"kind": "source_broll"})
    assert response.status_code == 403
    assert response.json() == {"error": "origin not allowed", "code": 'forbidden'}


def test_legacy_test_host_environment_cannot_allow_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTGRAPH_TEST_ALLOWED_HOSTS", "evil.test:8765")
    web_security.configure(8765)
    assert request("GET", "/api/health", {"Host": "evil.test:8765"}).status_code == 421


def test_dev_allowlist_does_not_change_between_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    web_security.configure(8765)
    monkeypatch.setenv("CUTGRAPH_DEV_ORIGIN", "http://127.0.0.1:5173")
    assert request("GET", "/api/health", {"Host": "127.0.0.1:5173"}).status_code == 421


def test_studio_does_not_export_internal_token() -> None:
    assert os.environ.get("CUTGRAPH_INTERNAL_TOKEN") != web_security.INTERNAL_TOKEN


def test_patch_cli_uses_origin_and_ignores_token_environment_and_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTGRAPH_INTERNAL_TOKEN", "cli-test-token")

    class Client:
        def __init__(self, **kwargs):
            assert "X-Cutgraph-Internal" not in kwargs["headers"]
            assert kwargs["trust_env"] is False
            assert "auth" not in kwargs
            assert kwargs["headers"]["Origin"] == "http://127.0.0.1:9123"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            return httpx.Response(200, json={"revision": 0})

        def patch(self, url, *, json):
            return httpx.Response(200, json={"revision": 1})

    monkeypatch.setattr(patch_project.httpx, "Client", Client)
    monkeypatch.setattr(sys, "argv", ["patch_project", "guard_probe", "--port", "9123", "--ops", "[]"])
    assert patch_project.main() == 0


def test_smoke_request_sends_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    original = httpx.Client

    def client(**kwargs):
        assert kwargs["trust_env"] is False

        def respond(req):
            assert req.headers["Origin"] == "http://127.0.0.1:9123"
            assert req.headers["Content-Type"] == "application/json"
            return httpx.Response(200, json={"ok": True})

        return original(transport=httpx.MockTransport(respond))

    urllib_module = getattr(smoke_copilot, "urllib", None)
    if urllib_module is not None:
        def reject_urllib(*args, **kwargs):
            raise AssertionError("smoke requests must use the proxy-independent HTTP client")

        monkeypatch.setattr(urllib_module.request, "urlopen", reject_urllib)
    monkeypatch.setattr(smoke_copilot, "httpx", httpx, raising=False)
    monkeypatch.setattr(httpx, "Client", client)
    smoke_copilot._request("POST", "http://127.0.0.1:9123/api/chat/guard_probe",
                           {"Content-Type": "application/json"}, b"{}")


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]",
                                 "127.0.0.1:80", "localhost:80", "[::1]:80"])
def test_default_http_port_allows_portless_loopback(host: str) -> None:
    web_security.configure(80)
    response = request("POST", "/api/jobs", {"Host": host, "Origin": f"http://{host}"},
                       json={"kind": "source_broll"})
    assert response.status_code == 200


@pytest.mark.parametrize("dev_origin", ["HTTP://LOCALHOST:5173/", "http://127.0.0.1:5173",
                                      "http://[::1]:5173", "https://localhost:5173"])
def test_loopback_dev_origin_is_lowercased(monkeypatch: pytest.MonkeyPatch, dev_origin: str) -> None:
    monkeypatch.setenv("CUTGRAPH_DEV_ORIGIN", dev_origin)
    web_security.configure(8765)
    origin = dev_origin.lower().rstrip("/")
    response = request("POST", "/api/jobs", {"Host": origin.split("//")[1], "Origin": origin},
                       json={"kind": "source_broll"})
    assert response.status_code == 200


@pytest.mark.parametrize("dev_origin", ["http://localhost", "http://localhost:bad", "http://localhost:65536",
                                      "http://localhost:5173/path", "http://user@localhost:5173",
                                      "http://localhost:5173?query", "http://localhost:5173#fragment",
                                      "ftp://localhost:5173", "http://localhost.evil.test:5173"])
def test_invalid_dev_origin_is_ignored(monkeypatch: pytest.MonkeyPatch, dev_origin: str) -> None:
    monkeypatch.setenv("CUTGRAPH_DEV_ORIGIN", dev_origin)
    web_security.configure(8765)
    assert request("GET", "/api/health", {"Host": "localhost:5173"}).status_code == 421


def test_extra_host_setter_rebuilds_allowlists() -> None:
    host = "guard.test:9123"
    web_security.set_extra_hosts(frozenset({host.upper()}))
    response = request("POST", "/api/jobs", {"Host": host, "Origin": f"http://{host}"},
                       json={"kind": "source_broll"})
    assert response.status_code == 200
    web_security.set_extra_hosts(frozenset())
    assert request("GET", "/api/health", {"Host": host}).status_code == 421


@pytest.mark.parametrize("path", ["/", "/editor", "/editor/", "/editor/deep/path"])
@pytest.mark.parametrize("site", ["cross-site", "same-site", "same-origin", "none"])
def test_editor_document_navigation_reaches_downstream(path: str, site: str) -> None:
    async def page(request: Request):
        return JSONResponse({"served": True})

    app = web_security.WebSecurityMiddleware(Starlette(routes=[Route("/{path:path}", page)]))
    with TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 123)) as client:
        response = client.get(path, headers={
            "Sec-Fetch-Site": site, "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
        })
    assert response.status_code == 200
    assert response.json() == {"served": True}


@pytest.mark.parametrize("site", ["cross-site", "same-site", "", "Same-Origin", "none, same-origin"])
def test_websocket_rejects_invalid_metadata(websocket_client: TestClient, site: str) -> None:
    with pytest.raises(WebSocketDisconnect) as closed:
        with websocket_client.websocket_connect("/socket", headers={
            "Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": site,
            "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
            "X-Cutgraph-Internal": web_security.INTERNAL_TOKEN,
        }):
            pytest.fail("invalid fetch metadata was accepted")
    assert closed.value.code == 1008


@pytest.mark.parametrize("name,value", [
    ("Host", "127.0.0.1:8765"), ("Origin", "http://127.0.0.1:8765"),
    ("Sec-Fetch-Site", "same-origin"), ("Sec-Fetch-Mode", "navigate"),
    ("Sec-Fetch-Dest", "document"), ("X-Cutgraph-Internal", "wrong"),
])
def test_duplicate_security_headers_are_rejected(name: str, value: str) -> None:
    response = request("POST", "/api/jobs", [(name, value), (name, value)])
    assert response.status_code == (421 if name == "Host" else 403)


def test_smoke_headers_ignore_removed_token_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTGRAPH_INTERNAL_TOKEN", "cli-test-token")
    assert smoke_copilot._headers() == {
        "Content-Type": "application/json",
    }


def assert_security_headers(response: httpx.Response) -> None:
    directives = dict(part.strip().split(" ", 1) for part in response.headers["Content-Security-Policy"].split(";"))
    assert directives == {
        "default-src": "'self'", "script-src": "'self'", "style-src": "'self'",
        "img-src": "'self' blob: data: https://images.pexels.com https://cdn.pixabay.com https://pixabay.com https://i.vimeocdn.com",
        "media-src": "'self' blob: data: https://videos.pexels.com https://cdn.pixabay.com",
        "font-src": "'self'", "connect-src": "'self'", "object-src": "'none'",
        "base-uri": "'none'", "form-action": "'self'", "frame-ancestors": "'none'",
    }
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"


@pytest.mark.parametrize("path", ["/editor", "/editor/", "/api/health"])
def test_security_headers_on_editor_and_api(path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "index.html").write_text("<html>editor</html>", encoding="utf-8")
    monkeypatch.setattr(editor, "UI_DIST", tmp_path)
    response = request("GET", path, {})
    assert response.status_code == 200
    assert_security_headers(response)


@pytest.mark.parametrize("path,headers,peer,status", [
    ("/missing", {}, "127.0.0.1", 404),
    ("/api/health", {"Host": "evil.test"}, "127.0.0.1", 421),
    ("/api/health", {"Sec-Fetch-Site": "cross-site"}, "127.0.0.1", 403),
    ("/api/health", {}, "192.0.2.10", 403),
])
def test_security_headers_on_errors(path: str, headers: dict, peer: str, status: int) -> None:
    response = request("GET", path, headers, peer=peer)
    assert response.status_code == status
    assert_security_headers(response)


def test_security_headers_on_unhandled_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_rel: str) -> None:
        raise RuntimeError("response failed")

    monkeypatch.setattr(editor, "_editor_response", fail)
    client = TestClient(studio_app.app, base_url="http://127.0.0.1:8765",
                        client=("127.0.0.1", 123), raise_server_exceptions=False)
    response = client.get("/editor/")
    assert response.status_code == 500
    assert response.json() == {"error": "Studio could not complete the request.", "code": "internal_error"}
    assert_security_headers(response)


def test_security_headers_preserve_media_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "FINAL_DIR", tmp_path)
    (tmp_path / "probe.compiled.mp4").write_bytes(b"0123456789")
    response = request("GET", "/media/compiled/probe", {"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["Content-Range"] == "bytes 2-5/10"
    assert response.headers["Accept-Ranges"] == "bytes"
    assert_security_headers(response)


def test_mcp_bridge_uses_startup_port_and_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_file = tmp_path / "internal-token"
    token_file.write_text(web_security.INTERNAL_TOKEN, encoding="utf-8")
    calls: list[tuple[str, str, str | None, object]] = []
    job_id = "0123456789abcdef" * 2

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.reply(body)

        def do_GET(self) -> None:
            self.reply(None)

        def reply(self, body: object) -> None:
            calls.append((self.command, self.path, self.headers.get("X-Cutgraph-Internal"), body))
            payload = json.dumps({"id": job_id, "status": "queued"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        port = server.server_port
        assert port != 8765
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            monkeypatch.setenv("CUTGRAPH_STUDIO_URL", "http://127.0.0.1:1")
            monkeypatch.setenv("CUTGRAPH_PROJECT", "guard_probe")
            monkeypatch.setenv("CUTGRAPH_INTERNAL_TOKEN_FILE", str(token_file))
            monkeypatch.setattr(sys, "argv", ["studio_app", "--port", str(port), "--no-browser"])

            from contextlib import nullcontext

            def run(instance, *, sockets) -> None:
                assert instance.config.app is studio_app.app
                assert sockets == [server.socket]
                assert web_security.BOUND_PORT == port
                assert json.loads(mcp_gpu.transcribe())["_status"] == 200
                assert json.loads(mcp_gpu.get_job(job_id))["status"] == "queued"

            monkeypatch.setattr(studio_app.uvicorn.Config, "bind_socket", lambda _: nullcontext(server.socket))
            monkeypatch.setattr(studio_app.uvicorn.Server, "run", run)
            assert studio_app.main() == 0
        finally:
            server.shutdown()
            thread.join(timeout=5)
        assert not thread.is_alive()
    assert calls == [
        ("POST", "/api/jobs", web_security.INTERNAL_TOKEN,
         {"kind": "transcribe", "project": "guard_probe", "params": {}}),
        ("GET", f"/api/jobs/{job_id}", web_security.INTERNAL_TOKEN, None),
    ]
