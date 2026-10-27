from __future__ import annotations

import sys
import types
import hashlib
import wave
from dataclasses import replace
from pathlib import Path

import pytest
import httpx

from pipeline import models, transcribe


@pytest.fixture(autouse=True)
def recorded_hardware(monkeypatch):
    from pipeline import addons

    monkeypatch.setattr(addons, "detect_gpus", lambda: [])
    monkeypatch.setattr(transcribe, "_silero_vad", lambda: "wheel-vad")
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(get_num_threads=lambda: 12, set_num_threads=lambda _: None))


def extract_silent_audio(_source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(transcribe.SAMPLE_RATE)
        wav.writeframes(b"\0\0" * transcribe.SAMPLE_RATE)


@pytest.mark.parametrize("failure", [OSError("offline"), OSError(28, "full")])
def test_installed_transcription_model_failure_does_not_request_reinstallation(monkeypatch, tmp_path, failure):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"media")
    monkeypatch.setattr(transcribe, "extract_audio", extract_silent_audio)
    monkeypatch.setattr(transcribe, "_inference_settings", lambda: ("cpu", "int8", 4))
    def fail(*args):
        raise failure
    monkeypatch.setattr(transcribe, "_whisperx_words", fail)
    result = transcribe.transcribe(source, output_dir=tmp_path / "out")
    assert result.status == "error" and result.reason is not None
    assert result.metrics["errorCode"] in {"SPEECH_MODEL_DOWNLOAD_FAILED", "SPEECH_MODEL_DISK_FULL"}
    assert "install" not in result.reason.lower()


def test_installed_dependencies_that_cannot_import_request_restart(monkeypatch, tmp_path):
    from pipeline import addons

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"media")
    monkeypatch.setattr(transcribe, "extract_audio", extract_silent_audio)
    monkeypatch.setattr(addons, "active", lambda name: {"tier": "cpu"})
    def fail():
        raise ImportError("dependency failed")
    monkeypatch.setattr(transcribe, "_inference_settings", fail)
    result = transcribe.transcribe(source, output_dir=tmp_path / "out")
    assert result.metrics["errorCode"] == "TRANSCRIPTION_DEPENDENCY_FAILED"
    assert result.reason is not None and "Restart" in result.reason


def fake_torch(monkeypatch, available: bool, free_gib: float) -> None:
    def memory():
        assert available
        return int(free_gib * 1024**3), 24 * 1024**3

    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=types.SimpleNamespace(
        is_available=lambda: available, mem_get_info=memory, empty_cache=lambda: None,
    )))


@pytest.mark.parametrize("available,free_gib,expected", [
    (False, 0, ("cpu", "int8", 4)),
    (True, 0, ("cuda", "float16", 4)),
    (True, 6.99, ("cuda", "float16", 4)),
    (True, 7, ("cuda", "float16", 8)),
    (True, 13.99, ("cuda", "float16", 8)),
    (True, 14, ("cuda", "float16", 16)),
    (True, 24, ("cuda", "float16", 16)),
])
def test_settings_use_available_device_and_free_memory(monkeypatch, available, free_gib, expected) -> None:
    fake_torch(monkeypatch, available, free_gib)
    assert transcribe._inference_settings() == expected


@pytest.mark.parametrize("available,free_gib,failures,message,batches,ok", [
    (False, 0, 0, "", [4], True),
    (True, 14, 0, "", [16], True),
    (True, 14, 1, "CUDA out of memory", [16, 8], True),
    (True, 7, 1, "CUDA out of memory", [8, 4], True),
    (True, 0, 1, "CUDA out of memory", [4, 2], True),
    (True, 0, 1, "CUDA out of memory", [1, 1], True),
    (True, 14, 2, "CUDA out of memory", [16, 8], False),
    (True, 14, 1, "inference failed", [16], False),
    (False, 0, 1, "out of memory", [4], False),
])
def test_transcription_settings_metrics_and_bounded_retry(
    monkeypatch, tmp_path: Path, available, free_gib, failures, message, batches, ok,
) -> None:
    fake_torch(monkeypatch, available, free_gib)
    if batches[0] == 1:
        monkeypatch.setattr(transcribe, "_inference_settings", lambda: ("cuda", "float16", 1))
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"media")
    calls = []
    monkeypatch.setattr(transcribe, "extract_audio", extract_silent_audio)
    monkeypatch.setitem(sys.modules, "whisperx", types.SimpleNamespace(
        load_audio=lambda _path: [0.0] * transcribe.SAMPLE_RATE,
    ))

    def words(_audio, batch, _language, device, compute):
        calls.append((device, compute, batch))
        if len(calls) <= failures:
            raise RuntimeError(message)
        return [{"start": 0.0, "end": 1.0, "text": "hello", "words": [
            {"word": "hello", "start": 0.0, "end": 1.0, "score": 1.0},
        ]}]

    monkeypatch.setattr(transcribe, "_whisperx_words", words)
    result = transcribe.transcribe(source, output_dir=tmp_path / "output")
    assert [call[2] for call in calls] == batches
    assert result.status == ("ok" if ok else "error")
    if ok:
        assert result.metrics["device"] == ("cuda" if available else "cpu")
        assert result.metrics["compute_type"] == ("float16" if available else "int8")
        assert result.metrics["batch_size"] == batches[-1]


@pytest.mark.parametrize("device,compute", [("cpu", "int8"), ("cuda", "float16")])
def test_model_and_alignment_use_selected_device(monkeypatch, tmp_path: Path, device, compute) -> None:
    calls = []
    threads = [12]
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(
        get_num_threads=lambda: threads[0], set_num_threads=lambda value: threads.__setitem__(0, value)))
    snapshot = "pinned-speech-snapshot"

    def download(**kwargs):
        assert kwargs["repo_id"] == models.WHISPER_REPO
        assert kwargs["revision"] == models.WHISPER.revision
        assert kwargs["token"] is False
        assert "model.bin" in kwargs["allow_patterns"]
        return snapshot

    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=download))
    monkeypatch.setitem(sys.modules, "torchaudio", types.SimpleNamespace(__version__=models.TORCHAUDIO_BUILDS[0]))
    monkeypatch.setattr(transcribe.config, "STATE_HOME", tmp_path)
    checkpoint = b"synthetic alignment checkpoint"
    monkeypatch.setattr(models, "ALIGNMENT", replace(models.ALIGNMENT, sha256=hashlib.sha256(checkpoint).hexdigest()))
    directory = tmp_path / "models" / "torchaudio"
    directory.mkdir(parents=True)
    (directory / models.ALIGNMENT.revision.split(":", 1)[1]).write_bytes(checkpoint)

    def model(_name, selected, **kwargs):
        assert _name == snapshot
        assert kwargs["local_files_only"] is True
        assert kwargs["vad_method"] == "silero"
        assert kwargs["vad_model"] == "wheel-vad"
        assert kwargs["threads"] == 12
        calls.append((selected, kwargs["compute_type"]))
        threads[0] = 1
        def transcribe_audio(*args, **kwargs):
            assert threads[0] == 12
            threads[0] = 1
            return {"segments": []}
        return types.SimpleNamespace(transcribe=transcribe_audio)

    def align_model(**kwargs):
        assert threads[0] == 12
        assert kwargs["model_name"] == models.ALIGNMENT_BUNDLE
        assert kwargs["model_dir"] == str(directory)
        calls.append(kwargs["device"])
        return object(), {}

    def align(_segments, _model, _meta, _audio, selected, **kwargs):
        calls.append(selected)
        return {"segments": []}

    monkeypatch.setitem(sys.modules, "whisperx", types.SimpleNamespace(
        load_model=model, load_align_model=align_model, align=align,
    ))
    assert transcribe._whisperx_words([], 4, "en", device, compute) == []
    assert threads == [12]
    assert calls == [(device, compute), device, device]


@pytest.mark.parametrize("cached,download_matches", [(False, True), (True, True), (False, False)])
def test_alignment_checkpoint_download_and_integrity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cached: bool, download_matches: bool,
) -> None:
    checkpoint = b"synthetic alignment checkpoint"
    monkeypatch.setattr(transcribe.config, "STATE_HOME", tmp_path)
    monkeypatch.setattr(models, "ALIGNMENT", replace(models.ALIGNMENT, sha256=hashlib.sha256(checkpoint).hexdigest()))
    directory = tmp_path / "models" / "torchaudio"
    path = directory / models.ALIGNMENT.revision.split(":", 1)[1]
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, method, url):
            from contextlib import nullcontext

            calls.append((method, url))
            response = httpx.Response(
                200, content=checkpoint if download_matches else b"wrong checkpoint",
                request=httpx.Request(method, url),
            )
            return nullcontext(response)

    monkeypatch.setattr(transcribe.httpx, "Client", Client)
    loaded = []
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(
        snapshot_download=lambda **kwargs: "pinned-speech-snapshot",
    ))
    monkeypatch.setitem(sys.modules, "torchaudio", types.SimpleNamespace(__version__=models.TORCHAUDIO_BUILDS[0]))

    def load_model(*args, **kwargs):
        loaded.append("transcription")
        return types.SimpleNamespace(transcribe=lambda *args, **kwargs: {"segments": []})

    def align_model(**kwargs):
        loaded.append("alignment")
        assert kwargs["model_dir"] == str(directory)
        assert path.read_bytes() == checkpoint
        return object(), {}

    monkeypatch.setitem(sys.modules, "whisperx", types.SimpleNamespace(
        load_model=load_model, load_align_model=align_model,
        align=lambda *args, **kwargs: {"segments": []},
    ))
    if cached:
        directory.mkdir(parents=True)
        path.write_bytes(b"corrupt cached checkpoint")
    if cached or not download_matches:
        with pytest.raises(RuntimeError, match="checkpoint SHA-256 mismatch"):
            transcribe._whisperx_words([], 4, "en", "cpu", "int8")
        assert loaded == []
        if cached:
            assert path.read_bytes() == b"corrupt cached checkpoint"
        else:
            assert not path.exists()
        assert list(directory.iterdir()) == ([path] if cached else [])
    else:
        assert transcribe._whisperx_words([], 4, "en", "cpu", "int8") == []
        assert loaded == ["transcription", "alignment"]
        assert path.read_bytes() == checkpoint
        assert list(directory.iterdir()) == [path]
        path.write_bytes(b"corrupted after first load")
        with pytest.raises(RuntimeError, match="checkpoint SHA-256 mismatch"):
            transcribe._whisperx_words([], 4, "en", "cpu", "int8")
        assert loaded == ["transcription", "alignment"]
    assert calls == ([] if cached else [("GET", models.ALIGNMENT.source)])


@pytest.mark.parametrize("model,language", [("small", "en"), ("large-v3", "fr")])
def test_unpinned_model_or_language_is_rejected_before_download(monkeypatch, model, language) -> None:
    monkeypatch.setattr(transcribe.config, "WHISPER_MODEL", model)
    monkeypatch.setattr(transcribe.importlib, "import_module", lambda _name: pytest.fail("must not download"))
    with pytest.raises(RuntimeError, match="No pinned model"):
        transcribe._whisperx_words([], 4, language, "cpu", "int8")


def test_alignment_rejects_unpinned_torchaudio_before_loading(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(
        snapshot_download=lambda **_kwargs: "pinned-speech-snapshot",
    ))
    monkeypatch.setitem(sys.modules, "torchaudio", types.SimpleNamespace(__version__="0.0.0"))
    monkeypatch.setitem(sys.modules, "whisperx", types.SimpleNamespace(
        load_model=lambda *args, **kwargs: pytest.fail("must not load with an unpinned alignment bundle"),
    ))
    with pytest.raises(RuntimeError, match="pinned torchaudio"):
        transcribe._whisperx_words([], 4, "en", "cpu", "int8")


@pytest.mark.parametrize("message", ["CUDA initialization failed", "CUDA out of memory"])
def test_cuda_memory_probe_failure_returns_stage_error(monkeypatch, tmp_path: Path, message: str) -> None:
    def memory():
        raise RuntimeError(message)

    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=types.SimpleNamespace(
        is_available=lambda: True, mem_get_info=memory,
    )))
    monkeypatch.setitem(sys.modules, "whisperx", types.SimpleNamespace(
        load_audio=lambda _path: [0.0] * transcribe.SAMPLE_RATE,
    ))
    monkeypatch.setattr(transcribe, "extract_audio", extract_silent_audio)
    monkeypatch.setattr(transcribe, "_whisperx_words", lambda *args: pytest.fail("inference must not start"))
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"media")
    output = tmp_path / "output"

    result = transcribe.transcribe(source, output_dir=output)

    assert result.status == "error"
    assert result.stage == "transcribe"
    assert result.clip_id == "clip"
    assert result.reason == "whisperx failed"
    assert result.details == message
    assert result.output_path is None
    assert sorted(path.name for path in output.iterdir()) == ["clip.wav"]
