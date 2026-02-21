"""Tests for acoustic_scorer (optional wav2vec2-based acoustic score)."""
import numpy as np
import pytest

from backend.acoustic_scorer import _acoustic_score_single, get_acoustic_scores


class TestAcousticScoreSingle:
    """Test the single-word acoustic score helper (1 - CER on stripped diacritics)."""

    def test_identical(self):
        assert _acoustic_score_single("بِسْمِ", "بِسْمِ") == 1.0

    def test_identical_stripped(self):
        assert _acoustic_score_single("بِسْمِ", "بسم") == 1.0

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
        scores = get_acoustic_scores(audio, ["بِسْمِ", "اللَّهِ"])
        assert len(scores) == 2
        for s in scores:
            assert isinstance(s, float)
            assert 0.0 <= s <= 1.0

    def test_fallback_when_decoded_words_fewer_than_expected(self, monkeypatch):
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: "واحد",
        )
        audio = np.zeros(1600, dtype=np.float32)
        scores = get_acoustic_scores(audio, ["واحد", "اثنان"])
        assert len(scores) == 2
        assert scores[1] == 0.5  # fallback

    def test_empty_expected_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: "بسم",
        )
        audio = np.zeros(1600, dtype=np.float32)
        assert get_acoustic_scores(audio, []) == []
