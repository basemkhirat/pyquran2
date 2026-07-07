"""Optional acoustic score via wav2vec2 Quran ASR. Use when config.enable_acoustic_score is True.

Optimizations over baseline:
- Audio normalization to [-1, 1] before inference
- Beam search decoding via pyctcdecode (with optional KenLM language model)
- Best-match word alignment instead of positional alignment
- Diacritics-aware comparison (the Quran wav2vec2 model outputs tashkeel): the decoded
  text is scored as a weighted blend of base-letter accuracy (WEIGHT_CHAR) and scored-
  diacritic accuracy (WEIGHT_DIACRITIC), mirroring the Whisper text scorer.
"""
import logging
import os
import re
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from backend.config import config, normalize_sukoon
from backend.scorer import compute_char_score, compute_diacritic_score, compute_text_score
from backend.terminal_arabic import display_arabic

logger = logging.getLogger(__name__)

# Characters that appear in uthmani_text but are NOT in the wav2vec2 model vocabulary.
# Must match the same regex used in scripts/generate_lm.py.
_CHARS_NOT_IN_VOCAB = re.compile("[\u0657\u06E1]")

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

    # Suppress noisy pyctcdecode alphabet INFO/WARNING messages (expected for Arabic models)
    logging.getLogger("pyctcdecode.alphabet").setLevel(logging.ERROR)

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


def _normalize_text(text: str) -> str:
    """Normalize text for comparison: normalize sukoon variant, strip out-of-vocab chars.

    Replaces U+06E1 (ۡ) with U+0652 (ْ) so both sukoon shapes compare equal.
    Removes the few characters that aren't in the model vocabulary.
    """
    return _CHARS_NOT_IN_VOCAB.sub("", normalize_sukoon(text)).strip()


def _acoustic_components(expected: str, decoded: str) -> Tuple[float, float]:
    """Return (char_score, diacritic_score) in [0, 1] for decoded text vs expected.

    Applies the acoustic-specific normalization (sukoon variant + out-of-vocab chars),
    then reuses the text scorer's char/diacritic accuracy so both scoring paths agree.
    """
    exp = _normalize_text(expected)
    dec = _normalize_text(decoded)
    return compute_char_score(exp, dec), compute_diacritic_score(exp, dec)


def _acoustic_score_single(expected: str, decoded: str) -> float:
    """Blended acoustic score in [0, 1]: WEIGHT_CHAR * char + WEIGHT_DIACRITIC * diacritic."""
    char_score, diacritic_score = _acoustic_components(expected, decoded)
    return compute_text_score(char_score, diacritic_score)


def _acoustic_score_best(
    emlaey: str, uthmani: str, decoded: str
) -> Tuple[float, float, float]:
    """Best (blended, char, diacritic) from comparing decoded against emlaey and uthmani.

    Picks the variant with the higher blended score so the returned sub-scores match it.
    """
    cs_e, ds_e = _acoustic_components(emlaey, decoded)
    cs_u, ds_u = _acoustic_components(uthmani, decoded)
    blend_e = compute_text_score(cs_e, ds_e)
    blend_u = compute_text_score(cs_u, ds_u)
    if blend_e >= blend_u:
        return blend_e, cs_e, ds_e
    return blend_u, cs_u, ds_u


@dataclass
class AcousticResult:
    """Per-word acoustic scores for a decoded chunk (all lists parallel to expected words)."""
    scores: List[float]        # blended: WEIGHT_CHAR * char + WEIGHT_DIACRITIC * diacritic
    char_scores: List[float]   # base-letter accuracy of the winning match
    diac_scores: List[float]   # scored-diacritic accuracy of the winning match
    best_words: List[str]      # raw decoded word that best-matched each expected word ("" if none)
    n_decoded: int             # number of words the model decoded from the audio


def get_acoustic_scores(
    audio: np.ndarray,
    previous_words: List[Tuple[str, str]],
    expected_words: List[Tuple[str, str]],
) -> AcousticResult:
    """Run wav2vec2 once on the chunk, decode, then best-match align to expected words.

    For each expected word (emlaey, uthmani), finds the decoded word with the best blended
    score against either variant. The blended score is WEIGHT_CHAR * char_accuracy +
    WEIGHT_DIACRITIC * diacritic_accuracy; char_scores/diac_scores hold the components of
    the winning match. Falls back to 0.5 (char=diac=0.5) when no decoded words.

    previous_words: list of (emlaey, uthmani) for already-confirmed words.
    expected_words: list of (emlaey, uthmani) for the current chunk.

    Returns an AcousticResult whose lists are sliced to the current chunk (previous_words
    dropped), parallel to expected_words.
    """
    if not expected_words:
        return AcousticResult([], [], [], [], 0)

    decoded_text = _decode_audio(audio)
    decoded_parts = decoded_text.split()
    n_decoded = len(decoded_parts)

    if not decoded_parts:
        n = len(expected_words)
        return AcousticResult([0.5] * n, [0.5] * n, [0.5] * n, [""] * n, 0)

    all_expected = previous_words + expected_words
    scores: List[float] = []
    char_scores: List[float] = []
    diac_scores: List[float] = []
    best_words: List[str] = []
    last_match_idx = 0

    for emlaey, uthmani in all_expected:
        best_score, best_cs, best_ds = 0.0, 0.0, 0.0
        best_word = ""
        best_word_idx = -1
        for j in range(last_match_idx, len(decoded_parts)):
            decoded_word = decoded_parts[j]
            blend, cs, ds = _acoustic_score_best(emlaey, uthmani, decoded_word)
            if blend > best_score:
                best_score, best_cs, best_ds = blend, cs, ds
                best_word = decoded_word
                best_word_idx = j

        scores.append(best_score)
        char_scores.append(best_cs)
        diac_scores.append(best_ds)
        best_words.append(best_word)
        if best_word_idx != -1 and best_score >= 0.4:
            last_match_idx = best_word_idx + 1

        logger.debug(
            "  acoustic: expected='%s' best_match='%s' score=%.2f (char=%.2f diac=%.2f)",
            display_arabic(uthmani), display_arabic(best_word), best_score, best_cs, best_ds,
        )

    k = len(previous_words)
    return AcousticResult(
        scores[k:], char_scores[k:], diac_scores[k:], best_words[k:], n_decoded
    )
