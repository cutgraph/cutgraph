"""Load the wheel's VAD without changing the process's torch thread budget."""
from __future__ import annotations

import threading
from pathlib import Path
import wave

_lock = threading.RLock()


def load():
    import torch  # type: ignore[import-not-found]

    with _lock:
        threads = torch.get_num_threads()
        try:
            import silero_vad  # type: ignore[import-not-found]

            return silero_vad, silero_vad.load_silero_vad(onnx=False)
        finally:
            torch.set_num_threads(threads)


def read_audio(path: Path, *, sampling_rate: int):
    """Read Studio's normalized PCM without optional torchaudio decoder backends."""
    import numpy as np
    import torch  # type: ignore[import-not-found]

    with wave.open(str(path), "rb") as stream:
        if stream.getnchannels() != 1 or stream.getsampwidth() != 2 or stream.getframerate() != sampling_rate:
            raise ValueError("Silence detection requires normalized mono 16-bit PCM audio.")
        audio = np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    return torch.from_numpy(audio)
