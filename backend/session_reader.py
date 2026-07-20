"""
Read back a recorded session for playback.

session_store.py writes; this module reads. It turns a session folder into a single
payload the playback page can render without further round-trips:

- the full verse range as display words (same shape as GET /api/words), and
- the recorded timeline, each entry pointing at its display word via `display_index`.

`display_index` is the bridge between the two naming schemes in this codebase: display
words use surah/ayah/word_index, the persisted timeline uses chapter_number/verse_number/
word_number. Resolving it here means the frontend never has to.

Two properties of the stored data shape everything below:

- The timeline is *sparse* — only spoken words are recorded, skipped ones are not.
- The timeline is *not unique* — in word_by_word mode a failed word is re-recorded on
  every retry, so one display word can have several attempts with different verdicts.
"""

import json
import logging
import os
import re
import wave
from typing import Any, Dict, List, Optional, Tuple

from backend import quran_data, session_store
from backend.config import config


logger = logging.getLogger(__name__)

# Session ids are uuid4 in production, but the tests (and any hand-made folder) use simpler
# names, so this is deliberately wider than a UUID parse — while still rejecting anything
# that could escape the sessions dir: no dots, slashes, backslashes or leading punctuation.
# (uuid.UUID() would be wrong here: it accepts "urn:uuid:..." and braced forms.)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# Canonical PCM WAV header size, used to derive duration when the header is unreliable.
_WAV_HEADER_BYTES = 44


def is_valid_session_id(session_id: str) -> bool:
    return bool(session_id) and _ID_RE.match(session_id) is not None


def session_dir(session_id: str) -> Optional[str]:
    """Absolute path to a session folder, or None if the id is invalid or unknown.

    Guards traversal twice: the id pattern above, then a containment check on the
    resolved path. commonpath (not startswith) — otherwise a sibling directory like
    ".../sessions-evil" would pass the prefix test.
    """
    if not is_valid_session_id(session_id):
        return None

    base = session_store.BASE_DIR
    candidate = os.path.join(base, session_id)
    real_base = os.path.realpath(base)
    try:
        if os.path.commonpath([real_base, os.path.realpath(candidate)]) != real_base:
            return None
    except ValueError:
        # Different drives on Windows — cannot be inside base.
        return None

    if os.path.isdir(candidate):
        return candidate
    # On a Modal Volume the session may have been written by another container; reload
    # once and re-check. Only on a miss, so the common path stays free.
    session_store.reload()
    return candidate if os.path.isdir(candidate) else None


def load_info(session_id: str) -> Optional[Dict[str, Any]]:
    """Parse a session's info.json, or None if missing/unreadable.

    _flush() writes via tmp file + os.replace, so a reader never sees a partial file
    even while the session is still being recorded.
    """
    directory = session_dir(session_id)
    if directory is None:
        return None
    try:
        with open(os.path.join(directory, "info.json"), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        logger.exception(f"Unreadable info.json for session {session_id}")
        return None


def recording_path(session_id: str) -> Optional[str]:
    """Absolute path to a session's recording.wav, or None if there isn't one."""
    directory = session_dir(session_id)
    if directory is None:
        return None
    path = os.path.join(directory, "recording.wav")
    return path if os.path.isfile(path) else None


def wav_duration_ms(path: str) -> Optional[int]:
    """Duration of a PCM WAV in milliseconds, computed defensively.

    The RIFF length fields are only finalized by SessionStore.close_audio(), so a session
    whose process died mid-recording leaves them stale — the browser then reports a
    duration of Infinity and seeking breaks. The frame count implied by the file size is
    correct in that case, so prefer whichever is larger.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return None

    sample_rate = config.audio_sample_rate
    size_frames = max(0, (size - _WAV_HEADER_BYTES) // 2)  # mono, 16-bit

    header_frames = 0
    try:
        with wave.open(path, "rb") as w:
            header_frames = w.getnframes()
            sample_rate = w.getframerate() or sample_rate
    except (wave.Error, EOFError, OSError):
        logger.warning(f"Unreadable WAV header at {path}; using file size for duration")

    frames = max(header_frames, size_frames)
    if frames <= 0 or sample_rate <= 0:
        return None
    return round(frames / sample_rate * 1000)


def _normalize_timeline(session_id: str, raw: List[Any]) -> List[Dict[str, Any]]:
    """Validate and time-sort raw timeline entries, dropping any that are malformed.

    Runs before range resolution so a single corrupt entry can't break the whole payload.
    """
    entries: List[Dict[str, Any]] = []
    for entry in raw:
        try:
            chapter = int(entry["chapter_number"])
            verse = int(entry["verse_number"])
            word = int(entry["word_number"])
        except (KeyError, TypeError, ValueError):
            logger.warning(f"Skipping malformed timeline entry in session {session_id}")
            continue
        entries.append({
            "chapter_number": chapter,
            "verse_number": verse,
            "word_number": word,
            "word_text": entry.get("word_text", ""),
            # Absent in sessions recorded before this field existed.
            "detected_text": entry.get("detected_text", ""),
            "status": entry.get("status", "incorrect"),
            "score": entry.get("score", 0.0),
            "start_time": entry.get("start_time", 0),
            "end_time": entry.get("end_time", 0),
        })
    entries.sort(key=lambda e: (e["start_time"], e["end_time"]))
    return entries


def _verse_key(entry: Dict[str, Any]) -> Tuple[int, int]:
    return (entry["chapter_number"], entry["verse_number"])


def _resolve_range(
    info: Dict[str, Any], timeline: List[Dict[str, Any]]
) -> Tuple[Optional[Dict[str, int]], bool]:
    """Work out which verses to display, and whether that had to be inferred.

    Sessions recorded before the range fields existed have no range at all, so inference
    from the timeline is a normal path, not just a corruption fallback. When a stored
    range *is* present it is unioned with the timeline's own span, so a timeline entry can
    never fall outside the displayed words and end up unmatched.
    """
    stored = None
    keys = ("start_chapter_number", "start_verse_number", "end_chapter_number", "end_verse_number")
    if all(info.get(k) is not None for k in keys):
        stored = {
            "start_chapter": int(info["start_chapter_number"]),
            "start_verse": int(info["start_verse_number"]),
            "end_chapter": int(info["end_chapter_number"]),
            "end_verse": int(info["end_verse_number"]),
        }

    spoken = None
    if timeline:
        verse_keys = [_verse_key(e) for e in timeline]
        (min_ch, min_v), (max_ch, max_v) = min(verse_keys), max(verse_keys)
        spoken = {
            "start_chapter": min_ch, "start_verse": min_v,
            "end_chapter": max_ch, "end_verse": max_v,
        }

    if stored is None:
        return spoken, spoken is not None
    if spoken is None:
        return stored, False

    merged = dict(stored)
    if (spoken["start_chapter"], spoken["start_verse"]) < (stored["start_chapter"], stored["start_verse"]):
        merged["start_chapter"] = spoken["start_chapter"]
        merged["start_verse"] = spoken["start_verse"]
    if (spoken["end_chapter"], spoken["end_verse"]) > (stored["end_chapter"], stored["end_verse"]):
        merged["end_chapter"] = spoken["end_chapter"]
        merged["end_verse"] = spoken["end_verse"]
    return merged, False


def build_playback(session_id: str) -> Optional[Dict[str, Any]]:
    """Everything the playback page needs, or None if the session doesn't exist."""
    info = load_info(session_id)
    if info is None:
        return None

    entries = _normalize_timeline(session_id, info.get("words") or [])
    verse_range, inferred = _resolve_range(info, entries)

    words: List[Dict[str, Any]] = []
    if verse_range is not None:
        words = quran_data.get_words_range(
            verse_range["start_chapter"], verse_range["start_verse"],
            verse_range["end_chapter"], verse_range["end_verse"],
        )
        if not words:
            # Stored range points at nothing real (corrupt or out of bounds) — fall back to
            # what was actually recited so the page still renders something.
            verse_range, inferred = _resolve_range({}, entries)
            if verse_range is not None:
                words = quran_data.get_words_range(
                    verse_range["start_chapter"], verse_range["start_verse"],
                    verse_range["end_chapter"], verse_range["end_verse"],
                )

    # (surah, ayah, word_index) -> position in words[], the index the frontend renders by.
    positions = {(w["surah"], w["ayah"], w["word_index"]): i for i, w in enumerate(words)}

    timeline: List[Dict[str, Any]] = []
    for entry in entries:
        key = (entry["chapter_number"], entry["verse_number"], entry["word_number"])
        # display_index is None when the entry has no matching display word; the entry is
        # kept anyway so the progress bar's attempt markers still line up with the audio.
        timeline.append({"display_index": positions.get(key), **entry})
    correct = sum(1 for e in timeline if e["status"] == "correct")

    wav = recording_path(session_id)
    return {
        "id": info.get("id", session_id),
        # info.json calls it `type`; expose it as `mode` so the frontend reuses SessionMode.
        "mode": info.get("type", "word_by_word"),
        "narration_id": info.get("narration_id", 1),
        "score_threshold": info.get("score_threshold"),
        "range": verse_range,
        "range_inferred": inferred,
        "duration_ms": wav_duration_ms(wav) if wav else None,
        "has_recording": wav is not None,
        "words": words,
        "timeline": timeline,
        "stats": {
            "total_words": len(words),
            "attempts": len(timeline),
            "distinct_recited": len({
                (e["chapter_number"], e["verse_number"], e["word_number"]) for e in timeline
            }),
            "correct": correct,
            "incorrect": len(timeline) - correct,
        },
    }
