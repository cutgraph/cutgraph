"""Compute MCP bridge (scripts/mcp_gpu.py).

No real GPU/network: the httpx client is mocked so each verb's loopback POST /api/jobs is
asserted (kind + params + internal token + scoped project), and every verb returns a compact JSON string.
"""
from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from scripts import mcp_gpu


class _FakeResp:
    def __init__(self, data: dict, status: int = 200) -> None:
        self._data = data
        self.status_code = status
        self.text = json.dumps(data)

    def json(self) -> dict:
        return self._data


class _FakeClient:
    def __init__(self, sink: list, resp: _FakeResp) -> None:
        self._sink = sink
        self._resp = resp

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *a: object) -> bool:
        return False

    def request(self, method: str, url: str, json: "dict | None" = None, auth=None, headers=None):
        self._sink.append({"method": method, "url": url, "json": json, "auth": auth, "headers": headers})
        return self._resp


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path) -> Iterator[list]:
    monkeypatch.setenv("CUTGRAPH_STUDIO_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("CUTGRAPH_PROJECT", "zzproj")
    token_file = tmp_path / "internal-token"
    token_file.write_text("bridge-test-token", encoding="utf-8")
    monkeypatch.setenv("CUTGRAPH_INTERNAL_TOKEN_FILE", str(token_file))
    sink: list = []
    resp = _FakeResp({"jobId": "abc", "status": "needs-approval"})
    monkeypatch.setattr(mcp_gpu.httpx, "Client", lambda *a, **k: _FakeClient(sink, resp))
    yield sink


def _only(sink: list) -> dict:
    assert len(sink) == 1
    return sink[0]


def test_render_preview_segment_bridges_params(_env) -> None:
    out = mcp_gpu.render_preview_segment(90, 180)
    call = _only(_env)
    assert call["method"] == "POST" and call["url"].endswith("/api/jobs")
    assert call["json"] == {"kind": "render_preview_segment", "project": "zzproj",
                            "params": {"start_f": 90, "end_f": 180}}
    assert call["auth"] is None
    assert call["headers"] == {"X-Cutgraph-Internal": "bridge-test-token"}
    assert json.loads(out)  # a compact JSON string


def test_render_final_has_no_approved_param(_env) -> None:
    """render_final is always human-gated server-side; the bridge exposes NO `approved` param, so
    the agent cannot self-approve — the body carries only kind/project/params."""
    mcp_gpu.render_final()
    call = _only(_env)
    assert call["json"] == {"kind": "render_final", "project": "zzproj", "params": {}}
    assert "approved" not in call["json"]


def test_get_job_is_a_get(_env) -> None:
    job_id = "0123456789abcdef" * 2
    mcp_gpu.get_job(job_id)
    call = _only(_env)
    assert call["method"] == "GET" and call["url"].endswith(f"/api/jobs/{job_id}")
    assert call["json"] is None


@pytest.mark.parametrize("job_id", [
    "", "abc123", "a" * 31, "a" * 33, "A" * 32, "g" * 32,
    "../health", "a" * 32 + "/approve", "a" * 32 + "?poll=1",
    "a" * 32 + "#fragment", "a" * 32 + "\n", "%2e%2e%2fhealth",
])
def test_get_job_rejects_invalid_id_before_request(_env, job_id: str) -> None:
    assert json.loads(mcp_gpu.get_job(job_id)) == {"error": "invalid job id"}
    assert _env == []


@pytest.mark.parametrize("verb", ["transcribe", "make_proxy", "detect_silence"])
def test_simple_verbs_bridge_with_empty_params(_env, verb) -> None:
    getattr(mcp_gpu, verb)()
    call = _only(_env)
    assert call["json"] == {"kind": verb, "project": "zzproj", "params": {}}


def test_registered_tools_match_the_host_allowlist() -> None:
    import asyncio

    from scripts import agent_host

    tools = asyncio.run(mcp_gpu.mcp.list_tools())
    assert {tool.name for tool in tools} == set(agent_host._GPU_TOOLS) == {
        "transcribe", "make_proxy", "detect_silence", "render_final",
        "render_preview_segment", "get_job",
    }



def test_bridge_returns_compact_json_with_status(_env) -> None:
    out = mcp_gpu.transcribe()
    assert " " not in out  # compact separators
    data = json.loads(out)
    assert data["_status"] == 200 and data["jobId"] == "abc"


# ── bridge resilience — the studio being down / replying non-JSON / no creds must NOT raise ──
class _RaisingClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __enter__(self) -> "_RaisingClient":
        return self

    def __exit__(self, *a: object) -> bool:
        return False

    def request(self, *a: object, **k: object):  # noqa: ANN002, ANN003
        raise self._exc


def test_bridge_studio_down_returns_error_string(monkeypatch) -> None:
    """httpx transport failure (studio restarting) → a recoverable `{"error": "studio bridge failed…"}`
    string, never a raised exception that would kill the agent turn."""
    monkeypatch.setenv("CUTGRAPH_PROJECT", "zzproj")
    monkeypatch.setattr(mcp_gpu.httpx, "Client",
                        lambda *a, **k: _RaisingClient(mcp_gpu.httpx.ConnectError("refused")))
    out = json.loads(mcp_gpu.transcribe())
    assert "studio bridge failed" in out["error"]


def test_bridge_non_json_reply_returns_error_text(monkeypatch) -> None:
    """A non-JSON reply (e.g. an HTML 502 page) becomes `{"error": <text>}` rather than crashing."""
    monkeypatch.setenv("CUTGRAPH_PROJECT", "zzproj")

    class _HtmlResp:
        status_code = 502
        text = "<html>bad gateway</html>"

        def json(self) -> dict:
            raise ValueError("not json")

    class _Client:
        def __enter__(self) -> "_Client":
            return self

        def __exit__(self, *a: object) -> bool:
            return False

        def request(self, *a: object, **k: object) -> "_HtmlResp":
            return _HtmlResp()

    monkeypatch.setattr(mcp_gpu.httpx, "Client", lambda *a, **k: _Client())
    out = json.loads(mcp_gpu.transcribe())
    assert out["error"] == "<html>bad gateway</html>"
    assert out["_status"] == 502


def test_bridge_uses_internal_token_only(monkeypatch) -> None:
    """Only the internal token authenticates the local bridge."""
    monkeypatch.setenv("CUTGRAPH_PROJECT", "zzproj")
    sink: list = []
    monkeypatch.setattr(mcp_gpu.httpx, "Client",
                        lambda *a, **k: _FakeClient(sink, _FakeResp({"ok": True})))
    mcp_gpu.transcribe()
    assert sink[0]["auth"] is None
    assert sink[0]["headers"] == {"X-Cutgraph-Internal": "bridge-test-token"}
