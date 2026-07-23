"""Tests for SessionStore (per-session info.json + recording.wav)."""
import json
import os
import wave

from backend.config import config
from backend.session_store import SessionStore


def _read_info(store):
    with open(store.info_file, encoding="utf-8") as f:
        return json.load(f)


class TestSessionStore:
    def test_creates_session_dir_and_info_shell(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.session_store.BASE_DIR", str(tmp_path))
        store = SessionStore(
            session_id="sess-1", mode="continuous", score_threshold=0.6,
            start_chapter_number=2, start_verse_number=255,
            end_chapter_number=3, end_verse_number=4,
        )
        assert os.path.isdir(store.session_dir)
        assert os.path.basename(store.session_dir) == "sess-1"
        # info.json holds session metadata + an (initially empty) words array.
        assert _read_info(store) == {
            "id": "sess-1",
            "type": "continuous",
            "narration_id": 1,
            "score_threshold": 0.6,
            "duration": 0,
            "start_chapter_number": 2,
            "start_verse_number": 255,
            "end_chapter_number": 3,
            "end_verse_number": 4,
            "words": [],
        }
        # no legacy files remain from the old formats
        assert not os.path.exists(os.path.join(store.session_dir, "data.json"))
        assert not os.path.exists(os.path.join(store.session_dir, "timeline.json"))

    def test_add_timeline_word_schema_and_ms_times(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.session_store.BASE_DIR", str(tmp_path))
        store = SessionStore(session_id="sess-2", mode="word_by_word", score_threshold=0.5)
        store.add_timeline_word(
            chapter_number=1, verse_number=2, word_number=3,
            expected_text="ٱلْحَمْدُ", detected_text="الحمد", status="correct", total_score=0.87654,
            start_time=1.23456, end_time=1.98765,  # seconds in
        )
        info = _read_info(store)
        assert info["id"] == "sess-2"
        assert info["type"] == "word_by_word"
        assert info["narration_id"] == 1
        assert info["score_threshold"] == 0.5
        assert info["words"] == [{
            "chapter_number": 1,
            "verse_number": 2,
            "word_number": 3,
            "expected_text": "ٱلْحَمْدُ",
            "detected_text": "الحمد",
            "status": "correct",
            "total_score": 0.877,  # rounded to 3 decimals
            "start_time": 1235,    # seconds -> integer milliseconds
            "end_time": 1988,
        }]
        # start_time / end_time are integer milliseconds, not float seconds
        assert isinstance(info["words"][0]["start_time"], int)
        assert isinstance(info["words"][0]["end_time"], int)

    def test_verse_range_persists_across_rewrites(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.session_store.BASE_DIR", str(tmp_path))
        store = SessionStore(
            session_id="sess-range", start_chapter_number=1, start_verse_number=1,
            end_chapter_number=2, end_verse_number=5,
        )
        store.add_timeline_word(
            chapter_number=1, verse_number=1, word_number=0,
            expected_text="w", status="correct", total_score=1.0,
            start_time=0.0, end_time=0.5,
        )
        # info.json is rewritten on every word, so the range must survive the rewrite.
        info = _read_info(store)
        assert info["start_chapter_number"] == 1
        assert info["start_verse_number"] == 1
        assert info["end_chapter_number"] == 2
        assert info["end_verse_number"] == 5
        assert len(info["words"]) == 1

    def test_verse_range_defaults_to_null(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.session_store.BASE_DIR", str(tmp_path))
        store = SessionStore(session_id="sess-norange")
        info = _read_info(store)
        for key in ("start_chapter_number", "start_verse_number",
                    "end_chapter_number", "end_verse_number"):
            assert key in info and info[key] is None

    def test_words_accumulate_monotonically(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.session_store.BASE_DIR", str(tmp_path))
        store = SessionStore(session_id="sess-3", mode="word_by_word", score_threshold=0.5)
        spans = [(0.0, 0.5), (0.5, 1.1), (1.1, 1.6)]
        for i, (s, e) in enumerate(spans):
            store.add_timeline_word(
                chapter_number=1, verse_number=1, word_number=i,
                expected_text=f"w{i}", status="correct", total_score=1.0,
                start_time=s, end_time=e,
            )
        words = _read_info(store)["words"]
        assert len(words) == 3
        for w in words:
            assert w["end_time"] > w["start_time"]
        starts = [w["start_time"] for w in words]
        ends = [w["end_time"] for w in words]
        assert starts == sorted(starts)
        # Non-overlapping: each word ends at or before the next word starts.
        assert all(ends[i] <= starts[i + 1] for i in range(len(words) - 1))

    def test_duration_tracks_audio_and_is_finalized_on_close(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.session_store.BASE_DIR", str(tmp_path))
        store = SessionStore(session_id="sess-dur")
        assert _read_info(store)["duration"] == 0

        # 16000 frames = exactly 1s at 16kHz (mono 16-bit -> 2 bytes per frame).
        store.append_audio(b"\x00\x00" * 16000)
        # Audio alone doesn't rewrite info.json, but the counter has moved.
        assert store.duration == 1000

        # close_audio() re-flushes, so the final duration lands on disk even though the
        # last word (and therefore the last _flush) came earlier.
        store.close_audio()
        assert _read_info(store)["duration"] == 1000

    def test_wav_format_and_frame_count(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.session_store.BASE_DIR", str(tmp_path))
        store = SessionStore(session_id="sess-4")
        # 1600 samples of 16-bit PCM (0.1s at 16kHz) = 3200 bytes.
        pcm = b"\x00\x00" * 1600
        store.append_audio(pcm)
        store.append_audio(pcm)
        store.close_audio()
        with wave.open(store.recording_file, "rb") as w:
            assert w.getnchannels() == 1
            assert w.getsampwidth() == 2
            assert w.getframerate() == config.audio_sample_rate
            assert w.getnframes() == 3200  # 2 * 1600 samples, one WAV per session
