"""Wheel VAD interface and import side effects without model downloads."""
from __future__ import annotations

import builtins
import errno
import sys
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from pipeline import addons, silence, silero_runtime, transcribe
from studio import compute


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("silence_first", [False, True])
def test_wheel_vad_restores_threads_including_import_side_effects(monkeypatch, fail, silence_first, tmp_path):
    threads = [24]
    calls = []
    def timestamps(audio, *args, min_speech_duration_ms=None, **kwargs):
        if min_speech_duration_ms is None:
            return [{"start": 0, "end": 0.12}]
        calls.append(min_speech_duration_ms)
        return [(0.0, 0.12)]
    class Silero:
        get_speech_timestamps: Callable
        vad_pipeline: object
        def __init__(self):
            pytest.fail("the repository loader must not run")
        def __call__(self, audio):
            return self.get_speech_timestamps(audio, model=self.vad_pipeline)
        @staticmethod
        def preprocess_audio(audio):
            return audio
        @staticmethod
        def merge_chunks(segments, chunk_size, onset, offset):
            return {"segments": segments, "chunk": chunk_size}
    def load(**kwargs):
        assert kwargs == {"onnx": False}
        if fail:
            raise RuntimeError("model failed")
        return "wheel-model"
    original = builtins.__import__
    def importing(name, *args, **kwargs):
        if name == "silero_vad":
            threads[0] = 1
            return SimpleNamespace(load_silero_vad=load, get_speech_timestamps=timestamps, read_audio=lambda *args, **kwargs: [])
        if name == "whisperx.vads.silero":
            return SimpleNamespace(Silero=Silero)
        return original(name, *args, **kwargs)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        get_num_threads=lambda: threads[0], set_num_threads=lambda count: threads.__setitem__(0, count),
        hub=SimpleNamespace(load=lambda *args, **kwargs: pytest.fail("no repository downloads")),
    ))
    monkeypatch.setattr(builtins, "__import__", importing)
    monkeypatch.setattr(silero_runtime, "read_audio", lambda *args, **kwargs: [])
    if silence_first:
        if fail:
            with pytest.raises(RuntimeError, match="model failed"):
                silence._silero_speech(tmp_path / "sample.wav")
        else:
            assert silence._silero_speech(tmp_path / "sample.wav") == [(0.0, 0.12)]
        assert threads == [24]
    if fail:
        with pytest.raises(RuntimeError, match="model failed"):
            transcribe._silero_vad()
    else:
        vad = transcribe._silero_vad()
        audio = {"waveform": [0.0], "sample_rate": 16000}
        assert vad.preprocess_audio(audio) is audio
        segments = vad(audio)
        assert segments == [(0.0, 0.12)]
        assert vad.merge_chunks(segments, 30, 0.5, None) == {"segments": segments, "chunk": 30}
        assert calls == [100]
    assert threads == [24]


@pytest.mark.parametrize("error,code", [(OSError("offline"), "SPEECH_MODEL_DOWNLOAD_FAILED"),
    (OSError(errno.ENOSPC, "full"), "SPEECH_MODEL_DISK_FULL")])
def test_model_failures_are_retryable_without_reinstall_guidance(error, code):
    result = transcribe._model_error("clip", error)
    assert result.reason == transcribe.ERROR_MESSAGES[code]
    assert result.metrics["errorCode"] == code
    assert result.reason is not None
    job = {"id": "job", "kind": "transcribe", "status": "error", "code": code}
    assert compute._job_presentation(job)["error"] == result.reason
    assert "install" not in result.reason.lower()


def test_cuda_visibility_override_falls_back_without_creating_contexts(monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1,0")
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(
        is_available=lambda: pytest.fail("visibility override must not create contexts"))))
    assert transcribe._inference_settings() == ("cpu", "int8", 4)


def test_multiple_eligible_gpus_create_one_context(monkeypatch):
    from pipeline import runtime_tools

    monkeypatch.setattr(runtime_tools, "bundle_root", lambda: True)
    contexts = []
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setattr(addons, "detect_gpus", lambda: addons.parse_gpus(
        "NVIDIA GeForce RTX 3070, 610.00, 8188\nNVIDIA RTX A4000, 610.00, 16376", platform="win32"))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(
        is_available=lambda: True, set_device=contexts.append, mem_get_info=lambda: (8 * 1024**3, 16 * 1024**3))))
    assert transcribe._inference_settings() == ("cuda:1", "float16", 8)
    assert contexts == [1]


@pytest.mark.parametrize("channels,rate", [(1, 16000), (2, 16000), (1, 22050)])
def test_normalized_pcm_reads_without_optional_decoders(monkeypatch, tmp_path, channels, rate):
    import wave
    import numpy as np

    path = tmp_path / "sample.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(bytes.fromhex("008000000040") * channels)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(from_numpy=lambda audio: audio))
    if channels != 1 or rate != 16000:
        with pytest.raises(ValueError, match="normalized mono"):
            silero_runtime.read_audio(path, sampling_rate=16000)
    else:
        audio = silero_runtime.read_audio(path, sampling_rate=16000)
        assert np.allclose(audio, [-1.0, 0.0, 0.5])
        assert audio.dtype == np.float32
