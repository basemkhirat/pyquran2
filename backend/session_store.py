"""
Persist per-session info + word timeline to data/sessions/{uuid}/info.json.

Each recitation session gets its own folder identified by a UUID, containing:
- recording.wav — the full session audio (mono, 16-bit PCM, 16kHz)
- info.json — session metadata (id, type/mode, narration_id, score_threshold, and the
  recited verse range as start/end chapter+verse numbers) plus a `words` array with one
  entry per confirmed *spoken* word (correct or incorrect), each carrying
  start_time/end_time as integer milliseconds relative to the start of recording.wav.

The JSON file is rewritten on every word addition for real-time persistence.
"""

import json
import logging
import os
import uuid
import wave
from typing import Callable, Optional

from backend.config import config


logger = logging.getLogger(__name__)

# Read at call time (not captured in __init__), so tests can monkeypatch this module global.
BASE_DIR = config.sessions_dir


# --- Durability hooks -------------------------------------------------------------------
# On a plain filesystem writes are durable immediately and these stay unset. On Modal the
# sessions dir is a Volume, which needs an explicit commit to persist and a reload to see
# writes from another container. modal_app.py installs them; backend/ never imports modal.
_commit_hook: Optional[Callable[[], None]] = None
_reload_hook: Optional[Callable[[], None]] = None


def set_commit_hook(fn: Optional[Callable[[], None]]) -> None:
    global _commit_hook
    _commit_hook = fn


def set_reload_hook(fn: Optional[Callable[[], None]]) -> None:
    global _reload_hook
    _reload_hook = fn


def commit() -> None:
    """Persist pending writes. No-op on a plain filesystem.

    Called once per session close — never per word. _flush() runs on every word and a
    Volume commit is far too expensive for that.
    """
    if _commit_hook is None:
        return
    try:
        _commit_hook()
    except Exception:
        logger.exception("Session store commit failed")


def reload() -> None:
    """Pick up writes made by another container. No-op on a plain filesystem."""
    if _reload_hook is None:
        return
    try:
        _reload_hook()
    except Exception:
        logger.exception("Session store reload failed")


class SessionStore:
    """Manages a single session's word timeline and audio on disk."""

    def __init__(
        self,
        session_id: str | None = None,
        *,
        mode: str = "word_by_word",
        score_threshold: float | None = None,
        narration_id: int = 1,
        start_chapter_number: int | None = None,
        start_verse_number: int | None = None,
        end_chapter_number: int | None = None,
        end_verse_number: int | None = None,
    ):
        self.session_id = session_id or str(uuid.uuid4())
        self.mode = mode
        self.score_threshold = score_threshold
        self.narration_id = narration_id
        # The verse range the session was started with (may span chapters).
        self.start_chapter_number = start_chapter_number
        self.start_verse_number = start_verse_number
        self.end_chapter_number = end_chapter_number
        self.end_verse_number = end_verse_number
        self.session_dir = os.path.join(BASE_DIR, self.session_id)
        self.info_file = os.path.join(self.session_dir, "info.json")
        self.recording_file = os.path.join(self.session_dir, "recording.wav")
        self._entries: list[dict] = []
        self._wav_file = None

        os.makedirs(self.session_dir, exist_ok=True)
        self._flush()

    # ------------------------------------------------------------------

    def append_audio(self, pcm_data: bytes) -> None:
        """Append PCM16 raw audio data to the session's WAV file."""
        if not self._wav_file:
            self._wav_file = wave.open(self.recording_file, "wb")
            self._wav_file.setnchannels(1)
            self._wav_file.setsampwidth(2)
            self._wav_file.setframerate(config.audio_sample_rate)

        self._wav_file.writeframes(pcm_data)
        # Flush the underlying file to disk so it updates in real-time
        if hasattr(self._wav_file, "_file"):
            self._wav_file._file.flush()

    def close_audio(self) -> None:
        """Close the WAV file, finalizing its header."""
        if self._wav_file:
            self._wav_file.close()
            self._wav_file = None

    # ------------------------------------------------------------------

    def add_timeline_word(
        self,
        *,
        chapter_number: int,
        verse_number: int,
        word_number: int,
        word_text: str,
        status: str,
        score: float,
        start_time: float,
        end_time: float,
    ) -> None:
        """Append a spoken-word timeline entry and persist to disk.

        start_time/end_time are given in seconds relative to the start of recording.wav
        and are persisted as integer milliseconds.
        """
        self._entries.append({
            "chapter_number": chapter_number,
            "verse_number": verse_number,
            "word_number": word_number,
            "word_text": word_text,
            "status": status,
            "score": round(score, 3),
            "start_time": round(start_time * 1000),
            "end_time": round(end_time * 1000),
        })
        self._flush()

    # ------------------------------------------------------------------

    def _flush(self) -> None:
        """Write session info + the word list to info.json (atomic-ish)."""
        data = {
            "id": self.session_id,
            "type": self.mode,
            "narration_id": self.narration_id,
            "score_threshold": self.score_threshold,
            "start_chapter_number": self.start_chapter_number,
            "start_verse_number": self.start_verse_number,
            "end_chapter_number": self.end_chapter_number,
            "end_verse_number": self.end_verse_number,
            "words": self._entries,
        }
        tmp = self.info_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.info_file)
