import re
from typing import List, Dict, Any
from jiwer import cer
from pyarabic import araby
from backend.config import config

# Arabic diacritics (tashkeel) unicode range
_DIACRITICS = re.compile(r"[\u0617-\u061A\u064B-\u0652\u0670\u06D6-\u06ED]")


def strip_diacritics(text: str) -> str:
    return araby.strip_tashkeel(text)


def extract_diacritics(text: str) -> str:
    return "".join(_DIACRITICS.findall(text))


def compute_char_score(expected: str, transcribed: str) -> float:
    """Character-level accuracy (1 - CER) after stripping diacritics."""
    exp = strip_diacritics(expected).strip()
    trans = strip_diacritics(transcribed).strip()
    if not exp:
        return 1.0 if not trans else 0.0
    error_rate = cer(exp, trans)
    return max(0.0, 1.0 - error_rate)


def compute_diacritic_score(expected: str, transcribed: str) -> float:
    """Diacritic (tashkeel) accuracy using CER on extracted diacritics."""
    exp_d = extract_diacritics(expected)
    trans_d = extract_diacritics(transcribed)
    if not exp_d:
        return 1.0 if not trans_d else 0.0
    error_rate = cer(exp_d, trans_d)
    return max(0.0, 1.0 - error_rate)


def compute_total_score(char_score: float, diacritic_score: float) -> float:
    return (config.weight_char * char_score) + (config.weight_diacritic * diacritic_score)


def score_word(expected: str, transcribed: str) -> Dict[str, float]:
    cs = compute_char_score(expected, transcribed)
    ds = compute_diacritic_score(expected, transcribed)
    ts = compute_total_score(cs, ds)
    return {
        "char_score": round(cs, 3),
        "diacritic_score": round(ds, 3),
        "total_score": round(ts, 3),
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
                "total_score": 0.0,
                "status": "incorrect",
            })

    return results
