"""Pinned local inference assets and offline release-manifest data."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass

import config
from pipeline.runtime_tools import bundle_root

# Hugging Face reads this setting at import time, including through WhisperX.
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["PYANNOTE_METRICS_ENABLED"] = "false"


def configure_caches() -> None:
    """Confine portable downloads while preserving source cache defaults and overrides."""
    if bundle_root() is None:
        return
    root = config.STATE_HOME / "models"
    for key, directory in {
        "HF_HOME": "huggingface", "HF_HUB_CACHE": "huggingface/hub",
        "HF_ASSETS_CACHE": "huggingface/assets", "HF_XET_CACHE": "huggingface/xet",
        "HUGGINGFACE_HUB_CACHE": "huggingface/hub", "TRANSFORMERS_CACHE": "huggingface/hub",
        "TORCH_HOME": "torch", "XDG_CACHE_HOME": "cache", "NLTK_DATA": "nltk",
        "MPLCONFIGDIR": "matplotlib", "NUMBA_CACHE_DIR": "numba",
    }.items():
        os.environ[key] = str(root / directory)


@dataclass(frozen=True)
class Model:
    id: str
    purpose: str
    source: str
    revision: str
    license: str
    gated: bool
    approximate_size_bytes: int
    sha256: str | None = None


WHISPER = Model(
    id="faster-whisper-large-v3",
    purpose="Word-level transcription",
    source="https://huggingface.co/Systran/faster-whisper-large-v3",
    revision="edaa852ec7e145841d8ffdb056a99866b5f0a478",
    license="MIT",
    gated=False,
    approximate_size_bytes=3_100_000_000,
)
WHISPER_REPO = "Systran/faster-whisper-large-v3"
ALIGNMENT_BUNDLE = "WAV2VEC2_ASR_BASE_960H"
TORCHAUDIO_VERSION = "2.8.0"
TORCHAUDIO_BUILDS = (TORCHAUDIO_VERSION + "+cpu", TORCHAUDIO_VERSION + "+cu128")
ALIGNMENT = Model(
    id=ALIGNMENT_BUNDLE,
    purpose="English word alignment",
    source="https://download.pytorch.org/torchaudio/models/wav2vec2_fairseq_base_ls960_asr_ls960.pth",
    revision=f"torchaudio=={TORCHAUDIO_VERSION}:wav2vec2_fairseq_base_ls960_asr_ls960.pth",
    license="MIT",
    gated=False,
    approximate_size_bytes=380_000_000,
    sha256="488fd4f16de84438ffc945334278c1b9fb9b7159a806c1080b16111a958c945d",
)
SILERO = Model(
    id="silero-vad",
    purpose="Speech segmentation during transcription and silence proposals",
    source="https://pypi.org/project/silero-vad/6.2.1/",
    revision="silero-vad==6.2.1:data/silero_vad.jit",
    license="MIT",
    gated=False,
    approximate_size_bytes=2_300_000,
)


def license_links() -> tuple[tuple[str, str], ...]:
    return (
        (f"{WHISPER.id} ({WHISPER.license})", WHISPER.source + "/blob/" + WHISPER.revision + "/README.md"),
        (f"{ALIGNMENT.id} ({ALIGNMENT.license})", "https://github.com/facebookresearch/fairseq/blob/v0.12.2/LICENSE"),
        (f"{SILERO.id} ({SILERO.license})", "https://github.com/snakers4/silero-vad/blob/v6.2.1/LICENSE"),
    )


def manifest() -> dict[str, object]:
    """Return fresh JSON-compatible data without importing inference packages."""
    return {"models": [asdict(model) for model in (WHISPER, ALIGNMENT, SILERO)]}
