"""Tests for acoustic_scorer (optional wav2vec2-based acoustic score)."""
import numpy as np
import pytest

from backend.config import config
from backend.acoustic_scorer import (
    _acoustic_components,
    _acoustic_score_single,
    _merge_vocative,
    get_acoustic_scores,
)


class TestAcousticScoreSingle:
    """Test the single-word acoustic score: WEIGHT_CHAR * char + WEIGHT_DIACRITIC * diacritic."""

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
        # char component 0.0 (letters present but nothing expected); diacritic component 1.0
        # (decoded has no diacritics) -> blend collapses to WEIGHT_DIACRITIC.
        assert _acoustic_score_single("", "بسم") == pytest.approx(config.weight_diacritic)

    def test_weighted_blend_of_char_and_diacritic(self):
        # Correct letters but all diacritics dropped: char ~1.0, diacritic ~0.0
        cs, ds = _acoustic_components("بِسْمِ", "بسم")
        assert cs == 1.0
        assert ds == 0.0
        blended = _acoustic_score_single("بِسْمِ", "بسم")
        assert blended == pytest.approx(config.weight_char * cs + config.weight_diacritic * ds)
        # With default weights (0.75/0.25) the score is dominated by WEIGHT_CHAR
        assert blended == pytest.approx(config.weight_char)


class TestMergeVocative:
    """Re-fuse a standalone vocative 'يا' onto the following decoded token."""

    def test_merges_ya_into_next(self):
        assert _merge_vocative(["يا", "أَيُّهَا"]) == ["ياأَيُّهَا"]

    def test_merges_diacritized_ya(self):
        # يا carrying a fatha still counts as the bare vocative once diacritics are stripped
        assert _merge_vocative(["يَا", "أيها"]) == ["يَاأيها"]

    def test_trailing_ya_left_alone(self):
        # Nothing follows the يا -> nothing to fuse
        assert _merge_vocative(["يا"]) == ["يا"]

    def test_non_vocative_unchanged(self):
        assert _merge_vocative(["الناس"]) == ["الناس"]

    def test_only_ya_merges_rest_untouched(self):
        assert _merge_vocative(["يا", "أيها", "الناس"]) == ["ياأيها", "الناس"]

    def test_empty(self):
        assert _merge_vocative([]) == []


class TestGetAcousticScores:
    """Test get_acoustic_scores with mocked decode so we don't load the model."""

    def test_returns_floats_in_range(self, monkeypatch):
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: "بسم الله",
        )
        audio = np.zeros(1600, dtype=np.float32)  # 0.1s at 16kHz
        expected = [("بِسْمِ", "بِسْمِ"), ("اللَّهِ", "اللَّهِ")]
        res = get_acoustic_scores(audio, [], expected)
        assert len(res.scores) == 2
        assert res.n_decoded == 2
        for s in res.scores:
            assert isinstance(s, float)
            assert 0.0 <= s <= 1.0
        # char/diacritic sub-scores are populated in parallel and in range
        assert len(res.char_scores) == 2
        assert len(res.diac_scores) == 2
        for cs, ds in zip(res.char_scores, res.diac_scores):
            assert 0.0 <= cs <= 1.0
            assert 0.0 <= ds <= 1.0

    def test_best_match_alignment(self, monkeypatch):
        """Best-match aligns expected words to decoded words."""
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: "بِسْمِ اللَّهِ",
        )
        audio = np.zeros(1600, dtype=np.float32)
        expected = [("بِسْمِ", "بِسْمِ"), ("اللَّهِ", "اللَّهِ")]
        res = get_acoustic_scores(audio, [], expected)
        assert len(res.scores) == 2
        assert res.n_decoded == 2
        assert res.scores[0] > 0.7  # بِسْمِ matches بِسْمِ
        assert res.scores[1] > 0.7  # اللَّهِ matches اللَّهِ
        # best_words is the raw decoded word that matched each expected word
        assert res.best_words == ["بِسْمِ", "اللَّهِ"]

    def test_fallback_when_no_decoded_words(self, monkeypatch):
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: "",
        )
        audio = np.zeros(1600, dtype=np.float32)
        expected = [("واحد", "واحد"), ("اثنان", "اثنان")]
        res = get_acoustic_scores(audio, [], expected)
        assert len(res.scores) == 2
        assert res.n_decoded == 0
        assert res.scores == [0.5, 0.5]
        assert res.char_scores == [0.5, 0.5]
        assert res.diac_scores == [0.5, 0.5]

    def test_extra_decoded_words_dont_crash(self, monkeypatch):
        """Model decodes more words than expected — should still work."""
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: "بِسْمِ الله الرحمن الرحيم",
        )
        audio = np.zeros(1600, dtype=np.float32)
        expected = [("بِسْمِ", "بِسْمِ")]
        res = get_acoustic_scores(audio, [], expected)
        assert len(res.scores) == 1
        assert res.n_decoded == 4
        assert res.scores[0] > 0.7

    def test_empty_expected_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: "بسم",
        )
        audio = np.zeros(1600, dtype=np.float32)
        res = get_acoustic_scores(audio, [], [])  # empty expected
        assert res.scores == []
        assert res.char_scores == []
        assert res.diac_scores == []
        assert res.best_words == []
        assert res.n_decoded == 0

    def test_vocative_split_is_refused_and_scored_high(self, monkeypatch):
        """wav2vec2 splits the fused vocative into 'يا' + 'أَيُّهَا'; merging them back lets the
        single reference word (emlaey يَاأَيُّهَا / uthmani يَٰٓأَيُّهَا) score as correct."""
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: "يا أَيُّهَا",
        )
        audio = np.zeros(1600, dtype=np.float32)
        expected = [("يَاأَيُّهَا", "يَٰٓأَيُّهَا")]
        res = get_acoustic_scores(audio, [], expected)
        # The two decoded tokens are fused into one, so only the recited word is counted...
        assert res.n_decoded == 1
        assert res.best_words == ["ياأَيُّهَا"]
        # ...and it now clears the pass/fail threshold instead of the buggy 0.70.
        assert res.scores[0] >= 0.76

    def test_non_vocative_two_words_not_merged(self, monkeypatch):
        """A decode with no standalone 'يا' is untouched: still two tokens, aligned per-word."""
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: "الْحَمْدُ لِلَّهِ",
        )
        audio = np.zeros(1600, dtype=np.float32)
        expected = [("الْحَمْدُ", "الْحَمْدُ"), ("لِلَّهِ", "لِلَّهِ")]
        res = get_acoustic_scores(audio, [], expected)
        assert res.n_decoded == 2
        assert res.best_words == ["الْحَمْدُ", "لِلَّهِ"]
