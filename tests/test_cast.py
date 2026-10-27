"""The cast (pipeline/cast.py): character profiles stored as data per workspace,
their custom expressions validated against their rig's spec space, brand vs client scoping, default
characters, drafts of client rigs and their approval, the preservation of the pre-profile brand file, the
keyword describer and the routes."""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable
from pathlib import Path
from typing import TypeVar

import httpx
import pytest

import config
from pipeline import cast, library
from scripts import studio_app

T = TypeVar("T")
MOTION_CORE = Path(__file__).resolve().parents[1] / "motion-core" / "src"
SMUG = {"eyes": "side", "mouth": "flat"}
DEFAULT_RIG = cast.DEFAULT_CHARACTER_ID


@pytest.fixture(autouse=True)
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Path:
    monkeypatch.setattr(config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    if "mirror_the_motion_core_registry" not in request.node.name:
        monkeypatch.setitem(cast.RIGS, "sample-rig", json.loads(
            (Path(__file__).parent / "fixtures" / "inputs" / "synthetic_rig.json").read_text(encoding="utf-8"),
        ))
    return tmp_path


def _client(name: str) -> str:
    return library.create_workspace(name)["id"]


def _image(ws: str, tmp_path: Path, name: str, data: bytes) -> str:
    src = tmp_path / name
    src.write_bytes(data)
    return library.import_asset(ws, src, display_name=name)[0]["id"]


def _ids(workspace: str | None) -> list[str]:
    return [c["id"] for c in cast.video_cast(workspace)["characters"]]


# ── the rig mirror ──────────────────────────────────────────────────────────────────────────────────

def _union(name: str) -> tuple[str, ...]:
    match = re.search(rf"export type {name} =([^;]+);", (MOTION_CORE / "types.ts").read_text(encoding="utf-8"))
    assert match, f"{name} not found in types.ts"
    return tuple(re.findall(r"'([^']+)'", match.group(1)))


def _sources() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(MOTION_CORE.glob("*.ts")))


def _list(src: str, const: str) -> tuple[str, ...]:
    """An exported `readonly T[]` constant's items, from whichever motion-core module declares it."""
    options = re.search(rf"export const {const}: readonly \w+\[\] = \[([^\]]*)\];", src)
    assert options, const
    return tuple(re.findall(r"'([^']+)'", options.group(1)))


def _record_keys(src: str, const: str) -> tuple[str, ...]:
    body = re.search(rf"export const {const}\b[^=]*=\s*\{{(.*?)\n\}};", src, re.S)
    assert body, const
    return tuple(re.findall(r"^\s*(\w+):", body.group(1), re.M))


def _registry() -> dict[str, dict]:
    """Each registered rig as motion-core declares it: the CHARACTERS entries in characters.ts, each a
    `Character` constant in its own module, with its sets resolved to their items."""
    src = _sources()
    registry = re.search(r"export const CHARACTERS[^=]*=\s*\{([^}]*)\}", src)
    assert registry, "CHARACTERS not found in characters.ts"
    rigs = {}
    for key, const in re.findall(r"(\w+):\s*(\w+)", registry.group(1)):
        found = re.search(rf"export const {const}: Character = \{{(.*?)\n\}};", src, re.S)
        assert found, f"{const} not found in motion-core/src"
        body = found.group(1)

        def field(pattern: str, body: str = body) -> str:
            match = re.search(pattern, body)
            assert match, pattern
            return match.group(1)

        rigs[key] = {
            "id": field(r"id: '([^']+)'"),
            "owner": field(r"owner: '([^']+)'"),
            "palettes": tuple(re.findall(r"'([^']+)'", field(r"palettes: \[([^\]]*)\]"))),
            "parts": {part: _list(src, c) for part, c in re.findall(r"(\w+): (\w+)", field(r"parts: \{([^}]*)\}"))},
            "expressions": field(r"expressions: (\w+)"),
            "poses": _list(src, field(r"poses: (\w+)")),
            "moves": _list(src, field(r"moves: (\w+)")),
        }
    return rigs


def test_the_rigs_mirror_the_motion_core_registry() -> None:
    registry = _registry()
    assert set(cast.BUILTIN_RIGS) == set(registry) == {"lens"}
    assert cast.DEFAULT_RIG == "lens"
    src = _sources()
    for key, rig in registry.items():
        assert rig["id"] == key
        mirror = cast.BUILTIN_RIGS[key]
        assert (mirror["owner"], mirror["palettes"], mirror["parts"], mirror["poses"], mirror["moves"]) == (
            rig["owner"], rig["palettes"], rig["parts"], rig["poses"], rig["moves"]), key
        assert mirror["expressions"] == _record_keys(src, rig["expressions"]), key
    assert cast.BUILTIN_EXPRESSIONS == _union("ExpressionId")
    assert cast.POSES == _union("PoseId")
    assert cast.MOVES == _union("MoveId")


def test_sample_rig_profiles_and_custom_looks_resolve_without_becoming_default() -> None:
    profile = cast.create_character("brand", "Sample", "lens")
    assert (profile["rig"], profile["palette"], profile["approved"]) == ("lens", "periwinkle", True)
    assert cast.video_cast(None)["default"] == DEFAULT_RIG
    spec = {"eyes": "side", "mouth": "crooked"}
    assert cast.validate_spec(spec, "lens") == spec
    with pytest.raises(cast.CastError):
        cast.validate_spec({**spec, "mouth": "unknown"}, "lens")
    custom = cast.create_expression(profile["id"], "Quizzical", spec)
    video = cast.video_cast(None)
    resolved, parts = cast.resolve_shot({
        "mode": "animation", "character": profile["id"], "expression": custom["id"],
        "pose": "flex", "move": "sway", "bubble": "think",
        "costar": {"character": profile["id"], "expression": custom["id"]},
        "ensemble": [{"character": profile["id"], "expression": custom["id"]}],
    }, video)
    assert (resolved["character"], resolved["palette"], parts) == ("lens", "periwinkle", spec)
    assert resolved["costar"]["custom"] == spec
    assert resolved["ensemble"][0]["custom"] == spec
    assert cast.describe_rig(character=profile["id"]) == "lens"
    assert cast.describe("curious", "lens")["matched"] == ["curious"]


# ── profiles and scoping ────────────────────────────────────────────────────────────────────────────

def test_the_brand_always_casts_the_default_character(state: Path) -> None:
    assert cast.video_cast(None) == {
        "characters": [{"id": DEFAULT_RIG, "name": "Lens", "rig": "lens", "art": [], "palette": "periwinkle",
                        "approved": True, "expressions": [], "createdAt": "", "workspaceId": "brand", "available": True}],
        "default": DEFAULT_RIG, "workspace": None, "brand": {"name": "", "accent": config.BRAND_ACCENT},
    }
    assert not (state / "workspaces" / "brand" / "cast.json").exists(), "reading writes nothing"
    with pytest.raises(cast.CastError, match="built in"):
        cast.delete_character(DEFAULT_RIG)


def test_brand_characters_show_everywhere_and_client_ones_only_in_their_workspace() -> None:
    garden, other = _client("Garden"), _client("Northwind")
    guest = cast.create_character(garden, "Sample placeholder", "lens")
    assert guest["id"] == "sample-placeholder" and guest["workspaceId"] == garden
    buddy = cast.create_character("brand", "Buddy", "lens")
    assert buddy["workspaceId"] == "brand"

    assert _ids(garden) == [DEFAULT_RIG, "buddy", guest["id"]]
    garden_cast = cast.video_cast(garden)
    assert garden_cast["workspace"] == {"id": garden, "name": "Garden"}
    for workspace in (None, "brand", "unsorted", other, "no-such-workspace", "../escape"):
        assert _ids(workspace) == [DEFAULT_RIG, "buddy"], f"{workspace} never sees the client's character"
    stored = {ws: json.loads((config.WORKSPACES_DIR / ws / "cast.json").read_text(encoding="utf-8")) for ws in ("brand", garden)}
    assert [c["id"] for c in stored[garden]["characters"]] == [guest["id"]], "a client character lives in its workspace"
    assert [c["id"] for c in stored["brand"]["characters"]] == [DEFAULT_RIG, "buddy"]


def test_character_ids_are_unique_across_workspaces_and_names_where_they_show() -> None:
    garden, other = _client("Garden"), _client("Northwind")
    cast.create_character(garden, "Sample")
    twin = cast.create_character(other, "Sample")
    assert twin["id"] == "sample-2", "another client's Sample gets its own id"
    with pytest.raises(cast.CastConflict):
        cast.create_character(garden, "sample")
    with pytest.raises(cast.CastConflict):
        cast.create_character("brand", "SAMPLE")
    with pytest.raises(cast.CastConflict):
        cast.create_character(garden, "Lens")
    renamed = cast.update_character("sample-2", name="Reader")
    assert renamed["id"] == "sample-2" and renamed["name"] == "Reader"


@pytest.mark.parametrize("kwargs, message", [
    ({"workspace_id": "unsorted", "name": "X"}, "unknown workspace"),
    ({"workspace_id": "ghost", "name": "X"}, "unknown workspace"),
    ({"workspace_id": None, "name": "X"}, "unknown workspace"),
    ({"workspace_id": "brand", "name": "X", "rig": "dragon"}, "unknown rig"),
    ({"workspace_id": "brand", "name": "X", "rig": "sample-rig"}, "client's"),
    ({"workspace_id": "brand", "name": "X", "palette": "clay"}, "invalid palette"),
    ({"workspace_id": "brand", "name": "  "}, "empty"),
    ({"workspace_id": "brand", "name": "X", "art": ["0123456789abcdef"]}, "images"),
    ({"workspace_id": "brand", "name": "X", "art": "sheet.png"}, "list"),
])
def test_a_new_character_is_validated(kwargs: dict, message: str) -> None:
    with pytest.raises(cast.CastError, match=message):
        cast.create_character(**kwargs)


def test_approved_art_is_images_in_the_characters_own_library(tmp_path: Path) -> None:
    garden, other = _client("Garden"), _client("Northwind")
    sheet = _image(garden, tmp_path, "sheet.png", b"garden model sheet")
    alt = _image(garden, tmp_path, "alt.jpg", b"garden alt pose")
    elsewhere = _image(other, tmp_path, "theirs.png", b"someone else's art")
    audio = _image(garden, tmp_path, "grunt.wav", b"not art")
    guest = cast.create_character(garden, "Sample", art=[sheet, sheet])
    assert guest["art"] == [sheet]
    assert cast.update_character("sample", art=[sheet, alt])["art"] == [sheet, alt]
    for bad in ([elsewhere], [audio], ["../x"], [7]):
        with pytest.raises(cast.CastError, match="images"):
            cast.update_character("sample", art=bad)
    assert cast.video_cast(garden)["characters"][-1]["art"] == [sheet, alt]


def test_delete_a_character_and_its_expressions() -> None:
    garden = _client("Garden")
    cast.create_character(garden, "Sample")
    cast.create_expression("sample", "Grin", SMUG)
    assert cast.delete_character("sample") is True
    assert cast.delete_character("sample") is False
    assert _ids(garden) == [DEFAULT_RIG]
    with pytest.raises(FileNotFoundError):
        cast.update_character("sample", name="Back")
    with pytest.raises(FileNotFoundError):
        cast.create_expression("sample", "Grin", SMUG)


def test_the_default_character_is_the_workspaces_else_the_brands_else_the_default_character() -> None:
    garden, other = _client("Garden"), _client("Northwind")
    cast.create_character(garden, "Sample")
    cast.create_character("brand", "Buddy")
    assert cast.video_cast(garden)["default"] == DEFAULT_RIG
    assert cast.set_default("brand", "buddy")["default"] == "buddy"
    assert cast.video_cast(garden)["default"] == "buddy" and cast.video_cast(None)["default"] == "buddy"
    assert cast.set_default(garden, "sample")["default"] == "sample"
    assert cast.video_cast(other)["default"] == "buddy", "another client keeps the brand's default"
    with pytest.raises(cast.CastError):
        cast.set_default(other, "sample")
    with pytest.raises(cast.CastError):
        cast.set_default("brand", "sample")
    cast.delete_character("sample")
    assert cast.video_cast(garden)["default"] == "buddy", "a deleted default falls back"
    assert cast.set_default("brand", None)["default"] == DEFAULT_RIG
    assert cast.set_default(garden, DEFAULT_RIG)["default"] == DEFAULT_RIG


def test_a_client_rig_starts_as_a_draft_until_its_look_is_approved() -> None:
    garden = _client("Garden")
    with pytest.raises(cast.CastError, match="invalid palette"):
        cast.create_character(garden, "Yellow Sample", "sample-rig", palette="yellow")
    guest = cast.create_character(garden, "Notes Sample", "sample-rig", palette="fern")
    assert (guest["rig"], guest["palette"], guest["approved"]) == ("sample-rig", "fern", False)
    assert cast.create_character(garden, "Stand-in", "lens")["approved"] is True, "a brand rig is approved already"
    assert cast.create_character(garden, "Fog Sample", "sample-rig")["palette"] == "fog", "the rig's first palette"
    approved = cast.update_character("notes-sample", approved=True)
    assert approved["approved"] is True
    assert cast.video_cast(garden)["characters"][1]["approved"] is True, "stored"
    assert cast.update_character("notes-sample", palette="clay")["approved"] is True, "a palette keeps the approval"
    with pytest.raises(cast.CastError, match="true or false"):
        cast.update_character("notes-sample", approved="yes")


def test_a_profile_moves_to_another_rig_and_back() -> None:
    garden = _client("Garden")
    placeholder = cast.create_character(garden, "Sample placeholder", "lens")
    moved = cast.update_character(placeholder["id"], rig="sample-rig", palette="fern")
    assert (moved["rig"], moved["palette"], moved["approved"]) == ("sample-rig", "fern", False), "a new look is a draft"
    assert cast.update_character(placeholder["id"], rig="sample-rig")["palette"] == "fern", "the same rig changes nothing"
    back = cast.update_character(placeholder["id"], rig="lens")
    assert (back["rig"], back["palette"], back["approved"]) == ("lens", "periwinkle", True)
    cast.create_expression(placeholder["id"], "Grin", SMUG)
    with pytest.raises(cast.CastError, match="custom expressions"):
        cast.update_character(placeholder["id"], rig="sample-rig")
    with pytest.raises(cast.CastError, match="unknown rig"):
        cast.update_character(placeholder["id"], rig="dragon")
    with pytest.raises(cast.CastError, match="default rig"):
        cast.update_character(DEFAULT_RIG, rig="sample-rig")
    buddy = cast.create_character("brand", "Buddy", "lens")
    with pytest.raises(cast.CastError, match="client's"):
        cast.update_character(buddy["id"], rig="sample-rig")
    with pytest.raises(cast.CastError, match="invalid palette"):
        cast.update_character(placeholder["id"], palette="fern")


def test_synthetic_expressions_are_combinations_of_its_own_parts() -> None:
    garden = _client("Garden")
    cast.create_character(garden, "Sample", "sample-rig")
    look = {"brows": "raised", "eyes": "open", "mouth": "closed"}
    assert cast.create_expression("sample", "Quiet focus", look)["spec"] == look
    for spec in (SMUG, {**look, "mouth": "beak"}, {"brows": "raised", "eyes": "open"}):
        with pytest.raises(cast.CastError):
            cast.create_expression("sample", "Odd", spec)
    with pytest.raises(cast.CastConflict, match="built-in"):
        cast.create_expression("sample", "Happy", look)
    assert cast.create_expression("sample", "Sleepy", look)["id"] == "sleepy", "the default rig's built-ins are free names here"


# ── custom expressions ──────────────────────────────────────────────────────────────────────────────

def test_expressions_round_trip_through_the_brand_file(state: Path) -> None:
    entry = cast.create_expression(DEFAULT_RIG, "  Smug  one ", SMUG)
    assert entry["id"] == "smug-one" and entry["name"] == "Smug one" and entry["spec"] == SMUG and entry["createdAt"]
    path = state / "workspaces" / "brand" / "cast.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["default"] is None and [c["id"] for c in doc["characters"]] == [DEFAULT_RIG]
    assert doc["characters"][0]["expressions"] == [entry]
    assert (state / "workspaces" / "brand" / "workspace.json").is_file(), "the brand workspace exists"

    renamed = cast.update_expression(DEFAULT_RIG, "smug-one", name="Smirk", spec={**SMUG, "mouth": "flat"})
    assert renamed["id"] == "smug-one", "the id never changes, so scenes keep their expression"
    assert renamed["name"] == "Smirk" and renamed["spec"]["mouth"] == "flat"
    assert cast.delete_expression(DEFAULT_RIG, "smug-one") is True
    assert cast.delete_expression(DEFAULT_RIG, "smug-one") is False
    assert cast.video_cast(None)["characters"][0]["expressions"] == []


def test_each_character_has_its_own_expressions() -> None:
    garden = _client("Garden")
    cast.create_character(garden, "Sample")
    cast.create_expression(DEFAULT_RIG, "Smug", SMUG)
    grin = cast.create_expression("sample", "Smug", {**SMUG, "mouth": "grin"})
    assert grin["id"] == "smug", "the same name on another character is fine"
    characters = {c["id"]: c for c in cast.video_cast(garden)["characters"]}
    assert characters[DEFAULT_RIG]["expressions"][0]["spec"] == SMUG
    assert characters["sample"]["expressions"][0]["spec"]["mouth"] == "grin"


@pytest.mark.parametrize("spec", [
    None, "arc", {}, {"eyes": "arc", "character": "up"}, {**SMUG, "extra": "grin"},
    {**SMUG, "extra": True}, {**SMUG, "eyes": "wink"}, {**SMUG, "character": "UP"},
])
def test_a_spec_must_be_exactly_the_rig_parts_with_known_options(spec: object) -> None:
    with pytest.raises(cast.CastError):
        cast.create_expression(DEFAULT_RIG, "Odd", spec)


@pytest.mark.parametrize("name", [None, "", "   ", "!!!", "x" * 41, 7])
def test_a_name_must_be_a_short_text_with_a_letter_or_digit(name: object) -> None:
    with pytest.raises(cast.CastError):
        cast.create_expression(DEFAULT_RIG, name, SMUG)


def test_names_never_collide_with_built_ins_or_each_other() -> None:
    with pytest.raises(cast.CastConflict, match="built-in"):
        cast.create_expression(DEFAULT_RIG, "Happy", SMUG)
    with pytest.raises(cast.CastConflict, match="built-in"):
        cast.create_expression(DEFAULT_RIG, "HAPPY!", SMUG)
    cast.create_expression(DEFAULT_RIG, "Smug", SMUG)
    with pytest.raises(cast.CastConflict, match="already exists"):
        cast.create_expression(DEFAULT_RIG, "smug", SMUG)
    other = cast.create_expression(DEFAULT_RIG, "Bold", SMUG)
    with pytest.raises(cast.CastConflict):
        cast.update_expression(DEFAULT_RIG, other["id"], name="Smug")
    with pytest.raises(cast.CastConflict):
        cast.update_expression(DEFAULT_RIG, other["id"], name="Sleepy")


def test_a_renamed_expression_keeps_its_id_and_a_new_one_takes_a_suffix() -> None:
    cast.create_expression(DEFAULT_RIG, "Smug", SMUG)
    cast.update_expression(DEFAULT_RIG, "smug", name="Smirk")
    again = cast.create_expression(DEFAULT_RIG, "Smug", {**SMUG, "mouth": "grin"})
    assert again["id"] == "smug-2"
    assert [e["id"] for e in cast.video_cast(None)["characters"][0]["expressions"]] == ["smug", "smug-2"]


def test_a_described_expression_is_a_draft_until_approved(state: Path) -> None:
    draft = cast.create_expression(DEFAULT_RIG, "Bold", SMUG, draft=True)
    assert draft["draft"] is True
    assert cast.video_cast(None)["characters"][0]["expressions"] == [draft], "a draft survives the reload"
    renamed = cast.update_expression(DEFAULT_RIG, "bold", name="Proudest")
    assert renamed["draft"] is True, "editing a draft keeps it a draft"
    approved = cast.update_expression(DEFAULT_RIG, "bold", approved=True)
    assert "draft" not in approved
    assert "draft" not in cast.video_cast(None)["characters"][0]["expressions"][0]
    assert "draft" not in cast.create_expression(DEFAULT_RIG, "Smug", SMUG), "a composed one is the user's own"
    for bad in ({"draft": "yes"}, ):
        with pytest.raises(cast.CastError):
            cast.create_expression(DEFAULT_RIG, "Odd", SMUG, **bad)
    with pytest.raises(cast.CastError):
        cast.update_expression(DEFAULT_RIG, "smug", approved=False)


def test_update_of_an_unknown_expression_or_character_is_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        cast.update_expression(DEFAULT_RIG, "ghost", name="Boo")
    with pytest.raises(FileNotFoundError):
        cast.update_expression("ghost", "smug", name="Boo")
    assert cast.delete_expression("ghost", "smug") is False


# ── the file on disk ────────────────────────────────────────────────────────────────────────────────

def test_pre_profile_expressions_stay_opaque(state: Path) -> None:
    path = state / "workspaces" / "brand" / "cast.json"
    path.parent.mkdir(parents=True)
    legacy = [{"id": "smug", "name": "Smug", "spec": SMUG, "createdAt": "t"}]
    path.write_text(json.dumps({"expressions": legacy}), encoding="utf-8")
    character = cast.video_cast(None)["characters"][0]
    assert character["id"] == DEFAULT_RIG and character["expressions"] == []
    assert json.loads(path.read_text(encoding="utf-8")) == {"expressions": legacy}

    cast.create_expression(DEFAULT_RIG, "Bold", {**SMUG, "eyes": "arc"})
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["expressions"] == legacy
    assert [e["id"] for e in doc["characters"][0]["expressions"]] == ["bold"]

    garden = _client("Garden")
    (config.WORKSPACES_DIR / garden / "cast.json").write_text(json.dumps({"expressions": legacy}), encoding="utf-8")
    assert _ids(garden) == [DEFAULT_RIG]


def test_a_hand_edited_file_is_read_defensively(state: Path) -> None:
    path = state / "workspaces" / "brand" / "cast.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"default": 7, "characters": [
        {"id": "buddy", "name": "Buddy", "rig": "lens", "art": ["0123456789abcdef", "../x", 3], "expressions": [
            {"id": "ok", "name": "Ok", "spec": SMUG, "createdAt": "t"},
            {"id": "happy", "name": "Shadow", "spec": SMUG},
            {"id": "../x", "name": "Escape", "spec": SMUG},
            {"id": "bad", "name": "Bad", "spec": {**SMUG, "eyes": "laser"}},
            {"id": "ok", "name": "Dup", "spec": SMUG},
            "junk",
        ]},
        {"id": "buddy", "name": "Twin", "rig": "lens"},
        {"id": "drake", "name": "Drake", "rig": "dragon"},
        {"id": "sample", "name": "Sample", "rig": "sample-rig", "palette": "fern", "approved": "yes"},
        {"id": "Bad Id", "name": "X", "rig": "lens"},
        {"id": "pal", "name": "Pal", "rig": "lens", "palette": "clay"},
        "junk",
    ]}), encoding="utf-8")
    brand = cast.video_cast(None)
    assert [c["id"] for c in brand["characters"]] == [DEFAULT_RIG, "buddy", "drake", "sample"]
    assert (brand["characters"][3]["palette"], brand["characters"][3]["approved"]) == ("fern", False)
    buddy = brand["characters"][1]
    assert buddy["art"] == ["0123456789abcdef"] and buddy["palette"] == "periwinkle"
    assert [e["id"] for e in buddy["expressions"]] == ["ok"]
    assert brand["default"] == DEFAULT_RIG
    path.write_text("{not json", encoding="utf-8")
    assert _ids(None) == [DEFAULT_RIG]


# ── resolving a scene's shot ────────────────────────────────────────────────────────────────────────

def test_resolve_shot_casts_the_profiles_rig_and_its_custom_expressions() -> None:
    garden = _client("Garden")
    cast.create_character(garden, "Sample")
    cast.create_expression("sample", "Grin", SMUG)
    cast.create_expression(DEFAULT_RIG, "Smug", {**SMUG, "mouth": "flat"})
    video = cast.video_cast(garden)
    # Unconfigured workspaces use the config defaults.
    garden_brand, ours = {"eyebrow": "", "accent": config.BRAND_ACCENT}, {"eyebrow": "", "accent": config.BRAND_ACCENT}

    shot = {"mode": "cameo", "character": "sample", "expression": "grin", "pose": "wave"}
    assert cast.resolve_shot(shot, video) == ({**shot, "character": "lens", "palette": "periwinkle", **garden_brand}, SMUG)
    assert cast.resolve_shot({**shot, "expression": "smug"}, video) == (
        {"mode": "cameo", "character": "lens", "palette": "periwinkle", "pose": "wave", **garden_brand}, None), "another character's"
    assert cast.resolve_shot({"mode": "cameo", "expression": "smug"}, video)[1] == {**SMUG, "mouth": "flat"}, \
        "no character: the default (the Lens)"
    assert cast.resolve_shot({"mode": "cameo", "character": "ghost", "expression": "grin"}, video) == (
        {"mode": "cameo", "character": "lens", "palette": "periwinkle", **garden_brand}, None)
    cast.set_default(garden, "sample")
    assert cast.resolve_shot({"mode": "cameo", "expression": "grin"}, cast.video_cast(garden))[1] == SMUG
    assert cast.resolve_shot(shot, cast.video_cast(None)) == (
        {"mode": "cameo", "character": "lens", "palette": "periwinkle", "pose": "wave", **ours}, None), \
        "outside Garden the Sample is never cast"
    builtin = {"mode": "animation", "expression": "happy"}
    assert cast.resolve_shot(builtin, video) == ({**builtin, "character": "lens", "palette": "periwinkle", **garden_brand}, None)

    cast.create_character(garden, "Notes Sample", "sample-rig", palette="fern")
    guest = cast.video_cast(garden)
    flex = {"mode": "animation", "character": "notes-sample", "pose": "wave", "expression": "happy"}
    assert cast.resolve_shot(flex, guest) == ({**flex, "character": "sample-rig", "palette": "fern", **garden_brand}, None)
    assert cast.resolve_shot({**flex, "pose": "shrug", "expression": "idea"}, guest) == (
        {"mode": "animation", "character": "sample-rig", "palette": "fern", **garden_brand}, None), "the default rig's own ids fall back to the role look"
    assert cast.resolve_shot({"mode": "cameo", "pose": "not-a-pose"}, guest) == (
        {"mode": "cameo", "character": "lens", "palette": "periwinkle", **garden_brand}, None), "an unsupported pose is dropped"
    assert cast.resolve_shot(None, None) == ({"character": "lens", "palette": "periwinkle", **ours}, None)


def test_resolve_shot_carries_the_video_brand_unless_the_shot_names_its_own_eyebrow() -> None:
    garden = _client("Museum Visit")
    library.set_brand(garden, {"name": "MUSEUM VISIT", "accent": "#2BB3A3"})
    video = cast.video_cast(garden)
    assert video["brand"] == {"name": "MUSEUM VISIT", "accent": "#2bb3a3"}
    brand = {"eyebrow": "MUSEUM VISIT", "accent": "#2bb3a3"}
    assert cast.resolve_shot({"mode": "animation"}, video)[0] == {"mode": "animation", "character": "lens", "palette": "periwinkle", **brand}
    own = cast.resolve_shot({"mode": "animation", "eyebrow": "TEAM NOTES"}, video)[0]
    assert (own["eyebrow"], own["accent"]) == ("TEAM NOTES", "#2bb3a3"), "the shot's own words, the brand's colour"
    assert cast.resolve_shot({"mode": "animation", "eyebrow": ""}, video)[0]["eyebrow"] == "", "an empty eyebrow hides it"
    duo = cast.resolve_shot({"mode": "animation", "costar": {"character": DEFAULT_RIG}}, video)[0]
    assert duo["eyebrow"] == "MUSEUM VISIT" and "eyebrow" not in duo["costar"]
    library.set_brand(garden, {"name": "Team Notes"})
    assert cast.video_cast(garden)["brand"] == {"name": "Team Notes", "accent": "#2bb3a3"}
    for workspace in (None, "unsorted", "no-such-workspace", "../escape"):
        assert cast.video_cast(workspace)["brand"] == {"name": "", "accent": config.BRAND_ACCENT}, workspace


# ── describe ───────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, name, spec", [
    ("smug, one eyebrow up", "Smug", {"eyes": "side", "mouth": "flat"}),
    ("a bit sleepy", "Sleepy", {"eyes": "closed", "mouth": "small"}),
    ("Shocked!", "Shocked", {"eyes": "tall", "mouth": "round"}),
    ("proud of a clever idea", "Proud", {"eyes": "arc", "mouth": "grin"}),
    ("deep in thought, pondering", "Deep", {"eyes": "open", "mouth": "smile"}),
    ("zzz", "Zzz", {"eyes": "open", "mouth": "smile"}),
])
def test_the_keyword_describer_proposes_parts_and_a_name(text: str, name: str, spec: dict) -> None:
    proposal = cast.describe(text)
    assert (proposal["name"], proposal["spec"], proposal["source"]) == (name, spec, "keywords")


def test_describe_reports_what_it_matched_and_is_deterministic() -> None:
    first = cast.describe("smug, one eyebrow up")
    assert first["matched"] == ["eyebrow", "smug"]
    assert cast.describe("smug, one eyebrow up") == first
    assert cast.describe("unhappy")["spec"] == {"eyes": "open", "mouth": "smile"}, "whole words only"


def test_describe_validates_whatever_the_describer_proposes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cast, "DESCRIBER", lambda text, rig: {"name": "Laser", "spec": {**SMUG, "eyes": "laser"}})
    with pytest.raises(cast.CastError):
        cast.describe("laser eyes")
    with pytest.raises(cast.CastError):
        cast.describe("   ")


def test_describe_rejects_an_unknown_rig() -> None:
    with pytest.raises(cast.CastError):
        cast.describe("smug", "dragon")


def test_validate_spec_checks_a_spec_against_either_rigs_own_parts() -> None:
    assert cast.validate_spec(dict(SMUG)) == SMUG, "the default rig by default"
    guest_spec = {"brows": "raised", "eyes": "open", "mouth": "closed"}
    assert cast.validate_spec(dict(guest_spec), "sample-rig") == guest_spec
    with pytest.raises(cast.CastError):  # the default rig's parts aren't the Sample's
        cast.validate_spec(dict(SMUG), "sample-rig")
    with pytest.raises(cast.CastError):
        cast.validate_spec({**guest_spec, "mouth": "pout"}, "sample-rig")


# ── the keyword path is rig-aware ──────────────────────────────────────────────────────────────────

def test_the_keyword_describer_falls_back_to_a_rigs_own_built_ins_and_neutral() -> None:
    neutral = {"brows": "level", "eyes": "open", "mouth": "closed"}
    curious = cast.describe("feeling curious", "sample-rig")
    assert (curious["spec"], curious["matched"], curious["source"]) == (neutral, ["curious"], "keywords")
    unmatched = cast.describe("xyz nonsense", "sample-rig")
    assert (unmatched["spec"], unmatched["matched"]) == (neutral, []), "no built-in name in it: starts neutral"


def test_describe_rig_resolves_an_explicit_rig_or_a_characters_own() -> None:
    garden = _client("Garden")
    cast.create_character(garden, "Notes Sample", "sample-rig")
    assert cast.describe_rig() == "lens"
    assert cast.describe_rig(rig="sample-rig") == "sample-rig"
    assert cast.describe_rig(character="notes-sample") == "sample-rig"
    assert cast.describe_rig(character=DEFAULT_RIG) == "lens"
    with pytest.raises(cast.CastError):
        cast.describe_rig(rig="dragon")
    with pytest.raises(cast.CastError):
        cast.describe_rig(character="ghost")


# ── routes ─────────────────────────────────────────────────────────────────────────────────────────

def _run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


async def _call(method: str, url: str, **kw) -> httpx.Response:
    transport = httpx.ASGITransport(app=studio_app.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://studio.test") as client:
        return await client.request(method, url, **kw)


def _get(url: str) -> dict:
    return _run(_call("GET", url)).json()


def test_the_cast_routes_scope_characters_by_workspace_and_video(monkeypatch: pytest.MonkeyPatch, state: Path) -> None:
    garden = _client("Garden")
    created = _run(_call("POST", "/api/cast/characters", json={"workspace": garden, "name": "Sample", "rig": "lens"}))
    assert created.status_code == 201 and created.json()["workspaceId"] == garden
    assert [c["id"] for c in _get(f"/api/cast?workspace={garden}")["characters"]] == [DEFAULT_RIG, "sample"]
    assert [c["id"] for c in _get("/api/cast")["characters"]] == [DEFAULT_RIG]

    projects = state / "projects"
    projects.mkdir()
    monkeypatch.setattr(config, "PROJECTS_DIR", projects)
    (projects / "in-garden.json").write_text(json.dumps({"name": "in-garden", "workspaceId": garden}), encoding="utf-8")
    (projects / "loose.json").write_text(json.dumps({"name": "loose"}), encoding="utf-8")
    assert [c["id"] for c in _get("/api/cast?video=in-garden")["characters"]] == [DEFAULT_RIG, "sample"]
    assert _get("/api/cast?video=in-garden")["workspace"] == {"id": garden, "name": "Garden"}
    for video in ("loose", "missing", "..%2Fescape"):
        assert [c["id"] for c in _get(f"/api/cast?video={video}")["characters"]] == [DEFAULT_RIG], video


def test_the_character_routes_patch_default_and_delete() -> None:
    garden = _client("Garden")
    _run(_call("POST", "/api/cast/characters", json={"workspace": garden, "name": "Sample"}))
    patched = _run(_call("PATCH", "/api/cast/characters/sample", json={"name": "Notes Sample"}))
    assert patched.status_code == 200 and patched.json()["name"] == "Notes Sample"
    assert _run(_call("PATCH", "/api/cast/characters/ghost", json={"name": "X"})).status_code == 404
    moved = _run(_call("PATCH", "/api/cast/characters/sample", json={"rig": "sample-rig", "palette": "fern"}))
    assert moved.status_code == 200 and (moved.json()["rig"], moved.json()["approved"]) == ("sample-rig", False)
    approved = _run(_call("PATCH", "/api/cast/characters/sample", json={"approved": True}))
    assert approved.status_code == 200 and approved.json()["approved"] is True
    assert _run(_call("PATCH", "/api/cast/characters/sample", json={"approved": 1})).status_code == 400
    assert _run(_call("PATCH", "/api/cast/characters/sample", json={"rig": "dragon"})).status_code == 400
    made_default = _run(_call("PUT", "/api/cast/default", json={"workspace": garden, "character": "sample"}))
    assert made_default.status_code == 200 and made_default.json()["default"] == "sample"
    assert _run(_call("PUT", "/api/cast/default", json={"workspace": "brand", "character": "sample"})).status_code == 400
    assert _run(_call("DELETE", f"/api/cast/characters/{DEFAULT_RIG}")).status_code == 400
    assert _run(_call("DELETE", "/api/cast/characters/sample")).json() == {"ok": True}
    assert _run(_call("DELETE", "/api/cast/characters/sample")).status_code == 404
    assert _get(f"/api/cast?workspace={garden}")["default"] == DEFAULT_RIG


def test_character_usage_counts_the_animated_scenes_that_draw_it(monkeypatch: pytest.MonkeyPatch, state: Path) -> None:
    garden, other = _client("Garden"), _client("Northwind")
    cast.create_character(garden, "Notes Sample", "sample-rig", palette="fern")
    scene = lambda i, shot=None: {"id": f"s{i}", "title": "t", "role": "Hook", "startWord": i, **({"shot": shot} if shot else {})}  # noqa: E731
    docs = [
        {"workspaceId": garden, "scenes": [
            scene(0, {"mode": "animation", "character": "notes-sample"}),
            scene(1, {"mode": "animation", "costar": {"character": "notes-sample"}}),
            scene(2, {"mode": "cameo", "character": "notes-sample", "costar": {"character": "notes-sample"}}),
            scene(3, {"character": "notes-sample"}),
            scene(4),
        ]},
        {"workspaceId": other, "scenes": [scene(0, {"mode": "animation", "character": "notes-sample"})]},
        {"scenes": [scene(0, {"mode": "cameo"})]},
    ]
    assert cast.character_usage("notes-sample", docs) == {"scenes": 3, "videos": 1}, "footage and other projects don't count"
    assert cast.character_usage(DEFAULT_RIG, docs) == {"scenes": 3, "videos": 3}, "the default where none (or an unseen one) is named"
    cast.set_default(garden, "notes-sample")
    assert cast.character_usage("notes-sample", docs)["scenes"] == 3
    docs[0]["scenes"][4] = scene(4, {"mode": "cameo"})
    assert cast.character_usage("notes-sample", docs)["scenes"] == 4, "now the default in Garden"
    with pytest.raises(FileNotFoundError):
        cast.character_usage("ghost", docs)

    projects = state / "outputs" / "projects"
    projects.mkdir(parents=True)
    monkeypatch.setattr(config, "PROJECTS_DIR", projects)
    (projects / "in-garden.json").write_bytes(json.dumps({"name": "in-garden", **docs[0]}).encode())
    (projects / "broken.json").write_bytes(b"{not json")
    assert _get("/api/cast/characters/notes-sample/usage") == {"scenes": 4, "videos": 1}
    assert _run(_call("GET", "/api/cast/characters/ghost/usage")).status_code == 404


def test_the_character_routes_reject_bad_input() -> None:
    assert _run(_call("POST", "/api/cast/characters", json={"workspace": "ghost", "name": "X"})).status_code == 400
    assert _run(_call("POST", "/api/cast/characters", json={"workspace": "brand", "name": "X", "rig": "dragon"})).status_code == 400
    assert _run(_call("POST", "/api/cast/characters", content=b"{nope")).status_code == 400
    _run(_call("POST", "/api/cast/characters", json={"workspace": "brand", "name": "Buddy"}))
    assert _run(_call("POST", "/api/cast/characters", json={"workspace": "brand", "name": "buddy"})).status_code == 409


def test_the_expression_routes_default_to_the_default_character_and_take_a_character() -> None:
    created = _run(_call("POST", "/api/cast/expressions", json={"name": "Smug", "spec": SMUG}))
    assert created.status_code == 201 and created.json()["id"] == "smug"
    assert [e["id"] for e in _get("/api/cast")["characters"][0]["expressions"]] == ["smug"]
    patched = _run(_call("PATCH", "/api/cast/expressions/smug", json={"spec": {**SMUG, "mouth": "grin"}}))
    assert patched.status_code == 200 and patched.json()["spec"]["mouth"] == "grin" and patched.json()["name"] == "Smug"

    _run(_call("POST", "/api/cast/characters", json={"workspace": "brand", "name": "Buddy"}))
    grin = _run(_call("POST", "/api/cast/expressions", json={"character": "buddy", "name": "Grin", "spec": SMUG}))
    assert grin.status_code == 201
    assert _run(_call("PATCH", "/api/cast/expressions/grin?character=buddy", json={"name": "Beam"})).json()["name"] == "Beam"
    assert _run(_call("PATCH", "/api/cast/expressions/grin", json={"name": "X"})).status_code == 404, "not the default rig's"
    assert _run(_call("DELETE", "/api/cast/expressions/grin?character=buddy")).json() == {"ok": True}
    assert _run(_call("DELETE", "/api/cast/expressions/smug")).json() == {"ok": True}
    assert _run(_call("DELETE", "/api/cast/expressions/smug")).status_code == 404
    assert _run(_call("POST", "/api/cast/expressions", json={"character": "ghost", "name": "X", "spec": SMUG})).status_code == 404
    assert _run(_call("POST", "/api/cast/expressions", json={"character": 3, "name": "X", "spec": SMUG})).status_code == 400


def test_the_expression_routes_draft_and_approve() -> None:
    created = _run(_call("POST", "/api/cast/expressions", json={"name": "Bold", "spec": SMUG, "draft": True}))
    assert created.status_code == 201 and created.json()["draft"] is True
    assert _get("/api/cast")["characters"][0]["expressions"][0]["draft"] is True
    approved = _run(_call("PATCH", "/api/cast/expressions/bold", json={"approved": True}))
    assert approved.status_code == 200 and "draft" not in approved.json()
    assert "draft" not in _get("/api/cast")["characters"][0]["expressions"][0]
    assert _run(_call("PATCH", "/api/cast/expressions/bold", json={"approved": False})).status_code == 400
    assert _run(_call("POST", "/api/cast/expressions", json={"name": "Odd", "spec": SMUG, "draft": 1})).status_code == 400


def test_the_expression_routes_reject_bad_input() -> None:
    assert _run(_call("POST", "/api/cast/expressions", json={"name": "Odd", "spec": {**SMUG, "eyes": "x"}})).status_code == 400
    assert _run(_call("POST", "/api/cast/expressions", content=b"{nope")).status_code == 400
    assert _run(_call("POST", "/api/cast/expressions", json=["Smug"])).status_code == 400
    conflict = _run(_call("POST", "/api/cast/expressions", json={"name": "Curious", "spec": SMUG}))
    assert conflict.status_code == 409 and "built-in" in conflict.json()["error"]


def _no_copilot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force every /api/cast/describe call in a test onto the deterministic keyword path: no live SDK
    call, so the route test stays fast and offline regardless of whether this machine is logged in."""
    from scripts import agent_host

    async def unavailable(text: str, rig: str) -> dict:
        raise RuntimeError("no SDK in tests")

    monkeypatch.setattr(agent_host, "_describe_by_copilot", unavailable)


def test_the_describe_route_proposes_without_saving(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_copilot(monkeypatch)
    r = _run(_call("POST", "/api/cast/describe", json={"text": "smug, one eyebrow up"}))
    assert r.status_code == 200
    assert r.json() == {"name": "Smug", "spec": SMUG, "matched": ["eyebrow", "smug"], "source": "keywords"}
    assert _get("/api/cast")["characters"][0]["expressions"] == []
    assert _run(_call("POST", "/api/cast/describe", json={"text": ""})).status_code == 400


def test_the_describe_route_accepts_a_rig_or_a_characters_own(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_copilot(monkeypatch)
    garden = _client("Garden")
    _run(_call("POST", "/api/cast/characters", json={"workspace": garden, "name": "Notes Sample", "rig": "sample-rig"}))
    by_rig = _run(_call("POST", "/api/cast/describe", json={"text": "proud", "rig": "sample-rig"}))
    assert by_rig.status_code == 200 and by_rig.json()["spec"] == {"brows": "level", "eyes": "open", "mouth": "closed"}
    by_character = _run(_call("POST", "/api/cast/describe", json={"text": "proud", "character": "notes-sample"}))
    assert by_character.status_code == 200 and by_character.json() == by_rig.json()
    assert _run(_call("POST", "/api/cast/describe", json={"text": "x", "rig": "dragon"})).status_code == 400
    assert _run(_call("POST", "/api/cast/describe", json={"text": "x", "character": "ghost"})).status_code == 400


def test_the_describe_route_surfaces_the_copilots_source(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import agent_host

    async def fake_copilot(text: str, rig: str) -> dict:
        return {"name": "Smug", "spec": SMUG}

    monkeypatch.setattr(agent_host, "_describe_by_copilot", fake_copilot)
    r = _run(_call("POST", "/api/cast/describe", json={"text": "smug, one eyebrow up"}))
    assert r.status_code == 200
    assert r.json() == {"name": "Smug", "spec": SMUG, "matched": [], "source": "copilot"}
