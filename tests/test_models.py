"""Offline pins, model release evidence, and inference download guards."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from pipeline import models
from scripts import model_manifest, sbom


ROOT = Path(__file__).resolve().parents[1]


def test_manifest_has_complete_pinned_model_data() -> None:
    entries = models.manifest()["models"]
    assert isinstance(entries, list)
    assert {entry["id"] for entry in entries} == {
        "faster-whisper-large-v3", "WAV2VEC2_ASR_BASE_960H", "silero-vad",
    }
    for entry in entries:
        assert set(entry) == {"id", "purpose", "source", "revision", "license", "gated", "approximate_size_bytes", "sha256"}
        assert all(entry[key] for key in ("id", "purpose", "source", "revision", "license"))
        assert "PIN_REQUIRED" not in entry["revision"]
        assert entry["source"].startswith("https://")
        assert isinstance(entry["gated"], bool)
        assert entry["approximate_size_bytes"] > 0
        if entry["source"].startswith("https://huggingface.co/"):
            assert re.fullmatch(r"[0-9a-f]{40}", entry["revision"])
    assert models.WHISPER.source == f"https://huggingface.co/{models.WHISPER_REPO}"
    assert models.ALIGNMENT.sha256 == "488fd4f16de84438ffc945334278c1b9fb9b7159a806c1080b16111a958c945d"
    assert "whisperx-vad" not in json.dumps(models.manifest())


def test_wheel_model_pins_match_optional_inputs_and_existing_locks() -> None:
    for tier, build in (("cpu", "cpu"), ("nvidia", "cu128")):
        inputs = (ROOT / f"requirements-local-ai-{tier}.in").read_text(encoding="utf-8").splitlines()
        lock = (ROOT / f"requirements-local-ai-{tier}.txt").read_text(encoding="utf-8")
        for pin in (f"torchaudio=={models.TORCHAUDIO_VERSION}+{build}", models.SILERO.revision.split(":", 1)[0]):
            assert pin in inputs
            assert pin + " " in lock


def test_telemetry_disabled_before_inference_imports() -> None:
    env = dict(os.environ, HF_HUB_DISABLE_TELEMETRY="0")
    command = (
        "import os, sys; import pipeline.transcribe; "
        "assert os.environ['HF_HUB_DISABLE_TELEMETRY'] == '1'; "
        "assert not {'huggingface_hub', 'whisperx', 'torch', 'torchaudio'} & sys.modules.keys()"
    )
    subprocess.run([sys.executable, "-c", command], cwd=ROOT, env=env, check=True, capture_output=True, text=True)


def test_manifest_cli_is_offline_and_sbom_preserves_it(tmp_path: Path) -> None:
    path = tmp_path / "models" / "manifest.json"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/model_manifest.py"), str(path)],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    )
    original = path.read_bytes()
    assert json.loads(original) == models.manifest()
    assert model_manifest.main([str(path)]) == 0
    assert path.read_bytes() == original
    sbom.generate(ROOT, tmp_path / "sbom", model_manifest=path)
    assert (tmp_path / "sbom/model-manifest.json").read_bytes() == original


def test_ci_generates_model_manifest_before_sbom() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    generate = "scripts/model_manifest.py dist/sbom/model-manifest.json"
    consume = "scripts/sbom.py --output-dir dist/sbom --model-manifest dist/sbom/model-manifest.json"
    assert workflow.index(generate) < workflow.index(consume)
    assert "--no-model-manifest" not in workflow


def test_no_torch_hub_usage_in_product_sources() -> None:
    offenders = [
        path.relative_to(ROOT).as_posix()
        for directory in ("pipeline", "scripts", "studio")
        for path in (ROOT / directory).rglob("*.py")
        if re.search(r"\btorch\s*\.\s*hub\b|\bfrom\s+torch\s+import\s+hub\b|\bfrom\s+torch\.hub\b", path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
