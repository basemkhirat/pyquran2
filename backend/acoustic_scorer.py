"""Optional acoustic score via wav2vec2 Quran ASR. Use when config.enable_acoustic_score is True.

Optimizations over baseline:
- Audio normalization to [-1, 1] before inference
- Greedy CTC decoding (argmax over the model logits)
- Best-match word alignment instead of positional alignment
- Diacritics-aware comparison (the Quran wav2vec2 model outputs tashkeel): the decoded
  text is scored as a weighted blend of base-letter accuracy (WEIGHT_CHAR) and scored-
  diacritic accuracy (WEIGHT_DIACRITIC), mirroring the Whisper text scorer.
"""
import logging
import re
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from backend.config import config, normalize_sukoon
from backend.scorer import compute_char_score, compute_diacritic_score, compute_text_score, strip_diacritics
from backend.terminal_arabic import display_arabic

logger = logging.getLogger(__name__)

# Spelling-only marks folded/stripped before comparison so they never affect the score (none are
# scored diacritics, so only the character comparison changes). See _normalize_text.
_ALEF_WASLA = "\u0671"  # \u0671 -> folded to plain alef \u0627
_ALEF = "\u0627"
# Stripped so they don't affect the char score: U+0657 inverted damma, U+0640 tatweel,
# U+0670 dagger alef, and the U+06D6-U+06ED Quranic annotation block (waqf/pause marks such
# as U+06DA, plus end-of-ayah, sajdah and small-letter pronunciation guides). U+06E1 (alt
# sukoon) is converted to standard sukoon by normalize_sukoon first, so it is not stripped.
_STRIP_FOR_COMPARE = re.compile("[\u0657\u0640\u0670\u06D6-\u06ED]")

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


def load_model():
    """Load the wav2vec2 model (call at server startup to avoid first-request latency)."""
    _get_model()


def _normalize_audio(audio: np.ndarray) -> np.ndarray:
    """Normalize audio to [-1, 1] range."""
    peak = np.max(np.abs(audio))
    if peak > 1e-7:
        audio = audio / peak
    return audio


def _decode_audio(audio: np.ndarray) -> str:
    """Run wav2vec2 on 16kHz float32 mono audio and return greedily-decoded text."""
    model, processor = _get_model()
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

    # Greedy CTC decoding: argmax over the vocab, then collapse repeats + drop blanks
    predicted_ids = torch.argmax(logits, dim=-1)
    text = processor.batch_decode(predicted_ids)[0].strip()
    logger.info("  wav2vec2 decoded: '%s'", display_arabic(text))
    return text


# The Quranic vocative يا is written fused to the next word in the reference
# (emlaey "يَاأَيُّهَا", uthmani "يَٰٓأَيُّهَا"), but wav2vec2 decodes it as two tokens
# ("يا" + "أَيُّهَا"). Re-fuse a standalone "يا" onto the following token so it aligns to the
# single reference word instead of being dropped by the one-token best-match in get_acoustic_scores.
_VOCATIVE_YA = "يا"  # يا — base letters, diacritics stripped


def _merge_vocative(parts: List[str]) -> List[str]:
    """Fuse a standalone vocative 'يا' token onto the decoded token that follows it."""
    merged: List[str] = []
    i = 0
    while i < len(parts):
        if i + 1 < len(parts) and strip_diacritics(parts[i]).strip() == _VOCATIVE_YA:
            merged.append(parts[i] + parts[i + 1])
            i += 2
        else:
            merged.append(parts[i])
            i += 1
    return merged


def _normalize_text(text: str) -> str:
    """Normalize text for comparison so spelling-only marks don't affect the char score.

    Replaces U+06E1 (ۡ) with U+0652 (ْ) so both sukoon shapes compare equal.
    Folds alef-wasla to plain alef, and strips tatweel (kashida), the superscript "dagger"
    alef, and the Quranic annotation / waqf marks (U+06D6-U+06ED, e.g. the small high jeem
    U+06DA). None of these are scored diacritics, so this only affects the character comparison.
    """
    text = normalize_sukoon(text).replace(_ALEF_WASLA, _ALEF)
    return _STRIP_FOR_COMPARE.sub("", text).strip()


def _acoustic_components(expected: str, decoded: str) -> Tuple[float, float]:
    """Return (char_score, diacritic_score) in [0, 1] for decoded text vs expected.

    Applies the acoustic-specific normalization (sukoon variant, alef-wasla folding,
    tatweel/dagger-alef and waqf-mark stripping), then reuses the text scorer's char/diacritic
    accuracy so both scoring paths agree.
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


# Minimum blended similarity for a decoded token to count as a real match during alignment.
# Below this, the expected word is treated as unmatched (best_word=""), which the caller's
# noise/silence guard uses to keep the reciter on the word instead of emitting a false miss.
_MATCH_FLOOR = 0.4


def _align_decoded_to_expected(
    decoded_parts: List[str],
    all_expected: List[Tuple[str, str]],
) -> Tuple[List[float], List[float], List[float], List[str]]:
    """Monotonically align decoded tokens to expected words (Needleman-Wunsch / weighted LCS).

    Returns four lists parallel to all_expected: (blended score, char score, diacritic score,
    best-matching decoded token). An expected word the alignment leaves unmatched -- or whose
    best aligned token scores below _MATCH_FLOOR -- gets score 0.0 and best_word "".

    Unlike a greedy forward pointer, this global alignment can't leapfrog to a distant duplicate
    word or stick on a low-scoring token, so words repeated within the reference (common in Quran
    text) no longer desync the rest of the sequence.
    """
    m = len(all_expected)
    n = len(decoded_parts)

    # Precompute blended similarity + (char, diac) components for every (expected word, token) pair.
    sim = [[0.0] * n for _ in range(m)]
    comp: List[List[Tuple[float, float]]] = [[(0.0, 0.0)] * n for _ in range(m)]
    for i, (emlaey, uthmani) in enumerate(all_expected):
        for j in range(n):
            blend, cs, ds = _acoustic_score_best(emlaey, uthmani, decoded_parts[j])
            sim[i][j] = blend
            comp[i][j] = (cs, ds)

    # dp[i][j] = best total (sim - floor) aligning the first i expected words with the first j
    # tokens. Using the floored gain means a match below _MATCH_FLOOR never beats skipping, so
    # low-similarity tokens are left unmatched. Backpointers record the chosen move per cell.
    dp = [[0.0] * (n + 1) for _ in range(m + 1)]
    bp = [["up"] * (n + 1) for _ in range(m + 1)]  # "match" | "up" (word skipped) | "left" (token skipped)
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            up_score = dp[i - 1][j]                                    # expected word i-1 unmatched
            left_score = dp[i][j - 1]                                  # decoded token j-1 skipped
            match_score = dp[i - 1][j - 1] + (sim[i - 1][j - 1] - _MATCH_FLOOR)
            # Ties prefer a skip over a match, so a match is taken only when sim > _MATCH_FLOOR.
            best, move = up_score, "up"
            if left_score > best:
                best, move = left_score, "left"
            if match_score > best:
                best, move = match_score, "match"
            dp[i][j] = best
            bp[i][j] = move

    scores = [0.0] * m
    char_scores = [0.0] * m
    diac_scores = [0.0] * m
    best_words = [""] * m

    i, j = m, n
    while i > 0 and j > 0:
        move = bp[i][j]
        if move == "match":
            cs, ds = comp[i - 1][j - 1]
            scores[i - 1] = sim[i - 1][j - 1]
            char_scores[i - 1] = cs
            diac_scores[i - 1] = ds
            best_words[i - 1] = decoded_parts[j - 1]
            i -= 1
            j -= 1
        elif move == "up":
            i -= 1
        else:  # "left"
            j -= 1

    return scores, char_scores, diac_scores, best_words


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
    decoded_parts = _merge_vocative(decoded_text.split())
    n_decoded = len(decoded_parts)

    if not decoded_parts:
        n = len(expected_words)
        return AcousticResult([0.5] * n, [0.5] * n, [0.5] * n, [""] * n, 0)

    all_expected = previous_words + expected_words
    scores, char_scores, diac_scores, best_words = _align_decoded_to_expected(
        decoded_parts, all_expected
    )

    for (_, uthmani), score, cs, ds, best_word in zip(
        all_expected, scores, char_scores, diac_scores, best_words
    ):
        logger.debug(
            "  acoustic: expected='%s' best_match='%s' score=%.2f (char=%.2f diac=%.2f)",
            display_arabic(uthmani), display_arabic(best_word), score, cs, ds,
        )

    k = len(previous_words)
    return AcousticResult(
        scores[k:], char_scores[k:], diac_scores[k:], best_words[k:], n_decoded
    )
