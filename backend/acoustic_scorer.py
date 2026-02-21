"""Optional acoustic score via wav2vec2 Quran ASR. Use when config.enable_acoustic_score is True."""
import logging
from typing import List

import numpy as np
from jiwer import cer

from backend.config import config
from backend.scorer import strip_diacritics

logger = logging.getLogger(__name__)

_model = None
_processor = None


def _get_model():
    global _model, _processor
    if _model is None:
        import torch
        from transformers import AutoModelForCTC, AutoProcessor

        logger.info("Loading wav2vec2 Quran ASR from %s", config.wav2vec2_quran_asr_model)
        _processor = AutoProcessor.from_pretrained(config.wav2vec2_quran_asr_model)
        _model = AutoModelForCTC.from_pretrained(config.wav2vec2_quran_asr_model)
        _model.eval()
        _model.to("cuda" if torch.cuda.is_available() else "cpu")
    return _model, _processor


def _decode_audio(audio: np.ndarray) -> str:
    """Run wav2vec2 on 16kHz float32 mono audio and return decoded text."""
    model, processor = _get_model()
    import torch

    device = next(model.parameters()).device
    inputs = processor(
        audio,
        sampling_rate=config.audio_sample_rate,
        return_tensors="pt",
        padding="longest",
    )
    input_values = inputs.input_values.to(device)
    with torch.no_grad():
        logits = model(input_values).logits
    ids = torch.argmax(logits, dim=-1)[0]
    text = processor.decode(ids, skip_special_tokens=True).strip()
    return text


def _acoustic_score_single(expected: str, decoded: str) -> float:
    """Score in [0, 1] using 1 - CER on stripped diacritics."""
    exp = strip_diacritics(expected).strip()
    dec = strip_diacritics(decoded).strip()
    if not exp:
        return 1.0 if not dec else 0.0
    err = cer(exp, dec)
    return max(0.0, 1.0 - err)


def get_acoustic_scores(audio: np.ndarray, expected_words: List[str]) -> List[float]:
    """Run wav2vec2 once on the chunk, decode, split by spaces; return one score per expected word.

    Score for word i is 1 - CER(expected_words[i], decoded_word[i]) when decoded has enough words;
    otherwise fallback 0.5 for that word.
    """
    if not expected_words:
        return []
    decoded_text = _decode_audio(audio)
    decoded_parts = decoded_text.split()
    scores: List[float] = []
    for i, expected in enumerate(expected_words):
        if i < len(decoded_parts):
            scores.append(_acoustic_score_single(expected, decoded_parts[i]))
        else:
            scores.append(0.5)
    return scores
