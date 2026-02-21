import pytest

from backend.config import config
from backend.scorer import (
    strip_diacritics,
    extract_diacritics,
    compute_char_score,
    compute_diacritic_score,
    compute_total_score,
    correct_word,
    score_word,
    align_multi_word,
)


class TestStripDiacritics:
    def test_strips_tashkeel(self):
        assert strip_diacritics("بِسْمِ") == "بسم"

    def test_no_diacritics(self):
        assert strip_diacritics("بسم") == "بسم"

    def test_empty(self):
        assert strip_diacritics("") == ""


class TestExtractDiacritics:
    def test_extracts(self):
        result = extract_diacritics("بِسْمِ")
        assert len(result) == 3  # kasra, sukun, kasra

    def test_no_diacritics(self):
        assert extract_diacritics("بسم") == ""


class TestCharScore:
    def test_identical(self):
        assert compute_char_score("بِسْمِ", "بِسْمِ") == 1.0

    def test_identical_stripped(self):
        assert compute_char_score("بِسْمِ", "بسم") == 1.0

    def test_partial_match(self):
        score = compute_char_score("بِسْمِ", "بسن")
        assert 0.0 < score < 1.0

    def test_completely_different(self):
        score = compute_char_score("بِسْمِ", "كتب")
        assert score < 0.5

    def test_empty_expected(self):
        assert compute_char_score("", "") == 1.0
        assert compute_char_score("", "بسم") == 0.0


class TestDiacriticScore:
    def test_identical(self):
        assert compute_diacritic_score("بِسْمِ", "بِسْمِ") == 1.0

    def test_different_diacritics(self):
        score = compute_diacritic_score("بِسْمِ", "بُسْمِ")
        assert 0.0 < score < 1.0

    def test_no_diacritics_expected(self):
        assert compute_diacritic_score("بسم", "بسم") == 1.0


class TestTotalScore:
    def test_weighted(self):
        score = compute_total_score(1.0, 0.5)
        assert score == pytest.approx(0.6 * 1.0 + 0.4 * 0.5)

    def test_perfect(self):
        assert compute_total_score(1.0, 1.0) == pytest.approx(1.0)

    def test_zero(self):
        assert compute_total_score(0.0, 0.0) == pytest.approx(0.0)

    def test_acoustic_none_unchanged(self):
        assert compute_total_score(1.0, 0.5, None) == pytest.approx(
            compute_total_score(1.0, 0.5)
        )

    def test_acoustic_included_when_enabled(self, monkeypatch):
        monkeypatch.setattr(config, "enable_acoustic_score", True)
        monkeypatch.setattr(config, "weight_char", 0.5)
        monkeypatch.setattr(config, "weight_diacritic", 0.2)
        monkeypatch.setattr(config, "weight_acoustic", 0.3)
        score = compute_total_score(0.0, 0.0, 1.0)
        assert score == pytest.approx(0.3)
        score = compute_total_score(1.0, 1.0, 1.0)
        assert score == pytest.approx(0.5 + 0.2 + 0.3)


class TestCorrectWord:
    def test_exact_match_returns_expected(self):
        assert correct_word("بِسْمِ", "بِسْمِ", 2) == "بِسْمِ"

    def test_one_edit_within_budget_returns_expected(self):
        assert correct_word("بِسْمِ", "بسن", 2) == "بِسْمِ"  # one substitution

    def test_over_budget_returns_transcribed(self):
        assert correct_word("بِسْمِ", "كتب", 2) == "كتب"

    def test_zero_edits_only_exact(self):
        assert correct_word("بسم", "بسم", 0) == "بسم"
        assert correct_word("بسم", "بسن", 0) == "بسن"


class TestScoreWord:
    def test_returns_all_fields(self):
        result = score_word("بِسْمِ", "بِسْمِ")
        assert "char_score" in result
        assert "diacritic_score" in result
        assert "total_score" in result
        assert result["char_score"] == 1.0
        assert result["total_score"] == 1.0


class TestAlignMultiWord:
    def test_exact_match(self):
        results = align_multi_word("بسم الله", ["بِسْمِ", "اللَّهِ"])
        assert len(results) == 2
        assert results[0]["status"] in ("correct", "incorrect")

    def test_fewer_transcribed(self):
        results = align_multi_word("بسم", ["بِسْمِ", "اللَّهِ"])
        assert len(results) == 2
        assert results[1]["total_score"] == 0.0
        assert results[1]["status"] == "incorrect"
