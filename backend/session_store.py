"""
Persist per-session word results to data/sessions/{uuid}/data.json.

Each recitation session gets its own folder identified by a UUID.
The JSON file is rewritten on every word addition for real-time persistence.
"""

import json
import os
import uuid
from datetime import datetime, timezone


BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "sessions")


class SessionStore:
    """Manages a single session's word-result log on disk."""

    def __init__(self):
        self.session_uuid = str(uuid.uuid4())
        self.session_dir = os.path.join(BASE_DIR, self.session_uuid)
        self.data_file = os.path.join(self.session_dir, "data.json")
        self._entries: list[dict] = []

        os.makedirs(self.session_dir, exist_ok=True)
        self._flush()

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
