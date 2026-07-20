"""Tests for acoustic_scorer (optional wav2vec2-based acoustic score)."""
import numpy as np
import pytest

from backend.config import config
from backend.acoustic_scorer import (
    _acoustic_components,
    _acoustic_score_single,
    _align_decoded_to_expected,
    _merge_vocative,
    _merge_vocative_with_offsets,
    _normalize_text,
    _MATCH_FLOOR,
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


class TestNormalizeText:
    """Spelling-only marks are folded/stripped so they don't affect the character score."""

    def test_uthmani_folds_to_emlaey_spelling(self):
        # ٱلرَّحۡمَٰنِ (alef-wasla + dagger-alef) normalizes to the plain الرَّحْمَنِ spelling
        assert _normalize_text("ٱلرَّحۡمَٰنِ") == "الرَّحْمَنِ"

    def test_folds_alef_wasla_to_alef(self):
        assert _normalize_text("ٱهدنا") == _normalize_text("اهدنا")

    def test_strips_tatweel(self):
        assert _normalize_text("مـن") == _normalize_text("من")

    def test_strips_dagger_alef(self):
        assert _normalize_text("هٰذا") == _normalize_text("هذا")

    def test_strips_quranic_waqf_and_annotation_marks(self):
        # Every mark in the U+06D6-U+06ED block (waqf/pause, end-of-ayah, sajdah, small-letter
        # guides) is dropped so it can't inflate the char comparison. U+06E1 is the one exception:
        # normalize_sukoon turns it into a standard sukoon, so it is preserved (see below).
        base = "هُ"  # هُ
        for cp in range(0x06D6, 0x06EE):
            if cp == 0x06E1:
                continue
            assert _normalize_text(base + chr(cp)) == _normalize_text(base), f"U+{cp:04X} not stripped"

    def test_alt_sukoon_head_preserved_as_standard_sukoon(self):
        # U+06E1 (alternate sukoon head) is normalized to U+0652, not stripped
        assert _normalize_text("هۡ") == "هْ"

    def test_waqf_mark_does_not_reduce_char_score(self):
        # A reference carrying a trailing waqf mark still matches a decode without it (char 1.0),
        # and the scored diacritic next to the mark is kept.
        cs, _ = _acoustic_components("هُۚ", "هُ")  # هُ + waqf  vs  هُ
        assert cs == pytest.approx(1.0)
        assert "ُ" in _normalize_text("هُۚ")


class TestGetAcousticScores:
    """Test get_acoustic_scores with mocked decode so we don't load the model."""

    def test_returns_floats_in_range(self, monkeypatch):
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: ("بسم الله", []),
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
            lambda _: ("بِسْمِ اللَّهِ", []),
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
            lambda _: ("", []),
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
            lambda _: ("بِسْمِ الله الرحمن الرحيم", []),
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
            lambda _: ("بسم", []),
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
            lambda _: ("يا أَيُّهَا", []),
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
            lambda _: ("الْحَمْدُ لِلَّهِ", []),
        )
        audio = np.zeros(1600, dtype=np.float32)
        expected = [("الْحَمْدُ", "الْحَمْدُ"), ("لِلَّهِ", "لِلَّهِ")]
        res = get_acoustic_scores(audio, [], expected)
        assert res.n_decoded == 2
        assert res.best_words == ["الْحَمْدُ", "لِلَّهِ"]

    def test_orthographic_marks_dont_penalize_score(self, monkeypatch):
        """Alef-wasla, tatweel and dagger-alef are spelling-only; a decode differing only by
        those marks scores a perfect char match instead of the buggy 0.714/0.786."""
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: ("الرَّحْمَـٰنِ", []),  # plain alef + tatweel + dagger-alef
        )
        audio = np.zeros(1600, dtype=np.float32)
        expected = [("الرَّحْمَنِ", "ٱلرَّحۡمَٰنِ")]  # emlaey, uthmani (alef-wasla + dagger-alef)
        res = get_acoustic_scores(audio, [], expected)
        assert res.char_scores[0] == pytest.approx(1.0)
        assert res.scores[0] == pytest.approx(1.0)

    def test_repeated_word_does_not_desync_current_chunk(self, monkeypatch):
        """Regression: a word repeated earlier in the utterance ('في' twice here) used to drift
        the greedy forward pointer so later correctly-recited words ('كبيرا', 'فاذا', 'جاء')
        matched the wrong token and scored ~0.15. The current chunk must align to its own tokens."""
        decoded = "في الارض في الكتاب ولتعلن علوا كبيرا فاذا جاء"
        monkeypatch.setattr("backend.acoustic_scorer._decode_audio", lambda _: (decoded, []))
        audio = np.zeros(1600, dtype=np.float32)
        previous = [(w, w) for w in ["في", "الارض", "في", "الكتاب", "ولتعلن", "علوا"]]
        expected = [(w, w) for w in ["كبيرا", "فاذا", "جاء"]]
        res = get_acoustic_scores(audio, previous, expected)
        # Sliced to the current chunk; each word maps to its own decoded token.
        assert res.best_words == ["كبيرا", "فاذا", "جاء"]
        assert all(s > 0.7 for s in res.scores)


class TestAlignDecodedToExpected:
    """The monotonic (Needleman-Wunsch) alignment that replaced the greedy forward matcher."""

    def test_clean_diagonal(self):
        words = ["الف", "باء", "تاء"]
        expected = [(w, w) for w in words]
        scores, _, _, best, _ = _align_decoded_to_expected(words, expected)
        assert best == words
        assert all(s == pytest.approx(1.0) for s in scores)

    def test_repeated_reference_words_align_positionally(self):
        # "في" appears twice; each expected occurrence maps to its own decoded token instead of
        # collapsing onto one (the old greedy matcher's leapfrog/stick failure).
        decoded = ["في", "الارض", "في", "الكتاب"]
        expected = [(w, w) for w in decoded]
        scores, _, _, best, _ = _align_decoded_to_expected(decoded, expected)
        assert best == decoded
        assert all(s == pytest.approx(1.0) for s in scores)

    def test_missing_middle_token_leaves_word_unmatched_without_shifting(self):
        # The middle word wasn't decoded. A monotonic alignment leaves THAT word unmatched and
        # still lines the last word up with its own token -- it must not shift over by one.
        expected = [(w, w) for w in ["اول", "وسط", "اخر"]]
        scores, _, _, best, _ = _align_decoded_to_expected(["اول", "اخر"], expected)
        assert best == ["اول", "", "اخر"]
        assert scores[0] == pytest.approx(1.0)
        assert scores[1] == 0.0
        assert scores[2] == pytest.approx(1.0)

    def test_below_floor_token_is_unmatched(self):
        # A decoded token too dissimilar to the expected word is left unmatched (best_word ""),
        # which drives the caller's noise/silence guard instead of a false low-score "incorrect".
        scores, _, _, best, _ = _align_decoded_to_expected(["زقز"], [("بِسْمِ", "بِسْمِ")])
        assert best == [""]
        assert scores[0] == 0.0

    def test_extra_tokens_are_skipped(self):
        decoded = ["الف", "باء", "تاء"]
        scores, _, _, best, _ = _align_decoded_to_expected(decoded, [("باء", "باء")])
        assert best == ["باء"]
        assert scores[0] == pytest.approx(1.0)

    def test_duplicate_expected_single_token_not_double_counted(self):
        # Same word expected twice but recited once: exactly one occurrence matches the token.
        scores, _, _, best, _ = _align_decoded_to_expected(["مثل"], [("مثل", "مثل"), ("مثل", "مثل")])
        assert best.count("مثل") == 1
        assert best.count("") == 1

    def test_match_floor_is_the_advance_gate_value(self):
        # Guards the intended floor; alignment treats blends below this as no-match.
        assert _MATCH_FLOOR == 0.4


class TestWordOffsets:
    """CTC word offsets flow through decode -> merge -> align -> AcousticResult.offsets.

    These per-word (start_sec, end_sec) spans (relative to the decoded segment) are what
    the session timeline uses to place each spoken word inside recording.wav.
    """

    def test_merge_vocative_with_offsets_fuses_spans(self):
        parts = ["يا", "أَيُّهَا", "الناس"]
        offs = [(0.0, 0.2), (0.2, 0.5), (0.5, 0.9)]
        merged, merged_off = _merge_vocative_with_offsets(parts, offs)
        assert merged == ["ياأَيُّهَا", "الناس"]
        # A fused span runs from the first token's start to the second token's end.
        assert merged_off == [(0.0, 0.5), (0.5, 0.9)]

    def test_merge_vocative_with_offsets_none_passthrough(self):
        merged, merged_off = _merge_vocative_with_offsets(["الناس"], None)
        assert merged == ["الناس"]
        assert merged_off is None

    def test_align_returns_offset_of_matched_token(self):
        expected = [(w, w) for w in ["اول", "وسط", "اخر"]]
        decoded = ["اول", "اخر"]
        decoded_offsets = [(0.0, 0.4), (0.7, 1.1)]
        _, _, _, best, offsets = _align_decoded_to_expected(decoded, expected, decoded_offsets)
        assert best == ["اول", "", "اخر"]
        # Matched words carry their token's offset; the unmatched middle word is None.
        assert offsets == [(0.0, 0.4), None, (0.7, 1.1)]

    def test_align_without_offsets_returns_none_list(self):
        _, _, _, _, offsets = _align_decoded_to_expected(["الف"], [("الف", "الف")])
        assert offsets == [None]

    def test_get_acoustic_scores_propagates_offsets(self, monkeypatch):
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: ("بِسْمِ اللَّهِ", [(0.0, 0.5), (0.6, 1.2)]),
        )
        audio = np.zeros(1600, dtype=np.float32)
        expected = [("بِسْمِ", "بِسْمِ"), ("اللَّهِ", "اللَّهِ")]
        res = get_acoustic_scores(audio, [], expected)
        assert res.offsets == [(0.0, 0.5), (0.6, 1.2)]

    def test_get_acoustic_scores_offsets_sliced_to_chunk(self, monkeypatch):
        # previous_words are dropped from the returned offsets, like the other lists.
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: ("الف باء تاء", [(0.0, 0.3), (0.4, 0.7), (0.8, 1.0)]),
        )
        audio = np.zeros(1600, dtype=np.float32)
        previous = [("الف", "الف")]
        expected = [("باء", "باء"), ("تاء", "تاء")]
        res = get_acoustic_scores(audio, previous, expected)
        assert res.offsets == [(0.4, 0.7), (0.8, 1.0)]

    def test_get_acoustic_scores_mismatched_offsets_ignored(self, monkeypatch):
        # Offsets that don't line up 1:1 with the decoded words are dropped, not misapplied.
        monkeypatch.setattr(
            "backend.acoustic_scorer._decode_audio",
            lambda _: ("بِسْمِ اللَّهِ", [(0.0, 0.5)]),  # one offset for two words
        )
        audio = np.zeros(1600, dtype=np.float32)
        expected = [("بِسْمِ", "بِسْمِ"), ("اللَّهِ", "اللَّهِ")]
        res = get_acoustic_scores(audio, [], expected)
        assert res.offsets == [None, None]
