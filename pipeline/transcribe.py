"""Transcribe stage: ffmpeg audio extract -> WhisperX large-v3 word-level transcript.

Runs locally when the optional local-AI capability is installed. Writes outputs/transcripts/<slug>.words.json
(a serialized contracts.TranscriptResult) plus .srt and .txt sidecars, and returns a
contracts.StageResult. torch/whisperx are imported lazily so this module also imports on
the standalone core (no GPU deps) for linting and for the ffmpeg-only audio step.
"""
from __future__ import annotations

import dataclasses
import errno
import hashlib
import importlib
import json
import os
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import httpx

import config
from pipeline import contracts, models, silero_runtime
from pipeline.runtime_tools import tool_path
from pipeline.errors import ValidationError

STAGE = "transcribe"
SAMPLE_RATE = 16000  # WhisperX alignment requires 16 kHz mono PCM
ERROR_MESSAGES = {
    "TRANSCRIPTION_DEPENDENCY_FAILED": "Transcription dependencies could not load. Restart Studio and retry.",
    "SPEECH_MODEL_DISK_FULL": "Speech models ran out of disk space. Free space and retry transcription.",
    "SPEECH_MODEL_DOWNLOAD_FAILED": "Speech model download or file access failed. Check the connection and disk space, then retry transcription.",
}


def _log_ffmpeg(cmd: list[str]) -> None:
    with open(config.RUN_LOG, "a", encoding="utf-8") as fh:
        fh.write(" ".join(cmd) + "\n")


def extract_audio(video: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        tool_path("ffmpeg"), "-y", "-i", str(video),
        "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le",
        str(dest),
    ]
    _log_ffmpeg(cmd)
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _verify_alignment(path: Path) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != models.ALIGNMENT.sha256:
        raise RuntimeError("English alignment checkpoint SHA-256 mismatch")


def _alignment_directory() -> Path:
    directory = config.STATE_HOME / "models" / "torchaudio"
    path = directory / models.ALIGNMENT.revision.split(":", 1)[1]
    tmp: Path | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            fd, filename = tempfile.mkstemp(prefix=".alignment-", dir=directory)
            tmp = Path(filename)
            with os.fdopen(fd, "wb") as stream:
                with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                    with client.stream("GET", models.ALIGNMENT.source) as response:
                        response.raise_for_status()
                        for chunk in response.iter_bytes():
                            stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            _verify_alignment(tmp)
            os.replace(tmp, path)
        _verify_alignment(path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    return directory


def _whisperx_words(audio, batch_size: int, language: str, device: str, compute: str):
    import torch  # type: ignore[import-not-found]

    threads = torch.get_num_threads()
    try:
        return _run_whisperx_words(audio, batch_size, language, device, compute, threads=threads)
    finally:
        torch.set_num_threads(threads)


def _run_whisperx_words(audio, batch_size: int, language: str, device: str, compute: str, *, threads: int):
    if config.WHISPER_MODEL != "large-v3" or language != "en":
        raise RuntimeError("No pinned model is configured for this transcription model and language")

    models.configure_caches()
    hub = importlib.import_module("huggingface_hub")
    model_path = hub.snapshot_download(
        repo_id=models.WHISPER_REPO, revision=models.WHISPER.revision, token=False,
        allow_patterns=["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"],
    )
    torchaudio = importlib.import_module("torchaudio")
    if torchaudio.__version__ not in models.TORCHAUDIO_BUILDS:
        raise RuntimeError("English alignment requires the pinned torchaudio version")
    alignment_directory = _alignment_directory()

    import whisperx  # type: ignore[import-not-found]

    model = whisperx.load_model(
        model_path, device.split(":")[0], device_index=int(device.split(":")[1]) if ":" in device else 0,
        compute_type=compute, language=language, local_files_only=True,
        vad_method="silero", vad_model=_silero_vad(), threads=threads,
    )
    import torch  # type: ignore[import-not-found]

    # CTranslate2 can also change the shared OpenMP pool during CPU inference.
    torch.set_num_threads(threads)
    base = model.transcribe(audio, batch_size=batch_size, language=language)
    torch.set_num_threads(threads)
    align_model, meta = whisperx.load_align_model(
        language_code=language, device=device, model_name=models.ALIGNMENT_BUNDLE,
        model_dir=str(alignment_directory),
    )
    aligned = whisperx.align(
        base["segments"], align_model, meta, audio, device,
        return_char_alignments=False,
    )
    return aligned["segments"]


def _silero_vad():
    # WhisperX's default constructor fetches repository code. Reuse its Silero
    # segmentation and merging with the model supplied by the pinned wheel.
    silero, model = silero_runtime.load()
    from whisperx.vads.silero import Silero  # type: ignore[import-not-found]

    class WheelSilero(Silero):
        def __init__(self):
            self.vad_onset = 0.5
            self.chunk_size = 30
            self.vad_pipeline = model
            self.get_speech_timestamps = lambda *args, **kwargs: silero.get_speech_timestamps(
                *args, **kwargs, min_speech_duration_ms=100,
            )

    return WheelSilero()


def _inference_settings() -> tuple[str, str, int]:
    from pipeline.runtime_tools import bundle_root

    bundled = bundle_root() is not None
    previous = os.environ.get("CUDA_DEVICE_ORDER")
    if bundled:
        os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    try:
        return _select_inference_settings(bundled=bundled)
    finally:
        if bundled:
            if previous is None:
                os.environ.pop("CUDA_DEVICE_ORDER", None)
            else:
                os.environ["CUDA_DEVICE_ORDER"] = previous


def _select_inference_settings(*, bundled: bool) -> tuple[str, str, int]:
    import torch  # type: ignore[import-not-found]  # optional inference dependency

    if os.environ.get("CUDA_VISIBLE_DEVICES") is not None or not torch.cuda.is_available():
        return "cpu", "int8", 4
    from pipeline import addons

    gpus = addons.detect_gpus()
    eligible = [gpu for gpu in gpus if gpu["supported"]]
    if gpus and not eligible:
        return "cpu", "int8", 4
    device = "cuda"
    if eligible and bundled:
        selected = max(eligible, key=lambda gpu: gpu["memoryMiB"])
        torch.cuda.set_device(selected["index"])
        device = f"cuda:{selected['index']}"
    free_bytes, _total_bytes = torch.cuda.mem_get_info()
    batch = next(batch for minimum, batch in config.WHISPER_BATCH_BY_FREE_VRAM if free_bytes >= minimum)
    return device, "float16", batch


def _free_gpu() -> None:
    import gc

    import torch  # type: ignore[import-not-found]

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _to_segments(raw_segments) -> list[contracts.TranscriptSegment]:
    segments: list[contracts.TranscriptSegment] = []
    for seg in raw_segments:
        if seg.get("start") is None or seg.get("end") is None:
            continue
        words = [
            contracts.WordToken(
                word=w["word"],
                start=float(w["start"]),
                end=float(w["end"]),
                score=float(w.get("score", 0.0)),
            )
            for w in seg.get("words", [])
            if w.get("start") is not None and w.get("end") is not None
        ]
        # Alignment drops timing for unalignable tokens (bare digits, symbols); a segment
        # with no timed words still needs one so it validates and stays cuttable downstream.
        if not words:
            words = [contracts.WordToken(
                seg["text"].strip(), float(seg["start"]), float(seg["end"]), 0.0,
            )]
        segments.append(contracts.TranscriptSegment(
            start=float(seg["start"]), end=float(seg["end"]),
            text=seg["text"].strip(), words=words,
        ))
    return segments


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_outputs(
    result: contracts.TranscriptResult, slug: str, *, output_dir: Path | None = None
) -> Path:
    directory = output_dir or config.TRANSCRIPTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{slug}.words.json"
    json_path.write_text(json.dumps(dataclasses.asdict(result), indent=2), encoding="utf-8")

    srt = []
    for i, seg in enumerate(result.segments, 1):
        srt += [str(i), f"{_ts(seg.start)} --> {_ts(seg.end)}", seg.text, ""]
    (directory / f"{slug}.srt").write_text("\n".join(srt), encoding="utf-8")

    (directory / f"{slug}.txt").write_text(
        "\n".join(seg.text for seg in result.segments) + "\n", encoding="utf-8",
    )
    return json_path


def _err(slug: str, reason: str, details: str | None = None, *, code: str | None = None) -> contracts.StageResult:
    return contracts.StageResult(status="error", stage=STAGE, clip_id=slug, reason=reason, details=details,
                                 metrics={"errorCode": code} if code else {})


def transcribe(
    video_path: str | Path, *, output_dir: Path | None = None
) -> contracts.StageResult:
    video = Path(video_path)
    slug = video.stem
    if not video.exists():
        return _err(slug, f"video not found: {video}")

    # A worker can provide a job-owned output directory so its input audio and
    # transcript sidecars never collide with another job that happens to use a
    # source object named ``data``.
    work_dir = output_dir or config.AUDIO_DIR
    audio_path = work_dir / f"{slug}.wav"
    try:
        extract_audio(video, audio_path)
    except subprocess.CalledProcessError as e:
        return _err(slug, "audio extract failed", e.stderr)

    import numpy as np

    with wave.open(str(audio_path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != SAMPLE_RATE:
            raise ValidationError("transcription audio must be 16 kHz mono 16-bit PCM")
        audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    duration = len(audio) / SAMPLE_RATE

    started = time.monotonic()
    try:
        device, compute, batch_size = _inference_settings()
    except ImportError as e:
        from pipeline import addons

        if addons.active("transcription") or addons.available("transcription"):
            code = "TRANSCRIPTION_DEPENDENCY_FAILED"
            return _err(slug, ERROR_MESSAGES[code], str(e), code=code)
        return _err(slug, addons.unavailable_message("transcription"), str(e), code="TRANSCRIPTION_NOT_INSTALLED")
    except OSError as e:
        return _err(slug, ERROR_MESSAGES["TRANSCRIPTION_DEPENDENCY_FAILED"], str(e), code="TRANSCRIPTION_DEPENDENCY_FAILED")
    except RuntimeError as e:
        return _err(slug, "whisperx failed", str(e))
    try:
        raw = _whisperx_words(audio, batch_size, config.WHISPER_LANGUAGE, device, compute)
    except ImportError as e:
        return _err(slug, ERROR_MESSAGES["TRANSCRIPTION_DEPENDENCY_FAILED"], str(e), code="TRANSCRIPTION_DEPENDENCY_FAILED")
    except (OSError, httpx.HTTPError) as e:
        return _model_error(slug, e)
    except RuntimeError as e:
        if not device.startswith("cuda") or "out of memory" not in str(e).lower():
            return _err(slug, "whisperx failed", str(e))
        _free_gpu()
        batch_size = max(1, batch_size // 2)
        try:
            raw = _whisperx_words(audio, batch_size, config.WHISPER_LANGUAGE, device, compute)
        except RuntimeError as e2:
            return _err(slug, f"whisperx OOM after retry at batch_size={batch_size}", str(e2))
        except (ImportError, OSError, httpx.HTTPError) as e2:
            return _model_error(slug, e2)
    finally:
        from pipeline import addons

        addons.invalidate_size("transcription")
    elapsed = time.monotonic() - started

    result = contracts.TranscriptResult(
        language=config.WHISPER_LANGUAGE, duration=duration, segments=_to_segments(raw),
    )
    try:
        result.validate()
    except ValidationError as e:
        return _err(slug, "schema validation failed", str(e))

    json_path = _write_outputs(result, slug, output_dir=output_dir)
    n_words = sum(len(s.words) for s in result.segments)
    return contracts.StageResult(
        status="ok", stage=STAGE, clip_id=slug, output_path=str(json_path),
        metrics={
            "duration_s": round(duration, 2),
            "segments": len(result.segments),
            "words": n_words,
            "device": device,
            "compute_type": compute,
            "batch_size": batch_size,
            "process_s": round(elapsed, 1),
            "rtf": round(elapsed / duration, 2) if duration else None,
        },
    )


def _model_error(slug: str, error: Exception) -> contracts.StageResult:
    if isinstance(error, OSError) and error.errno == errno.ENOSPC:
        code = "SPEECH_MODEL_DISK_FULL"
    elif isinstance(error, OSError) and getattr(error, "winerror", None) in {126, 127, 193}:
        code = "TRANSCRIPTION_DEPENDENCY_FAILED"
    else:
        code = "SPEECH_MODEL_DOWNLOAD_FAILED"
    return _err(slug, ERROR_MESSAGES[code], str(error), code=code)
