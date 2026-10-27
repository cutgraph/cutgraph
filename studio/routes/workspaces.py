from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from studio.errors import JSONResponse

import config
from pipeline import cast as cast_mod
from pipeline import edits as edits_mod
from pipeline import library as library_mod
from pipeline import project as project_mod
from pipeline import names
from studio import compute, helpers, state
from studio.multipart import MultipartFiles
from studio.errors import public_error
from pipeline.names import NameConflict

router = APIRouter()


def _video_summary(slug: str, doc: dict, mtime: float) -> dict:
    title, name = doc.get("title"), doc.get("name")
    if not (isinstance(title, str) and title):
        title = name if isinstance(name, str) and name else slug
    summary: dict = {
        "slug": slug,
        "title": title,
        "updatedAt": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
    }
    duration = _project_duration_s(doc)
    if duration is not None:
        summary["durationS"] = duration
    # A video with a thumbnail is covered by it, which the client draws with motion-core.
    if isinstance(doc.get("thumbnail"), dict):
        summary["thumbnail"] = True
    # A script video's speech media is black: its still is its first animated scene, which the client
    # draws with motion-core.
    if doc.get("narration") is not None:
        summary["script"] = True
        return summary
    stem = _speech_media_stem(doc)
    if stem:
        summary["thumbUrl"] = f"/media/thumb/{quote(stem, safe='')}"
    return summary


# The Export sheet's copy of a video at another format (`<video>__<W>x<H>`, derivedProjectName in
# ui/src/v3/story/export/formats.ts). Shorts are `<parent>__<slug>` videos of their own and stay listed.
_RENDER_DOC_RE = re.compile(r"^(?P<video>.+)__\d+x\d+$")


def _workspace_video_groups() -> dict[str | None, list[dict]]:
    """Every video grouped by its (possibly absent) workspaceId. None is the virtual "unsorted"
    bucket; an unreadable or non-dict document is silently skipped (matches `_list_projects`'s
    corrupt-file tolerance so a stray bad file never 500s this listing), and so is an export's
    render document, which is rebuilt on every export and is not a video of its own."""
    groups: dict[str | None, list[dict]] = {}
    if not config.PROJECTS_DIR.exists():
        return groups
    paths = sorted(config.PROJECTS_DIR.glob("*.json"))
    stems = {path.stem for path in paths}
    for path in paths:
        render_doc = _RENDER_DOC_RE.match(path.stem)
        if render_doc and render_doc["video"] in stems:
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                continue
            mtime = path.stat().st_mtime
            workspace_id = doc.get("workspaceId")
            if not isinstance(workspace_id, str) or not workspace_id:
                workspace_id = None
            groups.setdefault(workspace_id, []).append(_video_summary(path.stem, doc, mtime))
        except (json.JSONDecodeError, OSError, TypeError, ValueError, AttributeError):
            continue
    return groups


def _unsorted_workspace(videos: list[dict]) -> dict:
    return {
        "id": library_mod.UNSORTED_WORKSPACE_ID, "name": "Unsorted", "createdAt": None, "updatedAt": None,
        "videoCount": len(videos), "assetCount": 0,
    }


@router.get("/api/workspaces")
def api_list_workspaces() -> Response:
    """Every workspace, Unsorted last, each with its video count, whether it is archived and when it was
    last used."""
    groups = _workspace_video_groups()
    unsorted = groups.pop(None, [])
    rows = [(ws, groups.get(ws["id"], [])) for ws in library_mod.list_workspaces()]
    rows.append((_unsorted_workspace(unsorted), unsorted))
    return JSONResponse([library_mod.workspace_row(ws, [v["updatedAt"] for v in videos]) for ws, videos in rows])


@router.get("/api/videos/recent")
def api_recent_videos(limit: int = 8) -> Response:
    """Every video across every workspace, most recently edited first, for Home's Recent
    row — the same `_video_summary` rows `/api/workspaces/{id}` returns, each tagged with its
    workspace so the card can name it."""
    groups = _workspace_video_groups()
    names = {w["id"]: w["name"] for w in library_mod.list_workspaces()}
    out = [
        {**video, "workspaceId": workspace_id or library_mod.UNSORTED_WORKSPACE_ID,
         "workspaceName": names.get(workspace_id, workspace_id) if workspace_id else "Unsorted"}
        for workspace_id, videos in groups.items() for video in videos
    ]
    out.sort(key=lambda v: v["updatedAt"], reverse=True)
    return JSONResponse(out[:max(1, min(limit, 50))])


@router.post("/api/workspaces")
async def api_create_workspace(request: Request) -> Response:
    try:
        body = await helpers.read_json(request)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    name = body.get("name") if isinstance(body, dict) else None
    if not isinstance(name, str):
        return JSONResponse({"error": "name is required"}, status_code=400)
    try:
        workspace = await asyncio.to_thread(library_mod.create_workspace, name)
    except ValueError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    return JSONResponse(workspace, status_code=201)


@router.get("/api/workspaces/{workspace_id}")
def api_get_workspace(workspace_id: str) -> Response:
    groups = _workspace_video_groups()
    if workspace_id == library_mod.UNSORTED_WORKSPACE_ID:
        videos = groups.get(None, [])
        return JSONResponse({
            "workspace": {"id": library_mod.UNSORTED_WORKSPACE_ID, "name": "Unsorted",
                          "createdAt": None, "updatedAt": None, "brand": library_mod.workspace_brand(None)},
            "videos": videos, "assets": [],
        })
    try:
        workspace = library_mod.get_workspace(workspace_id)
    except ValueError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    if workspace is None:
        return JSONResponse({"error": "unknown workspace"}, status_code=404)
    return JSONResponse({
        "workspace": {**workspace, "brand": library_mod.brand_of(workspace)}, "videos": groups.get(workspace_id, []),
        "assets": library_mod.list_assets(workspace_id),
    })


def _patch_workspace(workspace_id: str, name: str | None, brand: dict | None, archived: bool | None) -> dict:
    workspace = library_mod.rename_workspace(workspace_id, name) if name is not None else None
    if brand is not None:
        workspace = library_mod.set_brand(workspace_id, brand)
    if archived is not None:
        workspace = library_mod.set_archived(workspace_id, archived)
    assert workspace is not None
    return {**workspace, "brand": library_mod.brand_of(workspace), "archived": workspace.get("archived") is True}


@router.patch("/api/workspaces/{workspace_id}")
async def api_rename_workspace(workspace_id: str, request: Request) -> Response:
    """Rename a workspace (`name`), set its brand (`brand: {name?, accent?}`, null or "" restoring a
    field's default: the eyebrow over its videos' animated headlines) and/or archive or restore it
    (`archived`)."""
    try:
        body = await helpers.read_json(request)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    body = body if isinstance(body, dict) else {}
    name, brand, archived = body.get("name"), body.get("brand"), body.get("archived")
    if ((name is not None and not isinstance(name, str)) or (brand is not None and not isinstance(brand, dict))
            or (archived is not None and not isinstance(archived, bool))):
        return JSONResponse({"error": "name must be a string, brand an object and archived a boolean"}, status_code=400)
    if name is None and brand is None and archived is None:
        return JSONResponse({"error": "name, brand or archived is required"}, status_code=400)
    try:
        workspace = await asyncio.to_thread(_patch_workspace, workspace_id, name, brand, archived)
    except ValueError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    except FileNotFoundError:
        return JSONResponse({"error": "unknown workspace"}, status_code=404)
    return JSONResponse(workspace)


@router.delete("/api/workspaces/{workspace_id}")
def api_delete_workspace(workspace_id: str) -> Response:
    """Delete an empty workspace. A built-in one, or one with videos, library assets or characters,
    answers 409 `{error, reason}`, the reason naming what is in it."""
    videos = len(_workspace_video_groups().get(workspace_id, []))
    characters = sum(c["workspaceId"] == workspace_id for c in cast_mod.video_cast(workspace_id)["characters"])
    try:
        library_mod.delete_workspace(workspace_id, videos=videos, characters=characters)
    except library_mod.DeleteRefused as e:
        return JSONResponse({"error": e.error, "reason": e.reason}, status_code=409)
    except ValueError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    except FileNotFoundError:
        return JSONResponse({"error": "unknown workspace"}, status_code=404)
    return JSONResponse({"ok": True})


@router.get("/api/workspaces/{workspace_id}/assets")
def api_list_workspace_assets(workspace_id: str, kind: str | None = None) -> Response:
    try:
        assets = library_mod.list_assets(workspace_id, kind=kind)
    except ValueError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    except FileNotFoundError:
        return JSONResponse({"error": "unknown workspace"}, status_code=404)
    return JSONResponse({"assets": assets})


def _import_library_uploads(workspace_id: str, files: list[tuple[str, Path]]) -> tuple[list[dict], list[str]]:
    assets: list[dict] = []
    deduped: list[str] = []
    for filename, tmp in files:
        asset, was_dup = library_mod.import_asset(workspace_id, tmp, display_name=filename)
        assets.append(asset)
        if was_dup:
            deduped.append(asset["id"])
    return assets, deduped


def _finish_library_import(
    workspace_id: str, files: list[tuple[str, Path]], directory: Path,
) -> tuple[list[dict], list[str]]:
    try:
        return compute._gpu_run(_import_library_uploads, workspace_id, files)
    finally:
        shutil.rmtree(directory)


@router.post("/api/workspaces/{workspace_id}/assets")
async def api_import_workspace_assets(workspace_id: str, request: Request) -> Response:
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        return JSONResponse({"error": "expected multipart/form-data"}, status_code=400)
    try:
        workspace = library_mod.get_workspace(workspace_id)
    except ValueError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    if workspace is None:
        return JSONResponse({"error": "unknown workspace"}, status_code=404)
    message = "upload exceeds the size limit"
    helpers.check_body_limit(request, config.MAX_LIBRARY_UPLOAD_BYTES, message)
    directory = Path(mkdtemp(prefix=".upload-", dir=config.WORKSPACES_DIR))
    importing = False
    try:
        parser = MultipartFiles(content_type, directory)
        try:
            async for chunk in helpers.limited_stream(request, config.MAX_LIBRARY_UPLOAD_BYTES, message):
                parser.feed(chunk)
            files = parser.finish()
        finally:
            parser.close()
        if not files:
            return JSONResponse({"error": "no files provided"}, status_code=400)
        pending = asyncio.get_running_loop().run_in_executor(None, _finish_library_import, workspace_id, files, directory)
        importing = True
        # Cancellation must leave cleanup with the thread that may still hold the files open.
        pending.add_done_callback(lambda result: result.exception())
        assets, deduped = await asyncio.shield(pending)
    except NameConflict:
        raise
    except ValueError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    except (OSError, ConnectionResetError):
        return JSONResponse({"error": "upload interrupted"}, status_code=400)
    finally:
        if not importing:
            shutil.rmtree(directory)
    return JSONResponse({"assets": assets, "deduped": deduped}, status_code=201)


@router.patch("/api/workspaces/{workspace_id}/assets/{asset_id}")
async def api_update_workspace_asset(workspace_id: str, asset_id: str, request: Request) -> Response:
    try:
        body = await helpers.read_json(request)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid body"}, status_code=400)
    name, tags = body.get("name"), body.get("tags")
    if name is not None and not isinstance(name, str):
        return JSONResponse({"error": "name must be a string"}, status_code=400)
    if tags is not None and not isinstance(tags, list):
        return JSONResponse({"error": "tags must be a list"}, status_code=400)
    try:
        asset = await asyncio.to_thread(library_mod.update_asset, workspace_id, asset_id, name=name, tags=tags)
    except ValueError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    except FileNotFoundError:
        return JSONResponse({"error": "unknown asset"}, status_code=404)
    return JSONResponse(asset)


@router.delete("/api/workspaces/{workspace_id}/assets/{asset_id}")
async def api_delete_workspace_asset(workspace_id: str, asset_id: str) -> Response:
    try:
        deleted = await asyncio.to_thread(library_mod.delete_asset, workspace_id, asset_id)
    except ValueError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    except FileNotFoundError:
        return JSONResponse({"error": "unknown workspace"}, status_code=404)
    if not deleted:
        return JSONResponse({"error": "unknown asset"}, status_code=404)
    return JSONResponse({"ok": True})


def _asset_usage(workspace_id: str, asset: dict) -> int:
    """How many videos reference this library asset: every clip whose `media.source`
    is its library ref — a brand asset can be used from any workspace's video, not only its own.
    Cheap: one glob + json.loads per project, no ffmpeg/ffprobe."""
    if not isinstance(asset.get("file"), str):
        return 0
    ref = f"workspaces/{workspace_id}/library/{asset['file']}"
    count = 0
    if not config.PROJECTS_DIR.exists():
        return 0
    for path in config.PROJECTS_DIR.glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(doc, dict):
            continue
        used = False
        for track in doc.get("tracks") or []:
            if not isinstance(track, dict):
                continue
            for clip in track.get("clips") or []:
                media = clip.get("media") if isinstance(clip, dict) else None
                if isinstance(media, dict) and media.get("source") == ref:
                    used = True
                    break
            if used:
                break
        if used:
            count += 1
    return count


@router.get("/api/workspaces/{workspace_id}/assets/{asset_id}/usage")
async def api_workspace_asset_usage(workspace_id: str, asset_id: str) -> Response:
    try:
        asset = await asyncio.to_thread(library_mod.get_asset, workspace_id, asset_id)
    except ValueError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    except FileNotFoundError:
        return JSONResponse({"error": "unknown workspace"}, status_code=404)
    if asset is None:
        return JSONResponse({"error": "unknown asset"}, status_code=404)
    videos = await asyncio.to_thread(_asset_usage, workspace_id, asset)
    return JSONResponse({"videos": videos})


@router.get("/api/workspaces/{workspace_id}/assets/{asset_id}/file")
async def api_workspace_asset_file(workspace_id: str, asset_id: str) -> Response:
    try:
        path = await asyncio.to_thread(library_mod.asset_file_path, workspace_id, asset_id)
    except ValueError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    except FileNotFoundError:
        return JSONResponse({"error": "unknown workspace"}, status_code=404)
    if path is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path)


@router.get("/api/workspaces/{workspace_id}/assets/{asset_id}/thumb")
async def api_workspace_asset_thumb(workspace_id: str, asset_id: str) -> Response:
    try:
        path = await asyncio.to_thread(library_mod.asset_thumb_path, workspace_id, asset_id)
    except ValueError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    except FileNotFoundError:
        return JSONResponse({"error": "unknown workspace"}, status_code=404)
    if path is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type="image/jpeg")


def _link_library_asset_as_source(workspace_id: str, asset_id: str) -> tuple[str, str]:
    """Hardlink (falling back to a copy across devices) a workspace library video asset into
    DATA_DIR so `/api/projects` can build a new editor project from it — the "from your
    library" pick — without re-uploading bytes the user already imported. The asset id (a
    content-hash slug, already `_SAFE`) doubles as the DATA_DIR slug, so re-picking the same
    asset is a no-op join rather than a second copy."""
    src = library_mod.asset_file_path(workspace_id, asset_id)
    if src is None:
        raise FileNotFoundError(asset_id)
    dest = config.DATA_DIR / f"{asset_id}{src.suffix.lower()}"
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with state._PROJECT_CREATE_LOCK:
        names.check_case_collision(dest, source_stem=True)
        if not dest.exists():
            try:
                os.link(src, dest)
            except FileExistsError:
                raise
            except OSError:
                with dest.open("xb") as target:
                    try:
                        with src.open("rb") as source:
                            shutil.copyfileobj(source, target)
                    except BaseException:
                        dest.unlink(missing_ok=True)
                        raise
    return dest.name, asset_id


@router.post("/api/workspaces/{workspace_id}/assets/{asset_id}/use-as-source")
async def api_use_library_asset_as_source(workspace_id: str, asset_id: str) -> Response:
    """Resolve a library video asset to an `/api/projects` `source` slug."""
    try:
        asset = await asyncio.to_thread(library_mod.get_asset, workspace_id, asset_id)
    except ValueError as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    except FileNotFoundError:
        return JSONResponse({"error": "unknown workspace"}, status_code=404)
    if asset is None:
        return JSONResponse({"error": "unknown asset"}, status_code=404)
    if asset.get("kind") != "video":
        return JSONResponse({"error": "asset is not a video"}, status_code=400)
    try:
        name, slug = await asyncio.to_thread(_link_library_asset_as_source, workspace_id, asset_id)
    except FileNotFoundError:
        return JSONResponse({"error": "asset file is missing"}, status_code=404)
    compute._spawn_proxy(config.DATA_DIR / name)
    return JSONResponse({"ok": True, "name": name, "slug": slug})


@router.put("/api/workspaces/{workspace_id}/videos/{slug:path}")
async def api_assign_workspace_video(workspace_id: str, slug: str) -> Response:
    project = helpers._safe(slug.strip("/"))
    if not project:
        return JSONResponse({"error": "bad project"}, status_code=400)
    target: str | None = None
    if workspace_id != library_mod.UNSORTED_WORKSPACE_ID:
        try:
            workspace = library_mod.get_workspace(workspace_id)
        except ValueError as e:
            return JSONResponse({"error": public_error(e, 400)}, status_code=400)
        if workspace is None:
            return JSONResponse({"error": "unknown workspace"}, status_code=404)
        target = workspace_id
    doc, on_disk = helpers._read_project_doc(project)
    if doc is None:
        return JSONResponse({"error": "unknown project"}, status_code=404)
    try:
        proj = project_mod.from_dict(doc)
    except (KeyError, TypeError, ValueError) as e:
        return JSONResponse({"error": public_error(e, 400)}, status_code=400)
    proj.workspaceId = target
    return helpers._commit_project(project, proj, on_disk)




def _project_duration_s(doc: dict) -> float | None:
    """The video's length (edits.timeline_end_f, the player's rule) from the already-loaded document,
    with no ffprobe per listing."""
    try:
        proj = project_mod.from_dict(doc)
        end_f = edits_mod.timeline_end_f(proj)
        return round(end_f / float(proj.fps), 3) if end_f > 0 else None
    except (AttributeError, KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def _speech_media_stem(doc: dict) -> str | None:
    """The primary speech track's source filename stem (Drawer.tsx's `speechMedia` mirrored server
    side): what `/media/thumb/<stem>` resolves via `_source`, whatever origin the media came from
    (a raw upload or a workspace library asset linked into DATA_DIR)."""
    for track in doc.get("tracks") or []:
        if not isinstance(track, dict) or track.get("kind") != "speech":
            continue
        clips = track.get("clips")
        if not isinstance(clips, list) or not clips:
            continue
        clip = clips[0]
        media = clip.get("media") if isinstance(clip, dict) else None
        source = media.get("source") if isinstance(media, dict) else None
        if isinstance(source, str) and source:
            return Path(source).stem
    return None
