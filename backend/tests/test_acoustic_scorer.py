"""Tests for acoustic_scorer (optional wav2vec2-based acoustic score)."""
import numpy as np
import pytest

from backend.acoustic_scorer import _acoustic_score_single, get_acoustic_scores


class TestAcousticScoreSingle:
    """Test the single-word acoustic score helper (1 - CER on base letters + scored diacritics)."""

    def test_identical(self):
        assert _acoustic_score_single("بِسْمِ", "بِسْمِ") == 1.0

    def test_decoded_without_diacritics_scores_lower(self):
        # Decoded "بسم" has no diacritics; expected "بِسْمِ" has scored diacritics - score < 1.0
        s = _acoustic_score_single("بِسْمِ", "بسم")
        assert 0.0 < s < 1.0

    def test_non_scored_diacritics_ignored(self):
        # Tanween (ٍ) is stripped from both; same base + scored diacritics remain
        assert _acoustic_score_single("بِسْمٍ", "بِسْمٍ") == 1.0

    def test_sukoon_variant_normalized(self):
        # U+06E1 (ۡ) and U+0652 (ْ) are both sukoon; should compare equal
        assert _acoustic_score_single("بِسْمْ", "بِسْمۡ") == 1.0

    def test_in_range(self):
        s = _acoustic_score_single("بِسْمِ", "بسن")
        assert 0.0 <= s <= 1.0

    def test_empty_expected(self):
        assert _acoustic_score_single("", "") == 1.0
        assert _acoustic_score_single("", "بسم") == 0.0


class TestGetAcousticScores:
    """Test get_acoustic_scores with mocked decode so we don't load the model."""

    def test_returns_floats_in_range(self, monkeypatch):
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: "بسم الله",
        )
        audio = np.zeros(1600, dtype=np.float32)  # 0.1s at 16kHz
        expected = [("بِسْمِ", "بِسْمِ"), ("اللَّهِ", "اللَّهِ")]
        scores, n_decoded = get_acoustic_scores(audio, [], expected)
        assert len(scores) == 2
        assert n_decoded == 2
        for s in scores:
            assert isinstance(s, float)
            assert 0.0 <= s <= 1.0

    def test_best_match_alignment(self, monkeypatch):
        """Best-match aligns expected words to decoded words."""
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: "بِسْمِ اللَّهِ",
        )
        audio = np.zeros(1600, dtype=np.float32)
        expected = [("بِسْمِ", "بِسْمِ"), ("اللَّهِ", "اللَّهِ")]
        scores, n_decoded = get_acoustic_scores(audio, [], expected)
        assert len(scores) == 2
        assert n_decoded == 2
        assert scores[0] > 0.7  # بِسْمِ matches بِسْمِ
        assert scores[1] > 0.7  # اللَّهِ matches اللَّهِ

    def test_fallback_when_no_decoded_words(self, monkeypatch):
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: "",
        )
        audio = np.zeros(1600, dtype=np.float32)
        expected = [("واحد", "واحد"), ("اثنان", "اثنان")]
        scores, n_decoded = get_acoustic_scores(audio, [], expected)
        assert len(scores) == 2
        assert n_decoded == 0
        assert scores[0] == 0.5
        assert scores[1] == 0.5

    def test_extra_decoded_words_dont_crash(self, monkeypatch):
        """Model decodes more words than expected — should still work."""
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: "بِسْمِ الله الرحمن الرحيم",
        )
        audio = np.zeros(1600, dtype=np.float32)
        expected = [("بِسْمِ", "بِسْمِ")]
        scores, n_decoded = get_acoustic_scores(audio, [], expected)
        assert len(scores) == 1
        assert n_decoded == 4
        assert scores[0] > 0.7

    def test_empty_expected_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: "بسم",
        )
        audio = np.zeros(1600, dtype=np.float32)
        scores, n_decoded = get_acoustic_scores(audio, [], [])  # empty expected
        assert scores == []
        assert n_decoded == 0
