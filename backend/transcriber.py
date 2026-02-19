import logging
import re
from abc import ABC, abstractmethod

import numpy as np

from backend.config import config

logger = logging.getLogger(__name__)

# Minimum audio duration (seconds) for Whisper to work reliably
_MIN_WHISPER_DURATION = 1.0

# Keep only Arabic characters, diacritics, and spaces
_NON_ARABIC_RE = re.compile(r'[^\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF\s]')


def _preprocess_audio(audio: np.ndarray) -> np.ndarray:
    """Normalize volume and pad short audio to minimum duration."""
    # Normalize to [-1, 1] range
    peak = np.max(np.abs(audio))
    if peak > 1e-7:
        audio = audio / peak

    # Pad with silence if too short for Whisper
    min_samples = int(config.audio_sample_rate * _MIN_WHISPER_DURATION)
    if len(audio) < min_samples:
        audio = np.pad(audio, (0, min_samples - len(audio)))

    return audio


def _clean_arabic(text: str) -> str:
    """Remove non-Arabic characters and collapse whitespace."""
    text = _NON_ARABIC_RE.sub('', text)
    return re.sub(r'\s+', ' ', text).strip()

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
        audio = _preprocess_audio(audio)
        segments = self._model.transcribe(
            audio,
            language="ar",
            initial_prompt=initial_prompt,
            beam_size=5,
        )
        text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
        return _clean_arabic(text)


# ── MLX backend (mlx-whisper) ─────────────────────────────────────

class MlxBackend(TranscriberBackend):
    def __init__(self):
        import mlx_whisper  # noqa: F401 – validate availability
        self._model_path = config.mlx_model_path
        logger.info("Using MLX whisper model at %s", self._model_path)

    def transcribe(self, audio: np.ndarray, initial_prompt: str = "") -> str:
        import mlx_whisper
        audio = _preprocess_audio(audio)
        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self._model_path,
            language="ar",
            initial_prompt=initial_prompt,
            temperature=0.0,
            no_speech_threshold=0.6,
            compression_ratio_threshold=2.4,
            word_timestamps=False,
            condition_on_previous_text=False,
        )
        return _clean_arabic(result.get("text", "").strip())


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
