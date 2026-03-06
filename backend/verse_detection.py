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
from backend.terminal_arabic import display_arabic

logger = logging.getLogger(__name__)


def _build_verse_candidates(
    words: List[Dict[str, Any]],
    start_ayah: int,
    end_ayah: int,
    n_words: int,
) -> Dict[int, Tuple[str, int]]:
    """Build a mapping of ayah_number → (first N words joined, word_list_index).

    Parameters
    ----------
    words : list
        The flat word list for the session (from quran_data.get_words).
    start_ayah, end_ayah : int
        Inclusive verse range.
    n_words : int
        How many words from the start of each verse to use for matching.

    Returns
    -------
    dict
        {ayah_number: (candidate_text, first_word_index_in_words_list)}
    """
    candidates: Dict[int, Tuple[str, int]] = {}
    i = 0
    while i < len(words):
        w = words[i]
        ayah = w["ayah"]
        if ayah < start_ayah or ayah > end_ayah:
            i += 1
            continue
        if ayah in candidates:
            i += 1
            continue

        # Collect first n_words of this ayah
        verse_words = []
        first_index = i
        j = i
        while j < len(words) and words[j]["ayah"] == ayah and len(verse_words) < n_words:
            verse_words.append(words[j]["emlaey_text"])
            j += 1

        candidate_text = " ".join(verse_words)
        candidates[ayah] = (candidate_text, first_index)
        i = j if j > i else i + 1

    return candidates


def detect_start_verse(
    audio: np.ndarray,
    words: List[Dict[str, Any]],
    start_ayah: int,
    end_ayah: int,
) -> Optional[Tuple[int, int, float]]:
    """Detect which verse the user is reciting from their speech.

    Parameters
    ----------
    audio : np.ndarray
        16 kHz float32 mono audio of the user's first utterance.
    words : list
        The flat word list for the session.
    start_ayah, end_ayah : int
        Inclusive verse range to search within.

    Returns
    -------
    tuple or None
        ``(ayah_number, word_index, score)`` if a match is found above
        the configured threshold, otherwise ``None``.
        ``word_index`` is the index into the session ``words`` list where
        the matched verse begins.
    """
    n_words = config.verse_detection_word_count
    threshold = config.verse_detection_threshold

    candidates = _build_verse_candidates(words, start_ayah, end_ayah, n_words)
    if not candidates:
        logger.warning("No verse candidates found for ayah %d–%d", start_ayah, end_ayah)
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

    best_ayah: Optional[int] = None
    best_score: float = 0.0
    best_index: int = 0

    for ayah, (candidate_text, word_index) in candidates.items():
        candidate_norm = _normalize_text(candidate_text)
        if not candidate_norm:
            continue

        # Use window size = candidate word count so 1-word verses (e.g. "الم") match
        # when user says only that word; compare like-to-like.
        candidate_len = len(candidate_text.split())
        candidate_best = 0.0
        for start in range(0, len(decoded_words) - candidate_len + 1):
            window = decoded_words[start : start + candidate_len]
            decoded_slice_norm = _normalize_text(" ".join(window))
            if not decoded_slice_norm:
                continue
            error_rate = cer(candidate_norm, decoded_slice_norm)
            candidate_best = max(candidate_best, max(0.0, 1.0 - error_rate))
        score = candidate_best

        logger.debug(
            "  verse %d: candidate='%s' score=%.3f",
            ayah,
            display_arabic(candidate_text),
            score,
        )

        if score > best_score:
            best_score = score
            best_ayah = ayah
            best_index = word_index

    if best_ayah is not None and best_score >= threshold:
        logger.info(
            "Verse detection: matched ayah %d (score=%.3f, threshold=%.2f)",
            best_ayah,
            best_score,
            threshold,
        )
        return (best_ayah, best_index, best_score)

    logger.info(
        "Verse detection: no match above threshold (best=ayah %s score=%.3f, threshold=%.2f)",
        best_ayah,
        best_score,
        threshold,
    )
    return None
