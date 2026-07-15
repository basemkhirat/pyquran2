import pytest

from backend.config import config
from backend.scorer import (
    strip_diacritics,
    extract_diacritics,
    extract_scored_diacritics,
    compute_char_score,
    compute_diacritic_score,
    compute_total_score,
    correct_word,
    score_word,
    score_word_best,
    align_multi_word,
    should_advance,
    should_skip_forward,
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


class TestExtractScoredDiacritics:
    def test_extracts_scored_only(self):
        result = extract_scored_diacritics("بِسْمِ")
        assert len(result) == 3  # kasra, sukun, kasra

    def test_ignores_tanween(self):
        # بسمٍ has kasratan (ً) at end - non-scored, should be ignored
        result = extract_scored_diacritics("بِسْمٍ")
        assert result == "ِْ"  # kasra, sukun only (no kasratan)

    def test_no_diacritics(self):
        assert extract_scored_diacritics("بسم") == ""


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

    def test_non_scored_diacritics_ignored(self):
        # Tanween (ً ٌ ٍ) is non-scored; both have same scored diacritics (kasra, sukun)
        assert compute_diacritic_score("بِسْمٍ", "بِسْمٍ") == 1.0

    def test_sukoon_variant_normalized(self):
        # U+06E1 (ۡ) and U+0652 (ْ) are both sukoon; should compare equal
        assert compute_diacritic_score("بِسْمْ", "بِسْمۡ") == 1.0


class TestTotalScore:
    def test_weighted(self, monkeypatch):
        monkeypatch.setattr(config, "weight_char", 0.6)
        monkeypatch.setattr(config, "weight_diacritic", 0.4)
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
        monkeypatch.setattr(config, "enable_text_score", True)
        monkeypatch.setattr(config, "enable_acoustic_score", True)
        monkeypatch.setattr(config, "weight_text", 0.7)
        monkeypatch.setattr(config, "weight_char", 0.5)
        monkeypatch.setattr(config, "weight_diacritic", 0.2)
        monkeypatch.setattr(config, "weight_acoustic", 0.3)
        score = compute_total_score(0.0, 0.0, 1.0)
        assert score == pytest.approx(0.3)
        score = compute_total_score(1.0, 1.0, 1.0)
        # total = weight_text * text_score + weight_acoustic * acoustic_score
        assert score == pytest.approx(0.7 * (0.5 + 0.2) + 0.3 * 1.0)


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


class TestScoreWordBest:
    def test_returns_best_of_emlaey_uthmani(self):
        # When emlaey and uthmani are same, same as score_word
        result = score_word_best("بِسْمِ", "بِسْمِ", "بِسْمِ", 2)
        assert result["char_score"] == 1.0
        assert result["diacritic_score"] == 1.0
        assert "t_corrected" in result

    def test_takes_best_when_variants_differ(self):
        # Best score is max of emlaey and uthmani; t_corrected from better variant
        result = score_word_best("بِسْمِ", "بِسۡمِ", "بِسْمِ", 2)  # emlaey uses ْ, uthmani may use ۡ
        assert result["char_score"] == 1.0
        assert result["diacritic_score"] == 1.0
        assert result["t_corrected"] in ("بِسْمِ", "بِسۡمِ")


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


class TestShouldAdvance:
    def test_word_by_word_advances_only_on_correct(self):
        assert should_advance("correct", "word_by_word") is True
        assert should_advance("incorrect", "word_by_word") is False

    def test_continuous_always_advances(self):
        assert should_advance("correct", "continuous") is True
        assert should_advance("incorrect", "continuous") is True


class TestShouldSkipForward:
    """Advancing past an expected word that got no acoustic match (reciter substituted/skipped)."""

    THRESHOLD = 0.76

    def test_confident_later_pass_skips_forward(self):
        # A later word recited correctly (>= threshold) is evidence the reciter moved on -> advance.
        assert should_skip_forward("continuous", [0.0, 0.95, 0.0], self.THRESHOLD, False) is True

    def test_weak_later_match_waits_on_interim(self):
        # A weak later match (below threshold) on an interim decode = decode still catching up;
        # don't confirm the word wrong yet — wait.
        assert should_skip_forward("continuous", [0.0, 0.45, 0.0], self.THRESHOLD, False) is False

    def test_weak_later_match_advances_on_final(self):
        # On the final pass there's no more audio; any later match is enough to avoid sticking.
        assert should_skip_forward("continuous", [0.0, 0.45, 0.0], self.THRESHOLD, True) is True

    def test_no_later_match_stays(self):
        assert should_skip_forward("continuous", [0.0, 0.0], self.THRESHOLD, False) is False
        assert should_skip_forward("continuous", [0.0, 0.0], self.THRESHOLD, True) is False
        assert should_skip_forward("continuous", [], self.THRESHOLD, True) is False

    def test_word_by_word_never_skips_forward(self):
        # In word_by_word mode the reciter repeats the word; never auto-advance on a miss.
        assert should_skip_forward("word_by_word", [0.95], self.THRESHOLD, True) is False
