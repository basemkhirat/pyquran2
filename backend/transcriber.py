import logging
import re
from abc import ABC, abstractmethod

import numpy as np

from backend.config import config

logger = logging.getLogger(__name__)

# Minimum audio duration (seconds) for Whisper to work reliably
_MIN_WHISPER_DURATION = 1.0

# Silence padding (seconds) added before and after audio for better Whisper
# onset/offset detection — prevents clipping first/last syllables.
_PAD_DURATION = 0.3

# Keep only Arabic characters, diacritics, and spaces
_NON_ARABIC_RE = re.compile(r'[^\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF\s]')


def _preprocess_audio(audio: np.ndarray) -> np.ndarray:
    """Normalize volume, add silence padding, and enforce minimum duration."""
    # Normalize to [-1, 1] range
    peak = np.max(np.abs(audio))
    if peak > 1e-7:
        audio = audio / peak

    # Add silence padding on both sides so Whisper can detect speech edges
    pad_samples = int(config.audio_sample_rate * _PAD_DURATION)
    audio = np.pad(audio, (pad_samples, pad_samples))

    # Pad with more silence if still too short for Whisper
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


# ── HuggingFace Transformers backend ──────────────────────────────

class HuggingFaceBackend(TranscriberBackend):
    def __init__(self):
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        self._model_path = config.hf_model_path
        logger.info("Loading HuggingFace Whisper model from %s", self._model_path)

        self._processor = WhisperProcessor.from_pretrained(self._model_path)
        self._model = WhisperForConditionalGeneration.from_pretrained(self._model_path)

        # Use GPU if available, otherwise CPU
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model.to(self._device)
        self._model.eval()

        # Pre-compute forced decoder IDs for Arabic transcription
        self._forced_decoder_ids = self._processor.get_decoder_prompt_ids(
            language="ar", task="transcribe"
        )

    def transcribe(self, audio: np.ndarray, initial_prompt: str = "") -> str:
        import torch
        import zlib

        audio = _preprocess_audio(audio)

        input_features = self._processor(
            audio,
            sampling_rate=config.audio_sample_rate,
            return_tensors="pt",
        ).input_features.to(self._device)

        # Use forced_decoder_ids for Arabic (avoids outdated generation_config.lang_to_id on fine-tuned models)
        gen_kwargs = {
            # "forced_decoder_ids": self._forced_decoder_ids,
            "num_beams": 5,
            "max_new_tokens": 256,
            "language": "ar",
        }

        # Use initial_prompt as decoder prefix for verse context guidance
        # if initial_prompt:
        #     prompt_ids = self._processor.get_prompt_ids(
        #         initial_prompt, return_tensors="pt"
        #     ).to(self._device)
        #     gen_kwargs["prompt_ids"] = prompt_ids

        with torch.no_grad():
            predicted_ids = self._model.generate(input_features, **gen_kwargs)

        text = self._processor.batch_decode(
            predicted_ids, skip_special_tokens=True
        )[0].strip()

        # Discard hallucinated/repetitive output (compression ratio check)
        if text:
            compressed = len(zlib.compress(text.encode("utf-8")))
            raw = len(text.encode("utf-8"))
            ratio = raw / compressed if compressed > 0 else 0.0
            if ratio > 2.4:
                logger.warning(
                    "Discarding hallucinated output (ratio=%.2f): '%s'",
                    ratio, text,
                )
                return ""

        return _clean_arabic(text)


# ── Factory / singleton ───────────────────────────────────────────

_backend: TranscriberBackend | None = None

_BACKENDS = {
    "whisper_cpp": WhisperCppBackend,
    "mlx": MlxBackend,
    "huggingface": HuggingFaceBackend,
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
