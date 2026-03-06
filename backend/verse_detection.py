"""Detect which verse the user is reciting from their first utterance.

Uses wav2vec2 acoustic decoding to match the user's speech against the
first N words of each candidate verse in the selected range.  Returns the
best-matching verse if the score exceeds a configurable threshold.
"""
import logging
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
from jiwer import cer

from backend.config import config
from backend.acoustic_scorer import _decode_audio, _normalize_text
from backend.scorer import strip_diacritics
from backend.terminal_arabic import display_arabic


def _normalize_for_verse_match(text: str) -> str:
    """Normalize for start-verse comparison: strip tashkeel then out-of-vocab chars."""
    return _normalize_text(strip_diacritics(text))

logger = logging.getLogger(__name__)


def _build_verse_candidates(
    words: List[Dict[str, Any]],
    n_words: int,
) -> Dict[Tuple[int, int], Tuple[str, int]]:
    """Build a mapping of (surah, ayah) → (first N words joined, word_list_index).

    Parameters
    ----------
    words : list
        The flat word list for the session (from quran_data.get_words_range).
    n_words : int
        How many words from the start of each verse to use for matching.

    Returns
    -------
    dict
        {(surah, ayah): (candidate_text, first_word_index_in_words_list)}
    """
    candidates: Dict[Tuple[int, int], Tuple[str, int]] = {}
    i = 0
    while i < len(words):
        w = words[i]
        key = (w["surah"], w["ayah"])
        if key in candidates:
            i += 1
            continue

        # Collect first n_words of this verse
        verse_words = []
        first_index = i
        j = i
        while (
            j < len(words)
            and words[j]["surah"] == w["surah"]
            and words[j]["ayah"] == w["ayah"]
            and len(verse_words) < n_words
        ):
            verse_words.append(words[j]["emlaey_text"])
            j += 1

        candidate_text = " ".join(verse_words)
        candidates[key] = (candidate_text, first_index)
        i = j if j > i else i + 1

    return candidates


def detect_start_verse(
    audio: np.ndarray,
    words: List[Dict[str, Any]],
) -> Optional[Tuple[int, int, int, float]]:
    """Detect which verse the user is reciting from their speech.

    Parameters
    ----------
    audio : np.ndarray
        16 kHz float32 mono audio of the user's first utterance.
    words : list
        The flat word list for the session (may span multiple chapters).

    Returns
    -------
    tuple or None
        ``(chapter, ayah, word_index, score)`` if a match is found above
        the configured threshold, otherwise ``None``.
        ``word_index`` is the index into the session ``words`` list where
        the matched verse begins.
    """
    n_words = config.verse_detection_word_count
    threshold = config.verse_detection_threshold

    candidates = _build_verse_candidates(words, n_words)
    if not candidates:
        logger.warning("No verse candidates found in word list")
        return None

    # Decode audio once
    decoded_text = _decode_audio(audio)
    if not decoded_text.strip():
        logger.info("Verse detection: wav2vec2 decoded empty text")
        return None

    # Compare like-to-like: candidate is first n_words of each verse. Try every
    # contiguous n_words window in decoded so extra words before/after (e.g. "قل"
    # from noise) don't break the match.
    decoded_words = decoded_text.split()
    logger.info(
        "Verse detection: decoded '%s' (%d candidates)",
        display_arabic(decoded_text),
        len(candidates),
    )

    best_key: Optional[Tuple[int, int]] = None
    best_score: float = 0.0
    best_index: int = 0

    for (surah, ayah), (candidate_text, word_index) in candidates.items():
        candidate_norm = _normalize_for_verse_match(candidate_text)
        if not candidate_norm:
            continue

        # Use window size = candidate word count so 1-word verses (e.g. "الم") match
        # when user says only that word; compare like-to-like (base letters only, no tashkeel).
        candidate_len = len(candidate_text.split())
        candidate_best = 0.0
        for start in range(0, len(decoded_words) - candidate_len + 1):
            window = decoded_words[start : start + candidate_len]
            decoded_slice_norm = _normalize_for_verse_match(" ".join(window))
            if not decoded_slice_norm:
                continue
            error_rate = cer(candidate_norm, decoded_slice_norm)
            candidate_best = max(candidate_best, max(0.0, 1.0 - error_rate))
        score = candidate_best

        logger.debug(
            "  verse %d:%d: candidate='%s' score=%.3f",
            surah,
            ayah,
            display_arabic(candidate_text),
            score,
        )

        if score > best_score:
            best_score = score
            best_key = (surah, ayah)
            best_index = word_index

    if best_key is not None and best_score >= threshold:
        logger.info(
            "Verse detection: matched %d:%d (score=%.3f, threshold=%.2f)",
            best_key[0],
            best_key[1],
            best_score,
            threshold,
        )
        return (best_key[0], best_key[1], best_index, best_score)

    logger.info(
        "Verse detection: no match above threshold (best=%s score=%.3f, threshold=%.2f)",
        best_key,
        best_score,
        threshold,
    )
    return None
