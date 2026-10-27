"""Independent single-scene export contracts, also checked by the TypeScript preview."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline import project as project_mod, scene_render

FIXTURE = Path(__file__).parent / "fixtures" / "scene_specs_parity_features.json"


def test_independent_feature_specs_and_requests_match_the_preview_contract() -> None:
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    shots = {}
    modes = {}
    for case in fx["cases"]:
        project = case["project"]
        assert len(project["scenes"]) == 1
        project_mod.validate(project_mod.from_dict(project))
        specs = scene_render.scene_specs(project, fx["words"])
        assert specs == case["expected"] and len(specs) == 1
        spec = specs[0]
        request = scene_render.render_request(spec, width=project["width"], height=project["height"],
                                              fps=project["fps"], cast=fx["cast"])
        assert case["requests"] == [{"sceneId": spec["sceneId"], "shot": request["shot"],
                                    "custom": request.get("custom")}]
        shots[case["name"]] = request["shot"]
        modes[case["name"]] = spec["mode"]
    assert set(shots) == {"member-labels", "tree-labels", "cameo-question", "list-partial-labels",
                          "chain-labels", "radial-question", "grid-diagram", "chain-diagram", "radial-diagram", "tree-diagram", "list-diagram"}
    assert {s.get("diagram") for s in shots.values()} >= {"grid", "chain", "radial", "tree", "list"}
    assert shots["chain-labels"]["boardLabels"] == ["INDEX", "Files", "Pages", "Notes"]
    assert shots["tree-labels"]["boardLabels"] == ["INDEX", "Files", "Pages", "Notes"]
    assert shots["list-partial-labels"]["boardLabels"] == ["INDEX", "", "Pages"]
    assert shots["tree-labels"]["costar"]["bubble"] == "exclaims"
    assert shots["radial-question"]["costar"]["bubble"] == "question"
    assert modes["cameo-question"] == "cameo" and shots["cameo-question"]["bubble"] == "question"
    assert shots["member-labels"]["headlineAt"] == "costar"
    assert [m["pose"] for m in shots["member-labels"]["ensemble"]] == ["wave", "point", "think"]
    assert [m["character"] for m in shots["member-labels"]["ensemble"]] == ["lens"] * 3
    assert shots["member-labels"]["badges"] == [{"text": "HI", "tone": "accent"}, {"text": "7", "tone": "ink"},
                                             {"text": "OAK", "tone": "paper"}]

    for kind in ("grid", "chain", "radial", "tree", "list"):
        shot = shots[f"{kind}-diagram"]
        assert shot["diagram"] == kind
        assert shot["boardLabels"] == ["First", "", "Third", "Fourth", "Next", "Last"]
