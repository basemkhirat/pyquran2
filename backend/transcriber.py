import logging
from abc import ABC, abstractmethod

import numpy as np

from backend.config import config

logger = logging.getLogger(__name__)

# ── Backend interface ──────────────────────────────────────────────

class TranscriberBackend(ABC):
    @abstractmethod
    def transcribe(self, audio: np.ndarray, initial_prompt: str = "") -> str: ...


# ── whisper.cpp backend (pywhispercpp) ─────────────────────────────

class WhisperCppBackend(TranscriberBackend):
    def __init__(self):
        from pywhispercpp.model import Model
        logger.info("Loading whisper.cpp model from %s", config.whisper_model_path)
        self._model = Model(config.whisper_model_path, n_threads=4)

    def transcribe(self, audio: np.ndarray, initial_prompt: str = "") -> str:
        segments = self._model.transcribe(
            audio,
            language="ar",
            initial_prompt=initial_prompt,
        )
        return " ".join(seg.text.strip() for seg in segments if seg.text.strip())


# ── MLX backend (mlx-whisper) ─────────────────────────────────────

class MlxBackend(TranscriberBackend):
    def __init__(self):
        import mlx_whisper  # noqa: F401 – validate availability
        self._model_path = config.mlx_model_path
        logger.info("Using MLX whisper model at %s", self._model_path)

    def transcribe(self, audio: np.ndarray, initial_prompt: str = "") -> str:
        import mlx_whisper
        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self._model_path,
            language="ar",
            initial_prompt=initial_prompt,
            temperature=0.0,
            no_speech_threshold=0.6,
            compression_ratio_threshold=2.4,
            word_timestamps=False,
        )
        return result.get("text", "").strip()


# ── Factory / singleton ───────────────────────────────────────────

_backend: TranscriberBackend | None = None

_BACKENDS = {
    "whisper_cpp": WhisperCppBackend,
    "mlx": MlxBackend,
}


def _get_backend() -> TranscriberBackend:
    global _backend
    if _backend is None:
        name = config.transcription_backend
        cls = _BACKENDS.get(name)
        if cls is None:
            raise ValueError(
                f"Unknown transcription backend '{name}'. "
                f"Choose from: {', '.join(_BACKENDS)}"
            )
        logger.info("Initializing transcription backend: %s", name)
        _backend = cls()
    return _backend


def transcribe(audio: np.ndarray, initial_prompt: str = "") -> str:
    """Transcribe a 16kHz float32 mono audio buffer to text.

    Args:
        audio: numpy float32 array of audio samples at 16kHz.
        initial_prompt: context prompt (e.g. current verse text) to improve accuracy.

    Returns:
        Transcribed text string.
    """
    return _get_backend().transcribe(audio, initial_prompt)
