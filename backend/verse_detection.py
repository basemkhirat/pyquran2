"""Detect which verse the user is reciting, and keep their position anchored.

Initial anchoring (`detect_start_verse`) aligns the *whole* first utterance
against the selected range's word sequence, so verses that only differ after a
shared prefix are told apart, and genuinely identical verses (e.g. Al-Rahman's
refrain, repeated 31x) are reported as *ambiguous* until the following distinct
verse resolves them.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
from jiwer import cer

from backend.config import config
from backend.acoustic_scorer import _decode_audio, _normalize_text
from backend.scorer import strip_diacritics
from backend.terminal_arabic import display_arabic


logger = logging.getLogger(__name__)


def _normalize_for_verse_match(text: str) -> str:
    """Normalize for verse comparison: strip tashkeel then out-of-vocab chars."""
    return _normalize_text(strip_diacritics(text))


@dataclass
class DetectionResult:
    """Outcome of an initial verse-detection attempt.

    status
        ``"commit"``    -- confident, unique match; anchor here.
        ``"ambiguous"`` -- several identical candidates tie; wait for more context
                           (``candidates`` lists the tied verses).
        ``"none"``      -- nothing matched above threshold.
    """
    status: str
    chapter: Optional[int] = None
    ayah: Optional[int] = None
    word_index: Optional[int] = None
    score: float = 0.0
    candidates: List[Tuple[int, int, int]] = field(default_factory=list)


def _verse_start_offsets(words: List[Dict[str, Any]]) -> List[Tuple[int, int, int]]:
    """Return ``(surah, ayah, word_list_index)`` for the first word of each verse, in order."""
    offsets: List[Tuple[int, int, int]] = []
    prev_key: Optional[Tuple[int, int]] = None
    for i, w in enumerate(words):
        key = (w["surah"], w["ayah"])
        if key != prev_key:
            offsets.append((w["surah"], w["ayah"], i))
            prev_key = key
    return offsets


def _score_offsets(
    decoded_words: List[str],
    words: List[Dict[str, Any]],
    start_indices: List[int],
) -> Dict[int, float]:
    """Align the decoded utterance against the reference starting at each index.

    For each start index ``s`` the reference window is ``words[s : s + len(decoded)]``
    (so a longer utterance that runs into the next verse contributes trailing words
    that distinguish otherwise-identical verses). Score is ``max(0, 1 - CER)`` over
    base letters (tashkeel stripped).
    """
    decoded_norm = _normalize_for_verse_match(" ".join(decoded_words))
    scores: Dict[int, float] = {}
    if not decoded_norm:
        return scores
    n = len(decoded_words)
    for s in start_indices:
        window = words[s : s + n]
        ref_norm = _normalize_for_verse_match(
            " ".join(w["emlaey_text"] for w in window)
        )
        if not ref_norm:
            continue
        scores[s] = max(0.0, 1.0 - cer(ref_norm, decoded_norm))
    return scores


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
    *,
    start_chapter: Optional[int] = None,
    start_verse: Optional[int] = None,
    is_final: bool = False,
) -> DetectionResult:
    """Detect which verse the user is reciting from their first utterance.

    Aligns the whole decoded utterance against every verse start in ``words``.
    Verses that share a prefix but diverge later are separated because the full
    utterance is compared; genuinely identical verses tie and yield an
    ``"ambiguous"`` result so the caller can wait for the next (distinct) verse.

    Parameters
    ----------
    audio : np.ndarray
        16 kHz float32 mono audio of the user's utterance so far.
    words : list
        The flat word list for the session (may span multiple chapters).
    start_chapter, start_verse : int, optional
        The range's selected start; used to break an unavoidable tie (on
        ``is_final``) toward the earliest occurrence at or after it.
    is_final : bool
        True when speech has ended. On a tie this forces a best-guess commit
        instead of waiting forever.

    Returns
    -------
    DetectionResult
        ``word_index`` (when set) is the index into ``words`` where the matched
        verse begins.
    """
    threshold = config.verse_detection_threshold
    epsilon = config.verse_detection_tie_epsilon

    offsets = _verse_start_offsets(words)
    if not offsets:
        logger.warning("No verse candidates found in word list")
        return DetectionResult(status="none")

    decoded_text, _ = _decode_audio(audio)
    decoded_words = decoded_text.split()
    if not decoded_words:
        logger.info("Verse detection: wav2vec2 decoded empty text")
        return DetectionResult(status="none")

    by_index = {idx: (surah, ayah, idx) for (surah, ayah, idx) in offsets}
    index_scores = _score_offsets(decoded_words, words, list(by_index.keys()))
    if not index_scores:
        return DetectionResult(status="none")

    best_score = max(index_scores.values())
    logger.info(
        "Verse detection: decoded '%s' (%d verses, best=%.3f, threshold=%.2f)",
        display_arabic(decoded_text), len(offsets), best_score, threshold,
    )

    if best_score < threshold:
        return DetectionResult(status="none", score=best_score)

    # Tied candidates = identical / near-identical verses within epsilon of the best.
    # Kept in word order so "earliest at/after start" fallbacks are natural.
    tied = [
        by_index[idx]
        for idx in sorted(index_scores)
        if index_scores[idx] >= best_score - epsilon
    ]

    if len(tied) == 1:
        surah, ayah, wi = tied[0]
        logger.info("Verse detection: matched %d:%d (score=%.3f)", surah, ayah, best_score)
        return DetectionResult(
            status="commit", chapter=surah, ayah=ayah, word_index=wi, score=best_score
        )

    # Ambiguous: several identical candidates. Wait for the next distinct verse
    # unless speech has ended, in which case fall back to a sensible default.
    if not is_final:
        logger.info(
            "Verse detection: ambiguous (%d identical candidates: %s) — waiting for more context",
            len(tied), [f"{s}:{a}" for s, a, _ in tied],
        )
        return DetectionResult(status="ambiguous", score=best_score, candidates=tied)

    chosen = None
    if start_chapter is not None and start_verse is not None:
        for (surah, ayah, wi) in tied:
            if (surah, ayah) >= (start_chapter, start_verse):
                chosen = (surah, ayah, wi)
                break
    if chosen is None:
        chosen = tied[0]
    surah, ayah, wi = chosen
    logger.info(
        "Verse detection: ambiguous refrain — final fallback to earliest at/after start -> %d:%d",
        surah, ayah,
    )
    return DetectionResult(
        status="commit", chapter=surah, ayah=ayah, word_index=wi, score=best_score, candidates=tied
    )
