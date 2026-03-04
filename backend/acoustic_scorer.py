"""Optional acoustic score via wav2vec2 Quran ASR. Use when config.enable_acoustic_score is True.

Optimizations over baseline:
- Audio normalization to [-1, 1] before inference
- Beam search decoding via pyctcdecode (with optional KenLM language model)
- Best-match word alignment instead of positional alignment
"""
import logging
import os
from typing import List

import numpy as np
from jiwer import cer

from backend.config import config
from backend.scorer import strip_diacritics
from backend.terminal_arabic import display_arabic

logger = logging.getLogger(__name__)

_model = None
_processor = None
_decoder = None


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


def _get_decoder():
    """Build or return a cached beam search decoder (pyctcdecode)."""
    global _decoder
    if _decoder is not None:
        return _decoder

    from pyctcdecode import build_ctcdecoder

    _, processor = _get_model()
    vocab = processor.tokenizer.get_vocab()
    # Sort by index to get labels in order
    labels = [token for token, _ in sorted(vocab.items(), key=lambda x: x[1])]

    # Try loading KenLM ARPA language model (requires kenlm Python package)
    lm_path = config.wav2vec2_lm_path
    kenlm_model = None
    if lm_path and os.path.isfile(lm_path):
        try:
            import kenlm as _kenlm  # noqa: F401 – just check availability
            logger.info("Loading KenLM language model from %s", lm_path)
            kenlm_model = lm_path
        except ImportError:
            logger.warning(
                "kenlm Python package not installed — beam search will run without LM. "
                "Install with: pip install kenlm"
            )
    else:
        logger.warning(
            "No KenLM LM found at '%s' — using beam search without LM. "
            "Run 'python scripts/generate_lm.py' to create one.",
            lm_path,
        )

    _decoder = build_ctcdecoder(
        labels=labels,
        kenlm_model_path=kenlm_model,
        alpha=config.wav2vec2_lm_alpha,
        beta=config.wav2vec2_lm_beta,
    )
    logger.info(
        "Beam search decoder ready (LM=%s, alpha=%.2f, beta=%.2f, beam=%d)",
        "yes" if kenlm_model else "no",
        config.wav2vec2_lm_alpha,
        config.wav2vec2_lm_beta,
        config.wav2vec2_beam_width,
    )
    return _decoder


def load_model():
    """Load wav2vec2 model and decoder (call at server startup to avoid first-request latency)."""
    _get_model()
    _get_decoder()


def _normalize_audio(audio: np.ndarray) -> np.ndarray:
    """Normalize audio to [-1, 1] range."""
    peak = np.max(np.abs(audio))
    if peak > 1e-7:
        audio = audio / peak
    return audio


def _decode_audio(audio: np.ndarray) -> str:
    """Run wav2vec2 on 16kHz float32 mono audio and return decoded text."""
    model, processor = _get_model()
    decoder = _get_decoder()
    import torch

    # Normalize audio volume
    audio = _normalize_audio(audio)

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

    # Beam search decoding with optional LM (instead of greedy argmax)
    logits_np = logits.cpu().numpy()[0]
    text = decoder.decode(logits_np, beam_width=config.wav2vec2_beam_width)
    text = text.strip()
    logger.info("  wav2vec2 decoded: '%s'", display_arabic(text))
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
    """Run wav2vec2 once on the chunk, decode, then best-match align to expected words.

    For each expected word, finds the decoded word with the best CER score
    (instead of simple positional alignment which breaks when words are
    inserted/dropped by the model). Falls back to 0.5 when no decoded words.
    """
    if not expected_words:
        return []
    decoded_text = _decode_audio(audio)
    decoded_parts = decoded_text.split()

    if not decoded_parts:
        return [0.5] * len(expected_words)

    scores: List[float] = []
    for expected in expected_words:
        exp_stripped = strip_diacritics(expected).strip()
        best_score = 0.0
        best_word = ""
        for decoded_word in decoded_parts:
            dec_stripped = strip_diacritics(decoded_word).strip()
            if not exp_stripped:
                s = 1.0 if not dec_stripped else 0.0
            else:
                s = max(0.0, 1.0 - cer(exp_stripped, dec_stripped))
            if s > best_score:
                best_score = s
                best_word = decoded_word
        scores.append(best_score)
        logger.debug(
            "  acoustic: expected='%s' best_match='%s' score=%.2f",
            display_arabic(expected), display_arabic(best_word), best_score,
        )
    return scores
