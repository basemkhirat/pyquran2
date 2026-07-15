import re
from typing import List, Dict, Any
from jiwer import cer, process_characters
from pyarabic import araby
from backend.config import config, SCORED_DIACRITICS, normalize_sukoon

# Arabic diacritics (tashkeel) unicode range - used by extract_diacritics for tests
_DIACRITICS = re.compile(r"[\u0617-\u061A\u064B-\u0652\u0670\u06D6-\u06ED]")


def strip_diacritics(text: str) -> str:
    return araby.strip_tashkeel(text)


def extract_diacritics(text: str) -> str:
    return "".join(_DIACRITICS.findall(text))


def extract_scored_diacritics(text: str) -> str:
    """Extract only scored diacritics: fatha, kasra, damma, sukoon, shadda."""
    return "".join(SCORED_DIACRITICS.findall(normalize_sukoon(text)))


def compute_char_score(expected: str, transcribed: str) -> float:
    """Character-level accuracy (1 - CER) after stripping diacritics."""
    exp = strip_diacritics(expected).strip()
    trans = strip_diacritics(transcribed).strip()
    if not exp:
        return 1.0 if not trans else 0.0
    error_rate = cer(exp, trans)
    return max(0.0, 1.0 - error_rate)


def compute_diacritic_score(expected: str, transcribed: str) -> float:
    """Diacritic (tashkeel) accuracy using CER on extracted scored diacritics only."""
    exp_d = extract_scored_diacritics(expected)
    trans_d = extract_scored_diacritics(transcribed)
    if not exp_d:
        return 1.0 if not trans_d else 0.0
    error_rate = cer(exp_d, trans_d)
    return max(0.0, 1.0 - error_rate)


def compute_text_score(char_score: float, diacritic_score: float) -> float:
    """Compute text score from character and diacritic scores."""
    return (config.weight_char * char_score) + (config.weight_diacritic * diacritic_score)


def compute_total_score(
    char_score: float, diacritic_score: float, acoustic_score: float | None = None
) -> float:
    """Compute total score based on enabled scoring methods.
    
    When both text and acoustic scoring are enabled:
        total = WEIGHT_TEXT * text_score + WEIGHT_ACOUSTIC * acoustic_score
    When only text scoring is enabled:
        total = text_score
    When only acoustic scoring is enabled:
        total = acoustic_score
    """
    text_score = compute_text_score(char_score, diacritic_score)
    
    text_enabled = config.enable_text_score
    acoustic_enabled = config.enable_acoustic_score and acoustic_score is not None
    
    if text_enabled and acoustic_enabled:
        return (config.weight_text * text_score) + (config.weight_acoustic * acoustic_score)
    elif text_enabled:
        return text_score
    elif acoustic_enabled:
        return acoustic_score
    else:
        return text_score


def correct_word(expected: str, transcribed: str, max_edits: int) -> str:
    """If transcribed is within max_edits (add/delete/substitute) of expected, return expected; else return transcribed.
    Uses same normalization as character scoring (strip diacritics)."""
    exp = strip_diacritics(expected).strip()
    trans = strip_diacritics(transcribed).strip()
    if not exp:
        return expected if not trans else transcribed
    out = process_characters(exp, trans)
    total_edits = out.substitutions + out.insertions + out.deletions
    return expected if total_edits <= max_edits else transcribed


def score_word(expected: str, transcribed: str) -> Dict[str, float]:
    cs = compute_char_score(expected, transcribed)
    ds = compute_diacritic_score(expected, transcribed)
    text_s = compute_text_score(cs, ds)
    total_s = compute_total_score(cs, ds)
    return {
        "char_score": round(cs, 3),
        "diacritic_score": round(ds, 3),
        "text_score": round(text_s, 3),
        "total_score": round(total_s, 3),
    }


def should_advance(status: str, mode: str) -> bool:
    """Whether to move to the next word after scoring the current one.

    In "word_by_word" mode the session only advances when the word passed (status
    "correct"), so the reciter repeats a word until it is right. In "continuous" mode
    the session always advances — every word is scored and recorded but never blocks.
    """
    return status == "correct" or mode == "continuous"


def should_skip_forward(
    mode: str, later_scores: List[float], threshold: float, is_final: bool
) -> bool:
    """Whether to advance past an expected word that got no acoustic match (continuous mode only).

    Advance only on real evidence the reciter substituted/skipped this word and moved on: a LATER
    word in the current buffer was recited *confidently* — its score clears `threshold` (a real
    pass). Requiring a clean pass, not just a weak best-match, avoids irreversibly confirming a word
    as wrong on a transient/partial interim decode where the reciter is still mid-word; such a word
    stays pending until the decode catches up and then scores normally.

    On the final pass (`is_final`) there is no more audio to wait for, so any later match at all
    (score > 0) is enough to advance rather than get stuck. In "word_by_word" mode the reciter
    repeats the word, so never skip.
    """
    if mode != "continuous":
        return False
    if any(s >= threshold for s in later_scores):
        return True
    return is_final and any(s > 0.0 for s in later_scores)


def score_word_best(
    emlaey: str, uthmani: str, transcribed: str, max_edits: int
) -> Dict[str, Any]:
    """Score transcribed against both emlaey and uthmani, return best of each component.

    Uses correct_word per variant for char_score; raw transcribed for diacritic_score.
    Returns scores dict plus "t_corrected" (from the variant that gave better char_score).
    """
    t_corr_e = correct_word(emlaey, transcribed, max_edits)
    t_corr_u = correct_word(uthmani, transcribed, max_edits)
    cs_e = compute_char_score(emlaey, t_corr_e)
    cs_u = compute_char_score(uthmani, t_corr_u)
    ds_e = compute_diacritic_score(emlaey, transcribed)
    ds_u = compute_diacritic_score(uthmani, transcribed)
    cs = max(cs_e, cs_u)
    ds = max(ds_e, ds_u)
    text_s = compute_text_score(cs, ds)
    total_s = compute_total_score(cs, ds)
    t_corrected = t_corr_e if cs_e >= cs_u else t_corr_u
    return {
        "char_score": round(cs, 3),
        "diacritic_score": round(ds, 3),
        "text_score": round(text_s, 3),
        "total_score": round(total_s, 3),
        "t_corrected": t_corrected,
    }


def align_multi_word(transcribed_text: str, expected_words: List[str]) -> List[Dict[str, Any]]:
    """Align multi-word Whisper output to expected word sequence.

    Splits transcribed text by spaces and scores each against the corresponding
    expected word. If counts don't match, aligns from the start and marks
    remaining expected words as unmatched.
    """
    transcribed_parts = transcribed_text.strip().split()
    results = []

    for i, expected in enumerate(expected_words):
        if i < len(transcribed_parts):
            scores = score_word(expected, transcribed_parts[i])
            results.append({
                "expected": expected,
                "transcribed": transcribed_parts[i],
                **scores,
                "status": "correct" if scores["total_score"] >= config.score_threshold else "incorrect",
            })
        else:
            results.append({
                "expected": expected,
                "transcribed": "",
                "char_score": 0.0,
                "diacritic_score": 0.0,
                "text_score": 0.0,
                "total_score": 0.0,
                "status": "incorrect",
            })

    return results
