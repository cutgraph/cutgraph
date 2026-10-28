"""Regenerate shared fixtures using the Python implementation; TS remains an independent check.

From the repository root: python tests/fixtures/generate.py
CI drift check: python tests/fixtures/generate.py --check (nonzero if files would change).
Inputs live in inputs/: scene_specs_parity*.json, caption_pages_parity.json,
cast_shot_parity.json, thumbnail_parity.json and timeline_length_parity.json.
script_video.json also generates script_video_golden.json. Outputs are deterministic UTF-8
JSON; unchanged files are never written, so a second run is a no-op, including mtimes.
The generated fixtures, including their synthetic prose and scripts, are covered by the project licence.
words.json holds only that text and its timingRecipe; this generator is the sole source of the word
timestamps, and parity tests read them from the generated fixtures. Inputs that name other inputs
must resolve inside inputs/.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _named_input(inputs: Path, name: str) -> dict:
    # Resolving first refuses absolute names, traversal and links that leave inputs/.
    path = (inputs / name).resolve()
    if not path.is_relative_to(inputs.resolve()):
        raise ValueError(f"Fixture input must stay inside inputs/: {name}")
    return _read(path)


def _recipe_words(word_input: dict) -> list[dict]:
    if word_input.keys() != {"text", "timingRecipe"}:
        raise ValueError("Word input holds only text and its timingRecipe; the generator owns the timestamps")
    recipe = word_input["timingRecipe"]
    cursor = recipe["start"]
    words = []
    for i, token in enumerate(word_input["text"].split()):
        duration = recipe["duration"] + i % recipe["durationCycle"] * recipe["durationStep"]
        aligned = i != recipe["unalignedWord"]
        words.append({"w": token, "start": round(cursor, 3) if aligned else None,
                      "end": round(cursor + duration, 3) if aligned else None})
        cursor += duration + recipe["gap"] + recipe["pausesAfter"].get(str(i), 0)
    return words


def _screen_images(fixture: dict) -> None:
    import config
    from pipeline import scene_render

    with TemporaryDirectory(prefix="fixture-library-") as temp:
        workspaces = Path(temp).resolve() / "workspaces"
        for wid in dict.fromkeys(e["workspaceId"] for e in fixture["library"]):
            folder = workspaces / wid
            (folder / "library").mkdir(parents=True)
            (folder / "workspace.json").write_text(json.dumps({"id": wid, "name": wid}), encoding="utf-8")
            assets = [e["asset"] for e in fixture["library"] if e["workspaceId"] == wid]
            (folder / "library" / "assets.json").write_text(json.dumps({"assets": assets}), encoding="utf-8")
            for asset in assets:
                (folder / "library" / asset["file"]).write_bytes(asset["id"].encode())
        old = config.WORKSPACES_DIR
        config.WORKSPACES_DIR = workspaces
        try:
            fixture["images"], fixture["moreImages"] = {}, {}
            for spec in fixture["expected"]:
                request = scene_render.render_request(
                    spec, width=fixture["project"]["width"], height=fixture["project"]["height"],
                    fps=fixture["project"]["fps"], workspace_id=fixture["project"]["workspaceId"],
                )
                screen = request["shot"].get("screen")
                if screen is None:
                    continue

                def relative_image(phone: dict) -> str | None:
                    image = phone.get("image")
                    if image is None:
                        return None
                    path = Path(image).relative_to(workspaces)
                    return (Path(path.parts[0]) / path.name).as_posix()

                fixture["images"][spec["sceneId"]] = relative_image(screen)
                if screen.get("more"):
                    fixture["moreImages"][spec["sceneId"]] = [relative_image(p) for p in screen["more"]]
        finally:
            config.WORKSPACES_DIR = old


def generate(folder: Path) -> dict[str, dict | str]:
    from pipeline import caption_burn, caption_pages, cast, edits, scene_render, script_video, thumbnail
    from pipeline import project as project_mod, story_insert

    inputs = folder / "inputs"
    words = _recipe_words(_read(inputs / "words.json"))
    outputs: dict[str, dict | str] = {}
    for path in sorted(inputs.glob("*parity*.json")):
        fixture = _read(path)
        if path.name == "encoder_ladder_parity.json":
            # Encoder expectations are authored independently of both runtime implementations.
            outputs[path.name] = fixture
            continue
        if "castInput" in fixture:
            fixture["cast"] = _named_input(inputs, fixture.pop("castInput"))
        if path.name.startswith("scene_specs_parity"):
            fixture["words"] = words
            for case in fixture.get("cases", [fixture]):
                case["expected"] = scene_render.scene_specs(case["project"], words)
                if "cast" in fixture:
                    case["requests"] = []
                    for spec in case["expected"]:
                        request = scene_render.render_request(
                            spec, width=case["project"]["width"], height=case["project"]["height"],
                            fps=case["project"]["fps"], cast=fixture["cast"],
                        )
                        case["requests"].append({"sceneId": spec["sceneId"], "shot": request["shot"],
                                                 "custom": request.get("custom")})
                if "library" in fixture:
                    images = {**case, "library": fixture["library"]}
                    _screen_images(images)
                    case.update(images=images["images"], moreImages=images["moreImages"])
        elif path.name == "caption_pages_parity.json":
            fixture["words"] = words
            project = project_mod.from_dict(fixture["project"])
            for case in fixture["cases"]:
                width, height = case["width"], case["height"]
                case["layout"] = list(caption_pages.layout(width, height))
                case["budget"] = caption_pages.line_budget(width, height)
                case["pages"] = caption_pages.caption_pages(project, words, width, height)
                case["events"] = caption_pages.caption_events(case["pages"])
        elif path.name == "cast_shot_parity.json":
            rig_input = fixture.pop("rigInput", None)
            previous_rig = cast.RIGS.get("sample-rig")
            if rig_input:
                cast.RIGS["sample-rig"] = _named_input(inputs, rig_input)
            try:
                for case in fixture["cases"]:
                    shot, custom = cast.resolve_shot(case["shot"], fixture["cast"])
                    case["expected"] = {"shot": shot, "custom": custom}
            finally:
                if rig_input:
                    if previous_rig is None:
                        del cast.RIGS["sample-rig"]
                    else:
                        cast.RIGS["sample-rig"] = previous_rig
        elif path.name == "thumbnail_parity.json":
            for case in fixture["cases"]:
                case["request"] = thumbnail.render_request(case["project"], fixture["cast"], case["format"])
        elif path.name == "timeline_length_parity.json":
            frames = fixture.pop("wordFrames")
            cases = {case["name"]: case for case in fixture["cases"]}
            for case in fixture["cases"]:
                if "edit" in case:
                    edit = case.pop("edit")
                    original = project_mod.from_dict(cases[edit["from"]]["project"])
                    if edit["operation"] == "reorder":
                        changed, _ = edits.reorder_scenes(original, edit["sceneIds"], word_frames=frames)
                    elif edit["operation"] == "cut":
                        changed, _ = edits.ripple_cut(original, edit["startF"], edit["endF"], word_frames=frames)
                    elif edit["operation"] == "restore":
                        changed, _ = edits.ripple_restore(
                            original, edit["media"], edit["startF"], edit["endF"], word_frames=frames,
                        )
                    else:
                        raise ValueError(f"Unknown timeline edit: {edit['operation']}")
                    case["project"] = project_mod.to_dict(changed)
                project = project_mod.from_dict(case["project"])
                case["endF"] = edits.timeline_end_f(project)
                case["rawEndF"] = max(
                    [c.tlStartF + c.srcOutF - c.srcInF for t in project.tracks for c in t.clips]
                    + [i.tlStartF + i.durF for t in project.tracks for i in t.items], default=0,
                )
        else:
            raise ValueError(f"No Python generator for {path.name}")
        outputs[path.name] = fixture
    scripts = _read(inputs / "script_video.json")
    for case in scripts["cases"]:
        scenes = script_video.parse_script(case["script"])
        spans, duration = script_video.estimate(scenes)
        case.update(scenes=[asdict(s) for s in scenes], timings=[t for s in spans for t in s], duration=duration)
    outputs["script_video_golden.json"] = scripts
    outputs["project_v0.json"] = _read(inputs / "project_v0.json")
    outputs["project_v1_golden.json"] = _read(inputs / "project_v1_golden.json")
    insertion = _read(inputs / "story_insert_cases.json")
    for case in insertion["cases"]:
        entry = case["entry"]
        try:
            doc, cid, tl = story_insert.insert_asset(
                case["project"], insertion["words"], insertion["media"], case["wordIdx"],
                entry["workspaceId"], entry["asset"],
            )
        except ValueError as error:
            case["expected"] = {"error": str(error)}
            continue
        track = next(t for t in doc["tracks"] if any(c["id"] == cid for c in t.get("clips") or []))
        case["expected"] = {
            "id": cid, "tlStartF": tl, "trackIds": [t["id"] for t in doc["tracks"]], "track": track["id"],
            "clip": next(c for c in track["clips"] if c["id"] == cid),
            "tab": story_insert.asset_tab(entry["asset"]),
        }
    outputs["story_insert_cases.json"] = insertion
    for path in sorted((inputs / "stock").glob("*.json")):
        outputs[f"stock/{path.name}"] = _read(path)
    tokens = "The quick brown fox jumps over lazy".split()
    times = [0.0, 0.4, 0.8, 1.3, 1.7, 2.2, 2.5, 3.0]
    karaoke = [{"word": word, "start": start, "end": end}
               for word, start, end in zip(tokens, times, times[1:])]
    outputs["karaoke_default_golden.ass"] = caption_burn._ass_document(karaoke, 1080, 1920)
    missing = {p.name for p in folder.glob("*parity*.json")} - outputs.keys()
    if missing:
        raise ValueError(f"Parity fixtures without inputs: {sorted(missing)}")
    return outputs


def regenerate(folder: Path, *, check: bool) -> int:
    changed = []
    for name, fixture in generate(folder).items():
        path = folder / name
        content = (fixture if isinstance(fixture, str) else
                   json.dumps(fixture, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        if not path.exists() or path.read_bytes() != content:
            changed.append(name)
            if not check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
    print(("Would change: " if check else "Regenerated: ") + ", ".join(changed) if changed else "Fixtures are current")
    return int(check and bool(changed))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    # Imports may consult config; even standalone generation gets a disposable state home.
    with TemporaryDirectory(prefix="fixture-state-") as temp:
        os.environ["CUTGRAPH_STATE_HOME"] = temp
        return regenerate(Path(__file__).parent, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
