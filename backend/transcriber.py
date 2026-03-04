import logging
import os
import re
from abc import ABC, abstractmethod

import numpy as np

from backend.config import config

logger = logging.getLogger(__name__)

# Project root (parent of backend/) for resolving relative model paths (e.g. on Modal)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_model_path(path: str) -> str:
    """Resolve relative local paths against project root so they work in any cwd (e.g. Modal)."""
    if path.startswith(".") or path.startswith("/"):
        return os.path.abspath(os.path.join(_PROJECT_ROOT, path))
    return path

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
    def transcribe(self, audio: np.ndarray) -> str: ...


# ── HuggingFace Transformers backend ──────────────────────────────

class HuggingFaceBackend(TranscriberBackend):
    def __init__(self):
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        self._model_path = _resolve_model_path(config.hf_model_path)
        logger.info("Loading HuggingFace Whisper model from %s", self._model_path)

        self._processor = WhisperProcessor.from_pretrained(self._model_path)
        self._model = WhisperForConditionalGeneration.from_pretrained(self._model_path)

        # Use GPU if available, otherwise CPU
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model.to(self._device)
        self._model.eval()

    def transcribe(self, audio: np.ndarray) -> str:
        import torch
        import zlib
        from transformers import GenerationConfig

        audio = _preprocess_audio(audio)

        proc = self._processor(
            audio,
            sampling_rate=config.audio_sample_rate,
            return_tensors="pt",
            return_attention_mask=True,
        )
        input_features = proc.input_features.to(self._device)
        attention_mask = proc.get("attention_mask")
        if attention_mask is not None:
            attention_mask = attention_mask.to(self._device)
        else:
            # Fallback: full mask so pad_token/eos warning is avoided
            attention_mask = torch.ones(
                1, input_features.shape[2], dtype=torch.long, device=self._device
            )

        # Single GenerationConfig: language/task (not deprecated forced_decoder_ids), only max_new_tokens (no max_length)
        cfg = {**self._model.generation_config.to_dict(), "max_new_tokens": 256, "max_length": None, "num_beams": 5, "language": "ar", "task": "transcribe"}
        generation_config = GenerationConfig.from_dict(cfg)
        gen_kwargs = {
            "generation_config": generation_config,
            "attention_mask": attention_mask,
        }

        with torch.no_grad():
            predicted_ids = self._model.generate(input_features, **gen_kwargs)

        text = self._processor.batch_decode(
            predicted_ids, skip_special_tokens=True
        )[0].strip()

        # Discard hallucinated/repetitive output (compression ratio check)
        # if text:
        #     compressed = len(zlib.compress(text.encode("utf-8")))
        #     raw = len(text.encode("utf-8"))
        #     ratio = raw / compressed if compressed > 0 else 0.0
        #     if ratio > 2.4:
        #         logger.warning(
        #             "Discarding hallucinated output (ratio=%.2f): '%s'",
        #             ratio, text,
        #         )
        #         return ""

        return _clean_arabic(text)


# ── Factory / singleton ───────────────────────────────────────────

_backend: TranscriberBackend | None = None

def _get_backend() -> TranscriberBackend:
    global _backend
    if _backend is None:
        logger.info("Initializing transcription backend: huggingface")
        _backend = HuggingFaceBackend()
    return _backend


def load_model():
    """Load Whisper/transcription backend (call at server startup to avoid first-request latency)."""
    _get_backend()


def transcribe(audio: np.ndarray) -> str:
    """Transcribe a 16kHz float32 mono audio buffer to text.

    Args:
        audio: numpy float32 array of audio samples at 16kHz.

    Returns:
        Transcribed text string.
    """
    return _get_backend().transcribe(audio)
