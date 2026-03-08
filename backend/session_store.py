"""
Persist per-session word results to data/sessions/{uuid}/data.json.

Each recitation session gets its own folder identified by a UUID.
The JSON file is rewritten on every word addition for real-time persistence.
"""

import json
import os
import uuid
import wave
from datetime import datetime, timezone

from backend.config import config


BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "sessions")


class SessionStore:
    """Manages a single session's word-result log and audio on disk."""

    def __init__(self, session_id: str | None = None):
        self.session_id = session_id or str(uuid.uuid4())
        self.session_dir = os.path.join(BASE_DIR, self.session_id)
        self.data_file = os.path.join(self.session_dir, "data.json")
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

    def add_word(
        self,
        *,
        chapter_number: int,
        verse_number: int,
        word_number: int,
        word_text: str,
        score: float,
        status: str,
    ) -> None:
        """Append a word entry and persist to disk."""
        self._entries.append({
            "chapter_number": chapter_number,
            "verse_number": verse_number,
            "word_number": word_number,
            "word_text": word_text,
            "score": round(score, 3),
            "status": status,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        self._flush()

    # ------------------------------------------------------------------

    def _flush(self) -> None:
        """Write the full entries list to data.json (atomic-ish)."""
        tmp = self.data_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._entries, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.data_file)
