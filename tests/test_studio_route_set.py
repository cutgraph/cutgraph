import asyncio

import httpx
from fastapi import HTTPException
from starlette.routing import Route

from studio.app import ROUTERS, app, routes
from studio.routes.reveal import require_loopback_client


def test_route_set_is_pinned() -> None:
    expected = [
        (('DELETE',), '/api/addons/{name}'),
        (('DELETE',), '/api/agent/connection'),
        (('DELETE',), '/api/cast/characters/{character_id}'),
        (('DELETE',), '/api/cast/expressions/{expression_id}'),
        (('DELETE',), '/api/workspaces/{workspace_id}'),
        (('DELETE',), '/api/workspaces/{workspace_id}/assets/{asset_id}'),
        (('GET',), '/api/addons'),
        (('GET',), '/api/agent/connection'),
        (('GET',), '/api/cast'),
        (('GET',), '/api/cast/characters/{character_id}/usage'),
        (('GET',), '/api/channels'),
        (('GET',), '/api/chat/config'),
        (('GET',), '/api/chat/{p:path}/log'),
        (('GET',), '/api/chat/{p:path}/proposals'),
        (('GET',), '/api/compute/jobs'),
        (('GET',), '/api/compute/jobs/{job_id}'),
        (('GET',), '/api/distribution/{document}'),
        (('GET',), '/api/events'),
        (('GET',), '/api/health'),
        (('GET',), '/api/jobs'),
        (('GET',), '/api/jobs/{job_id}'),
        (('GET',), '/api/peaks/{slug:path}'),
        (('GET',), '/api/project/{p:path}'),
        (('GET',), '/api/project/{p:path}/proxy'),
        (('GET',), '/api/project/{p:path}/transcription'),
        (('GET',), '/api/projects'),
        (('GET',), '/api/proxy/{slug}'),
        (('GET',), '/api/script-video/{p}/narration'),
        (('GET',), '/api/stock/providers'),
        (('GET',), '/api/stock/search'),
        (('GET',), '/api/transcript/{p:path}'),
        (('GET',), '/api/updates'),
        (('GET',), '/api/version'),
        (('GET',), '/api/videos/recent'),
        (('GET',), '/api/workspaces'),
        (('GET',), '/api/workspaces/{workspace_id}'),
        (('GET',), '/api/workspaces/{workspace_id}/assets'),
        (('GET',), '/api/workspaces/{workspace_id}/assets/{asset_id}/file'),
        (('GET',), '/api/workspaces/{workspace_id}/assets/{asset_id}/thumb'),
        (('GET',), '/api/workspaces/{workspace_id}/assets/{asset_id}/usage'),
        (('GET',), '/editor'),
        (('GET',), '/editor/{rest:path}'),
        (('GET',), '/media/compiled/{rest:path}'),
        (('GET',), '/media/preview/{rest:path}'),
        (('GET',), '/media/project-asset/{project}/{filename:path}'),
        (('GET',), '/media/proxy/{rest:path}'),
        (('GET',), '/media/source-raw/{rest:path}'),
        (('GET',), '/media/source/{rest:path}'),
        (('GET',), '/media/thumb/{rest:path}'),
        (('GET',), '/media/thumbnail/{name}'),
        (('GET',), '/media/workspace/{workspace_id}/{filename}'),
        (('PATCH',), '/api/cast/characters/{character_id}'),
        (('PATCH',), '/api/cast/expressions/{expression_id}'),
        (('PATCH',), '/api/project/{p:path}'),
        (('PATCH',), '/api/workspaces/{workspace_id}'),
        (('PATCH',), '/api/workspaces/{workspace_id}/assets/{asset_id}'),
        (('POST',), '/api/addons/{name}/install'),
        (('POST',), '/api/agent/connection'),
        (('POST',), '/api/agent/connection/verify'),
        (('POST',), '/api/cast/characters'),
        (('POST',), '/api/cast/describe'),
        (('POST',), '/api/cast/expressions'),
        (('POST',), '/api/chat/{p:path}'),
        (('POST',), '/api/jobs'),
        (('POST',), '/api/jobs/{job_id}/approve'),
        (('POST',), '/api/jobs/{job_id}/cancel'),
        (('POST',), '/api/jobs/{job_id}/retry'),
        (('POST',), '/api/project/{p:path}/scenes/auto'),
        (('POST',), '/api/project/{p:path}/thumbnail/render'),
        (('POST',), '/api/projects'),
        (('POST',), '/api/proposal/{p:path}/{pid}/accept'),
        (('POST',), '/api/proposal/{p:path}/{pid}/status'),
        (('POST',), '/api/proxy/{slug:path}'),
        (('POST',), '/api/reveal'),
        (('POST',), '/api/script-video'),
        (('POST',), '/api/script-video/{p}/narration/{take}/apply'),
        (('POST',), '/api/script/draft'),
        (('POST',), '/api/stock/keys'),
        (('POST',), '/api/updates/check'),
        (('POST',), '/api/updates/install'),
        (('POST',), '/api/verbs/reorder_scenes/{p:path}'),
        (('POST',), '/api/verbs/ripple_cut/{p:path}'),
        (('POST',), '/api/verbs/ripple_restore/{p:path}'),
        (('POST',), '/api/workspaces'),
        (('POST',), '/api/workspaces/{workspace_id}/assets'),
        (('POST',), '/api/workspaces/{workspace_id}/assets/{asset_id}/use-as-source'),
        (('POST',), '/api/workspaces/{workspace_id}/stock/import'),
        (('PUT',), '/api/cast/default'),
        (('PUT',), '/api/project/{p:path}'),
        (('PUT',), '/api/script-video/{p}/narration/{filename}'),
        (('PUT',), '/api/updates/preferences'),
        (('PUT',), '/api/workspaces/{workspace_id}/videos/{slug:path}'),
        (('PUT',), '/upload/{filename:path}'),
    ]
    assert all(isinstance(route, Route) for route in routes())
    actual = sorted(
        (tuple(sorted(route.methods or ())), route.path)
        for route in routes()
        if isinstance(route, Route)
    )
    assert actual == expected


def test_dependency_overrides_reach_included_routes() -> None:
    def refuse() -> None:
        raise HTTPException(status_code=418, detail="overridden")

    async def call() -> int:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
            response = await client.post("/api/reveal", json={}, headers={
                "Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"})
            return response.status_code

    app.dependency_overrides[require_loopback_client] = refuse
    try:
        assert asyncio.run(call()) == 418
    finally:
        app.dependency_overrides.clear()


def test_every_served_route_comes_from_a_listed_router() -> None:
    """routes() walks ROUTERS, so a route added to the app another way would escape the security tests."""
    assert [getattr(route, "original_router", None) for route in app.router.routes] == list(ROUTERS)
