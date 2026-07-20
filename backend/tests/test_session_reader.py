"""Tests for session_reader (reading a recorded session back for playback)."""
import json
import os
import struct
import wave

import pytest

from backend import session_reader
from backend.config import config


def _write_session(base, session_id, info):
    directory = os.path.join(str(base), session_id)
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False)
    return directory


def _write_wav(directory, seconds=1.0):
    path = os.path.join(directory, "recording.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(config.audio_sample_rate)
        w.writeframes(b"\x00\x00" * int(config.audio_sample_rate * seconds))
    return path


def _entry(chapter, verse, word, status="correct", score=1.0, start=0, end=500):
    return {
        "chapter_number": chapter, "verse_number": verse, "word_number": word,
        "word_text": f"w{word}", "status": status, "score": score,
        "start_time": start, "end_time": end,
    }


@pytest.fixture
def sessions_base(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.session_store.BASE_DIR", str(tmp_path))
    return tmp_path


class TestSessionLookup:
    @pytest.mark.parametrize("bad_id", [
        "..", "../..", "../other", "a/b", "/etc/passwd", ".hidden", "", "  ",
        "sess id", "sess;id", "x" * 200,
    ])
    def test_rejects_unsafe_ids(self, sessions_base, bad_id):
        assert session_reader.session_dir(bad_id) is None
        assert session_reader.build_playback(bad_id) is None

    def test_traversal_cannot_escape_base(self, sessions_base, tmp_path):
        # A real session dir outside the base must stay unreachable.
        outside = tmp_path.parent / "outside-sessions"
        os.makedirs(outside, exist_ok=True)
        with open(outside / "info.json", "w") as f:
            json.dump({"id": "x", "words": []}, f)
        assert session_reader.session_dir("../outside-sessions") is None

    def test_unknown_id_returns_none(self, sessions_base):
        assert session_reader.build_playback("2f1c9a4e-0000-4000-8000-000000000000") is None

    def test_unreadable_info_returns_none(self, sessions_base):
        directory = os.path.join(str(sessions_base), "broken")
        os.makedirs(directory)
        with open(os.path.join(directory, "info.json"), "w") as f:
            f.write("{not json")
        assert session_reader.build_playback("broken") is None


class TestRangeResolution:
    def test_uses_stored_range(self, sessions_base):
        _write_session(sessions_base, "stored", {
            "id": "stored", "type": "continuous", "narration_id": 1, "score_threshold": 0.6,
            "start_chapter_number": 1, "start_verse_number": 1,
            "end_chapter_number": 1, "end_verse_number": 2,
            "words": [_entry(1, 1, 1)],
        })
        payload = session_reader.build_playback("stored")
        assert payload["range"] == {
            "start_chapter": 1, "start_verse": 1, "end_chapter": 1, "end_verse": 2,
        }
        assert payload["range_inferred"] is False
        assert payload["mode"] == "continuous"       # info.json's `type` is exposed as `mode`
        # Fatiha 1:1 has 4 words, 1:2 has 4 -> the full stored range is rendered, not just
        # the single word that was recited.
        assert len(payload["words"]) == 8

    def test_infers_range_for_legacy_session(self, sessions_base):
        # Sessions recorded before the range fields existed have no range at all.
        _write_session(sessions_base, "legacy", {
            "id": "legacy", "type": "word_by_word", "narration_id": 1, "score_threshold": 0.76,
            "words": [_entry(1, 2, 1, start=1000, end=1600),
                      _entry(1, 3, 1, start=2000, end=2600)],
        })
        payload = session_reader.build_playback("legacy")
        assert payload["range_inferred"] is True
        assert payload["range"] == {
            "start_chapter": 1, "start_verse": 2, "end_chapter": 1, "end_verse": 3,
        }
        assert all(w["ayah"] in (2, 3) for w in payload["words"])
        assert all(e["display_index"] is not None for e in payload["timeline"])

    def test_stored_range_is_unioned_with_timeline(self, sessions_base):
        # A recited word outside the stored range must still resolve to a display word.
        _write_session(sessions_base, "union", {
            "id": "union",
            "start_chapter_number": 1, "start_verse_number": 1,
            "end_chapter_number": 1, "end_verse_number": 1,
            "words": [_entry(1, 4, 2)],
        })
        payload = session_reader.build_playback("union")
        assert payload["range"]["end_verse"] == 4
        assert payload["timeline"][0]["display_index"] is not None


class TestTimelineMerge:
    def test_repeated_word_keeps_both_attempts(self, sessions_base):
        # word_by_word re-records a failed word on every retry, so one display word can
        # have several attempts with different verdicts.
        _write_session(sessions_base, "retry", {
            "id": "retry",
            "words": [
                _entry(1, 4, 3, status="incorrect", score=0.683, start=75936, end=76516),
                _entry(1, 4, 3, status="correct", score=1.0, start=88892, end=89432),
            ],
        })
        payload = session_reader.build_playback("retry")
        assert len(payload["timeline"]) == 2
        first, second = payload["timeline"]
        assert first["display_index"] == second["display_index"]
        assert (first["status"], second["status"]) == ("incorrect", "correct")
        assert payload["stats"] == {
            "total_words": len(payload["words"]),
            "attempts": 2, "distinct_recited": 1, "correct": 1, "incorrect": 1,
        }

    def test_timeline_sorted_by_start_time(self, sessions_base):
        _write_session(sessions_base, "unsorted", {
            "id": "unsorted",
            "words": [_entry(1, 1, 2, start=900, end=1200),
                      _entry(1, 1, 1, start=100, end=400)],
        })
        starts = [e["start_time"] for e in session_reader.build_playback("unsorted")["timeline"]]
        assert starts == sorted(starts)

    def test_malformed_entry_is_skipped(self, sessions_base):
        _write_session(sessions_base, "malformed", {
            "id": "malformed",
            "words": [{"word_text": "x", "status": "correct"}, _entry(1, 1, 1)],
        })
        assert len(session_reader.build_playback("malformed")["timeline"]) == 1

    def test_empty_words_is_valid(self, sessions_base):
        # A real session on disk looks like this — it must render, not crash.
        _write_session(sessions_base, "empty", {"id": "empty", "type": "word_by_word", "words": []})
        payload = session_reader.build_playback("empty")
        assert payload["range"] is None
        assert payload["words"] == []
        assert payload["timeline"] == []
        assert payload["stats"]["attempts"] == 0


class TestRecording:
    def test_missing_recording(self, sessions_base):
        _write_session(sessions_base, "norec", {"id": "norec", "words": [_entry(1, 1, 1)]})
        payload = session_reader.build_playback("norec")
        assert payload["has_recording"] is False
        assert payload["duration_ms"] is None
        assert session_reader.recording_path("norec") is None

    def test_duration_from_valid_wav(self, sessions_base):
        directory = _write_session(sessions_base, "rec", {"id": "rec", "words": [_entry(1, 1, 1)]})
        _write_wav(directory, seconds=2.5)
        payload = session_reader.build_playback("rec")
        assert payload["has_recording"] is True
        assert payload["duration_ms"] == 2500

    def test_duration_survives_stale_wav_header(self, sessions_base):
        # close_audio() finalizes the RIFF length fields; a session whose process died
        # leaves them at 0, and the browser then reports a duration of Infinity.
        directory = _write_session(sessions_base, "stale", {"id": "stale", "words": []})
        path = _write_wav(directory, seconds=3.0)
        with open(path, "r+b") as f:
            f.seek(4); f.write(struct.pack("<I", 0))   # RIFF chunk size
            f.seek(40); f.write(struct.pack("<I", 0))  # data chunk size
        assert session_reader.wav_duration_ms(path) == 3000
