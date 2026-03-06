"""Tests for verse_detection module."""
import numpy as np
import pytest

from backend.verse_detection import detect_start_verse, _build_verse_candidates


# Sample word list mimicking quran_data.get_words() output for Al-Fatiha (7 verses)
SAMPLE_WORDS = [
    # Verse 1: بِسْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ
    {"surah": 1, "ayah": 1, "word_index": 1, "emlaey_text": "بسم", "uthmani_text": "بِسْمِ"},
    {"surah": 1, "ayah": 1, "word_index": 2, "emlaey_text": "الله", "uthmani_text": "ٱللَّهِ"},
    {"surah": 1, "ayah": 1, "word_index": 3, "emlaey_text": "الرحمن", "uthmani_text": "ٱلرَّحْمَٰنِ"},
    {"surah": 1, "ayah": 1, "word_index": 4, "emlaey_text": "الرحيم", "uthmani_text": "ٱلرَّحِيمِ"},
    # Verse 2: الْحَمْدُ لِلَّهِ رَبِّ الْعَالَمِينَ
    {"surah": 1, "ayah": 2, "word_index": 1, "emlaey_text": "الحمد", "uthmani_text": "ٱلْحَمْدُ"},
    {"surah": 1, "ayah": 2, "word_index": 2, "emlaey_text": "لله", "uthmani_text": "لِلَّهِ"},
    {"surah": 1, "ayah": 2, "word_index": 3, "emlaey_text": "رب", "uthmani_text": "رَبِّ"},
    {"surah": 1, "ayah": 2, "word_index": 4, "emlaey_text": "العالمين", "uthmani_text": "ٱلْعَٰلَمِينَ"},
    # Verse 3: الرَّحْمَنِ الرَّحِيمِ
    {"surah": 1, "ayah": 3, "word_index": 1, "emlaey_text": "الرحمن", "uthmani_text": "ٱلرَّحْمَٰنِ"},
    {"surah": 1, "ayah": 3, "word_index": 2, "emlaey_text": "الرحيم", "uthmani_text": "ٱلرَّحِيمِ"},
    # Verse 4: مَالِكِ يَوْمِ الدِّينِ
    {"surah": 1, "ayah": 4, "word_index": 1, "emlaey_text": "مالك", "uthmani_text": "مَٰلِكِ"},
    {"surah": 1, "ayah": 4, "word_index": 2, "emlaey_text": "يوم", "uthmani_text": "يَوْمِ"},
    {"surah": 1, "ayah": 4, "word_index": 3, "emlaey_text": "الدين", "uthmani_text": "ٱلدِّينِ"},
]


class TestBuildVerseCandidates:
    """Test the verse candidate builder."""

    def test_builds_correct_candidates(self):
        candidates = _build_verse_candidates(SAMPLE_WORDS, 1, 4, n_words=2)
        assert len(candidates) == 4
        assert 1 in candidates
        assert 2 in candidates
        assert 3 in candidates
        assert 4 in candidates
        # Check first 2 words of each verse
        assert candidates[1][0] == "بسم الله"
        assert candidates[2][0] == "الحمد لله"
        assert candidates[3][0] == "الرحمن الرحيم"
        assert candidates[4][0] == "مالك يوم"

    def test_word_indices(self):
        candidates = _build_verse_candidates(SAMPLE_WORDS, 1, 4, n_words=2)
        assert candidates[1][1] == 0   # verse 1 starts at index 0
        assert candidates[2][1] == 4   # verse 2 starts at index 4
        assert candidates[3][1] == 8   # verse 3 starts at index 8
        assert candidates[4][1] == 10  # verse 4 starts at index 10

    def test_partial_range(self):
        candidates = _build_verse_candidates(SAMPLE_WORDS, 2, 3, n_words=2)
        assert len(candidates) == 2
        assert 1 not in candidates
        assert 2 in candidates
        assert 3 in candidates

    def test_single_word_verse(self):
        """Verse with fewer words than n_words should still produce a candidate."""
        words = [
            {"surah": 1, "ayah": 1, "word_index": 1, "emlaey_text": "قل", "uthmani_text": "قُلْ"},
        ]
        candidates = _build_verse_candidates(words, 1, 1, n_words=2)
        assert len(candidates) == 1
        assert candidates[1][0] == "قل"


class TestDetectStartVerse:
    """Test detect_start_verse with mocked wav2vec2 decoding."""

    def test_exact_match_verse1(self, monkeypatch):
        monkeypatch.setattr(
            "backend.verse_detection._decode_audio",
            lambda _: "بسم الله",
        )
        audio = np.zeros(16000, dtype=np.float32)
        result = detect_start_verse(audio, SAMPLE_WORDS, 1, 4)
        assert result is not None
        ayah, word_index, score = result
        assert ayah == 1
        assert word_index == 0
        assert score >= 0.6

    def test_exact_match_verse4(self, monkeypatch):
        monkeypatch.setattr(
            "backend.verse_detection._decode_audio",
            lambda _: "مالك يوم",
        )
        audio = np.zeros(16000, dtype=np.float32)
        result = detect_start_verse(audio, SAMPLE_WORDS, 1, 4)
        assert result is not None
        ayah, word_index, score = result
        assert ayah == 4
        assert word_index == 10

    def test_best_match_among_candidates(self, monkeypatch):
        """Slightly imperfect match should still find the best candidate."""
        monkeypatch.setattr(
            "backend.verse_detection._decode_audio",
            lambda _: "الحمد لله",  # exact match for verse 2
        )
        audio = np.zeros(16000, dtype=np.float32)
        result = detect_start_verse(audio, SAMPLE_WORDS, 1, 4)
        assert result is not None
        assert result[0] == 2  # ayah 2

    def test_below_threshold(self, monkeypatch):
        """Completely unrelated speech should fail detection."""
        monkeypatch.setattr(
            "backend.verse_detection._decode_audio",
            lambda _: "كلام عشوائي تماما",  # random unrelated text
        )
        audio = np.zeros(16000, dtype=np.float32)
        result = detect_start_verse(audio, SAMPLE_WORDS, 1, 4)
        assert result is None

    def test_empty_decoded_text(self, monkeypatch):
        monkeypatch.setattr(
            "backend.verse_detection._decode_audio",
            lambda _: "",
        )
        audio = np.zeros(16000, dtype=np.float32)
        result = detect_start_verse(audio, SAMPLE_WORDS, 1, 4)
        assert result is None

    def test_single_verse_range(self, monkeypatch):
        monkeypatch.setattr(
            "backend.verse_detection._decode_audio",
            lambda _: "مالك يوم",
        )
        audio = np.zeros(16000, dtype=np.float32)
        result = detect_start_verse(audio, SAMPLE_WORDS, 4, 4)
        assert result is not None
        assert result[0] == 4

    def test_verse_outside_range_not_matched(self, monkeypatch):
        """Speech matching verse 1 should not match when range is 3-4."""
        monkeypatch.setattr(
            "backend.verse_detection._decode_audio",
            lambda _: "بسم الله",
        )
        audio = np.zeros(16000, dtype=np.float32)
        result = detect_start_verse(audio, SAMPLE_WORDS, 3, 4)
        # Should either be None or match a different verse, not verse 1
        if result is not None:
            assert result[0] != 1
