"""Tests for the muaalem acoustic backend.

The model and quran_transcript are never loaded: `_phonetize`, `_run_muaalem` and `_explain`
are the only functions that touch them, and every test stubs those. What is exercised is the
attribution/scoring algorithm, which is pure.
"""
import numpy as np
import pytest

from backend import acoustic_muaalem as am
from backend.acoustic_scorer import WordError
from backend.config import config


AUDIO = np.zeros(1600, dtype=np.float32)  # 0.1s at 16kHz; never actually decoded


def _meta(surah, ayah, word_index):
    return {"surah": surah, "ayah": ayah, "word_index": word_index}


def _err(error_type="normal", speech="delete", predicted_ph="", expected_ph=""):
    return WordError(
        error_type=error_type,
        speech_error_type=speech,
        expected_ph=expected_ph,
        predicted_ph=predicted_ph,
    )


class _FakeRef:
    phonemes = "PHONEMES"
    mappings = []


class _FakeOut:
    class phonemes:
        text = "PREDICTED"


class TestWordSpans:
    def test_single_word(self):
        assert am._word_spans(["abc"]) == [(0, 3)]

    def test_multiple_words_account_for_spaces(self):
        assert am._word_spans(["ab", "cde", "f"]) == [(0, 2), (3, 6), (7, 8)]

    def test_spans_index_the_joined_string(self):
        words = ["بِسْمِ", "ٱللَّهِ"]
        joined = " ".join(words)
        for w, (s, e) in zip(words, am._word_spans(words)):
            assert joined[s:e] == w

    def test_empty(self):
        assert am._word_spans([]) == []


class TestOverlap:
    def test_disjoint(self):
        assert am._overlap((0, 3), (5, 8)) == 0

    def test_touching_is_not_overlap(self):
        assert am._overlap((0, 3), (3, 8)) == 0

    def test_partial(self):
        assert am._overlap((0, 5), (3, 8)) == 2

    def test_contained(self):
        assert am._overlap((2, 4), (0, 10)) == 2


class TestLocateInsert:
    SPANS = [(0, 3), (4, 9), (10, 12)]  # "abc defgh ij"

    def test_inside_a_word(self):
        assert am._locate_insert(6, self.SPANS) == 1

    def test_at_word_end_charges_that_word(self):
        assert am._locate_insert(9, self.SPANS) == 1

    def test_in_the_gap_charges_preceding_word(self):
        # position 3 is the space after "abc"
        assert am._locate_insert(3, self.SPANS) == 0

    def test_before_first_word(self):
        assert am._locate_insert(0, self.SPANS) == 0

    def test_past_the_end_charges_last_word(self):
        assert am._locate_insert(99, self.SPANS) == 2

    def test_no_spans(self):
        assert am._locate_insert(0, []) is None


class TestAttributeErrors:
    def test_error_within_one_word(self):
        spans = [(0, 3), (4, 7)]
        errs = [_err(speech="replace")]
        per_word, penalties, deleted = am._attribute_errors(errs, [(0, 2)], spans)
        assert per_word[0] == errs
        assert per_word[1] == []
        assert penalties[0]["normal"] == 2
        assert deleted[0] == 0

    def test_error_straddling_two_words_charges_both(self):
        spans = [(0, 3), (4, 7)]
        errs = [_err(speech="delete")]
        per_word, penalties, deleted = am._attribute_errors(errs, [(1, 6)], spans)
        assert len(per_word[0]) == 1 and len(per_word[1]) == 1
        assert penalties[0]["normal"] == 2  # chars 1-2
        assert penalties[1]["normal"] == 2  # chars 4-5
        assert deleted == [2, 2]

    def test_insert_is_capped(self):
        spans = [(0, 3)]
        errs = [_err(speech="insert", predicted_ph="xxxxxxxxxx")]
        _pw, penalties, deleted = am._attribute_errors(errs, [(2, 2)], spans)
        assert penalties[0]["normal"] == am._INSERT_PENALTY_CAP
        assert deleted[0] == 0  # an insert is not a deletion

    def test_error_types_are_tracked_separately(self):
        spans = [(0, 5)]
        errs = [_err("normal", "replace"), _err("tashkeel", "replace"), _err("tajweed", "replace")]
        _pw, penalties, _d = am._attribute_errors(errs, [(0, 1), (1, 2), (2, 3)], spans)
        assert penalties[0] == {"normal": 1, "tashkeel": 1, "tajweed": 1}


class TestIsUnrecited:
    def test_fully_deleted_word_is_unrecited(self):
        assert am._is_unrecited([_err(speech="delete")], deleted=5, span_len=5) is True

    def test_partial_coverage_still_counts_when_all_deletes(self):
        # The real-world shape (measured on 1:1): deletes never cover an unspoken word
        # completely, because its non-phonemic characters are never inside a delete span.
        # ٱلرَّحْمَـٰنِ unspoken measures 9 deleted of 13 characters.
        errs = [_err(speech="delete") for _ in range(5)]
        assert am._is_unrecited(errs, deleted=9, span_len=13) is True

    def test_a_dropped_phoneme_is_not_an_unrecited_word(self):
        # One small delete in an otherwise-spoken word: far below the coverage floor.
        assert am._is_unrecited([_err(speech="delete")], deleted=1, span_len=13) is False

    def test_any_non_delete_error_means_the_word_was_attempted(self):
        # The model decoded *something* for this word, so it was spoken -- however badly.
        errs = [_err(speech="delete"), _err(speech="replace")]
        assert am._is_unrecited(errs, deleted=5, span_len=5) is False

    def test_clean_word_is_not_unrecited(self):
        assert am._is_unrecited([], deleted=0, span_len=5) is False

    def test_a_shortened_run_does_not_count_toward_the_coverage_floor(self):
        """The real إِيَّاكَ shape: decoded as يَااكَ, two errors, both nominally deletes.

        Measured spans are 2 chars for delete ءِ->'' and 3 for delete ييَ->'يَ'. Only the first
        decoded nothing, so coverage is 2/8 — under the floor. Counting both (5/8) called a
        correctly-aligned word unrecited and sent it to the rescue, which returned 0.125.
        """
        errs = [
            _err(speech="delete", expected_ph="ءِ", predicted_ph=""),
            _err("tashkeel", speech="delete", expected_ph="ييَ", predicted_ph="يَ"),
        ]
        assert am._is_unrecited(errs, deleted=2, span_len=8) is False

    def test_one_decoded_delete_does_not_rescue_a_word_never_reached(self):
        """ٱلْمُسْتَقِيمَ, measured while the reciter was still on 1:5:1.

        It picked up one incidental `delete مُ->م` among six empty deletes. The rule stays
        proportional so that single decoded delete cannot make an unreached word look attempted.
        """
        errs = [_err(speech="delete", expected_ph=x) for x in ("ل", "س", "تَ", "قِ", "ۦۦ", "مَ")]
        errs.append(_err(speech="delete", expected_ph="مُ", predicted_ph="م"))
        assert am._is_unrecited(errs, deleted=9, span_len=14) is True


class TestDeletedCoverage:
    def test_a_delete_that_decoded_nothing_counts(self):
        _pw, _pen, deleted = am._attribute_errors(
            [_err(speech="delete", expected_ph="ءِ")], [(0, 2)], [(0, 8)]
        )
        assert deleted[0] == 2

    def test_a_delete_that_decoded_something_does_not(self):
        # A shortened run is imprecise recitation, not reference characters that made no sound.
        _pw, _pen, deleted = am._attribute_errors(
            [_err(speech="delete", expected_ph="ييَ", predicted_ph="يَ")], [(2, 5)], [(0, 8)]
        )
        assert deleted[0] == 0


class TestGhunnahDuration:
    """A shortened ghunnah is a duration matter, so it is moved onto the tajweed axis.

    explain_error returns madd length differences as `tajweed` but ghunnah ones as `normal`,
    which charged a held-too-briefly ikhfa as a wrong letter (مِن → 0.62 in real sessions).
    """

    def _lib_err(self, error_type="normal", speech="insert", expected_ph="", predicted_ph=""):
        """A stand-in for quran_transcript's ReciterError (note *their* misspelled field)."""
        return type(
            "ReciterError", (), {
                "error_type": error_type,
                "speech_error_type": speech,
                "expected_ph": expected_ph,
                "preditected_ph": predicted_ph,
                "expected_len": None,
                "predicted_len": None,
                "ref_tajweed_rules": [],
            },
        )()

    @pytest.mark.parametrize("expected_ph, predicted_ph", [
        ("ںںں", "ں"),      # ikhfa held for one frame instead of three (the مِن قَبْلِكَ case)
        ("ںںں", ""),       # nothing nasal decoded at all
        ("ںںں", "ںں"),     # one frame short
        ("۾۾۾", "م"),      # ikhfa meem came back as a plain meem
        ("مممم", "مم"),    # idgham meem, half held
    ])
    def test_nasal_run_differences_become_tajweed(self, expected_ph, predicted_ph):
        err = am._to_word_error(self._lib_err(expected_ph=expected_ph, predicted_ph=predicted_ph))
        assert err.error_type == "tajweed"
        # The phonemes are passed through untouched, so the panel still shows what differed.
        assert (err.expected_ph, err.predicted_ph) == (expected_ph, predicted_ph)

    @pytest.mark.parametrize("expected_ph, predicted_ph", [
        ("ںںں", "سب"),     # a real letter substitution, nasal or not
        ("ں", "س"),         # single letter, not a held run
        ("قَب", "قَم"),      # ordinary word characters that merely contain a nasal
        ("ںںس", "ں"),      # not one repeated symbol
    ])
    def test_real_letter_errors_stay_normal(self, expected_ph, predicted_ph):
        err = am._to_word_error(self._lib_err(expected_ph=expected_ph, predicted_ph=predicted_ph))
        assert err.error_type == "normal"

    @pytest.mark.parametrize("error_type", ["tashkeel", "tajweed"])
    def test_a_type_the_library_already_assigned_is_left_alone(self, error_type):
        err = am._to_word_error(
            self._lib_err(error_type=error_type, expected_ph="ںںں", predicted_ph="ں")
        )
        assert err.error_type == error_type

    def test_the_word_no_longer_fails_on_a_short_ghunnah(self, monkeypatch):
        """End of the chain: tajweed carries weight 0 by default, so the word scores clean."""
        monkeypatch.setattr(config, "muaalem_weight_tajweed", 0.0)
        err = am._to_word_error(self._lib_err(expected_ph="ںںں", predicted_ph="ں"))
        per_word, penalties, _deleted = am._attribute_errors([err], [(1, 4)], [(0, 5)])
        total, cs, _ds, ts = am._score_word(penalties[0], span_len=5)
        assert per_word[0] == [err]
        assert total == 1.0 and cs == 1.0
        assert ts < 1.0            # still reported, so the تجويد tab can show it


class TestScoreWord:
    def test_clean_word_scores_one(self):
        total, cs, ds, ts = am._score_word({}, span_len=5)
        assert (total, cs, ds, ts) == (1.0, 1.0, 1.0, 1.0)

    def test_zero_length_span_does_not_divide_by_zero(self):
        # An empty span would make the penalty denominator 0; it is floored to 1, so the
        # penalised axis bottoms out rather than raising.
        total, cs, ds, _ts = am._score_word({"normal": 1}, span_len=0)
        assert cs == 0.0
        assert ds == 1.0          # untouched axis stays clean
        assert 0.0 <= total <= 1.0

    def test_penalty_is_clamped_never_negative(self):
        total, cs, _ds, _ts = am._score_word({"normal": 999}, span_len=5)
        assert cs == 0.0
        assert total >= 0.0

    def test_normal_errors_hit_char_score(self):
        _t, cs, ds, ts = am._score_word({"normal": 2}, span_len=4)
        assert cs == 0.5
        assert ds == 1.0 and ts == 1.0

    def test_tashkeel_errors_hit_diacritic_score(self):
        _t, cs, ds, ts = am._score_word({"tashkeel": 2}, span_len=4)
        assert ds == 0.5
        assert cs == 1.0 and ts == 1.0

    def test_tajweed_is_surfaced_but_does_not_lower_total_by_default(self, monkeypatch):
        monkeypatch.setattr(config, "muaalem_weight_tajweed", 0.0)
        total, cs, ds, ts = am._score_word({"tajweed": 4}, span_len=4)
        assert ts == 0.0          # the tajweed error is measured
        assert total == 1.0       # but it does not fail the word
        assert cs == 1.0 and ds == 1.0

    def test_tajweed_lowers_total_once_weighted(self, monkeypatch):
        monkeypatch.setattr(config, "muaalem_weight_tajweed", 0.5)
        total, *_ = am._score_word({"tajweed": 4}, span_len=4)
        assert total == pytest.approx(0.5)


class TestScoreIntegration:
    """End-to-end through MuaalemBackend.score with the library stubbed out."""

    # Five 5-character words -> spans (0,5) (6,11) (12,17) (18,23) (24,29). Long enough that
    # a one-character delete sits below the unrecited coverage floor, as real words do.
    WORDS = ["aaaaa", "bbbbb", "ccccc", "ddddd", "eeeee"]
    META = [_meta(1, 1, i + 1) for i in range(5)]
    EXPECTED = [(w, w) for w in WORDS]

    def _score(self, monkeypatch, errors_and_spans, previous=0):
        monkeypatch.setattr(config, "muaalem_context_pad_words", 0)
        monkeypatch.setattr(config, "muaalem_weight_tajweed", 0.0)
        monkeypatch.setattr(am, "_qt_verse_words", lambda s, a: tuple(self.WORDS))
        monkeypatch.setattr(am, "_phonetize", lambda text: _FakeRef())
        monkeypatch.setattr(am, "_run_muaalem", lambda w, r, sampling_rate: [_FakeOut()])

        # score() reads .uthmani_pos off whatever _explain returns, then hands it to
        # _to_word_error. Stub both so the test can speak in our own WordError type.
        class _Raw:
            def __init__(self, err, span):
                self.err = err
                self.uthmani_pos = span

        raw = [_Raw(e, sp) for e, sp in errors_and_spans]
        monkeypatch.setattr(am, "_explain", lambda *a, **k: raw)
        monkeypatch.setattr(am, "_to_word_error", lambda r: r.err)

        return am.MuaalemBackend().score(
            AUDIO,
            self.EXPECTED[:previous],
            self.EXPECTED[previous:],
            word_meta=self.META,
        )

    def test_clean_recitation_scores_all_words(self, monkeypatch):
        res = self._score(monkeypatch, [])
        assert res.scores == [1.0] * 5
        assert res.best_words == self.WORDS
        assert res.n_decoded == 5
        assert res.errors == [[], [], [], [], []]

    def test_delete_trap_unrecited_words_are_unmatched_not_wrong(self, monkeypatch):
        """The regression that matters most.

        Words 3-5 have not been recited yet, so explain_error reports them fully deleted.
        They must come back as unmatched (best_word "") -- the signal main.py's last_matched
        uses to stop -- rather than as five red zero-scored words on every streaming tick.
        """
        errs = [
            (_err(speech="delete"), (12, 17)),
            (_err(speech="delete"), (18, 23)),
            (_err(speech="delete"), (24, 29)),
        ]
        res = self._score(monkeypatch, errs)
        assert res.best_words == ["aaaaa", "bbbbb", "", "", ""]
        assert res.scores[:2] == [1.0, 1.0]
        assert res.scores[2:] == [0.0, 0.0, 0.0]
        assert res.errors[2:] == [[], [], []]
        assert res.n_decoded == 2

    def test_partial_delete_is_a_real_error_not_an_unrecited_word(self, monkeypatch):
        # One character of a five-character word: the reciter said it, dropping a phoneme.
        res = self._score(monkeypatch, [(_err(speech="delete"), (12, 13))])
        assert res.best_words[2] == "ccccc"       # attempted, so still matched
        assert 0.0 < res.scores[2] < 1.0
        assert len(res.errors[2]) == 1

    def test_current_substitution_aligned_to_future_word_is_rescued_locally(self, monkeypatch):
        """A future exact word must not steal the current spoken substitution.

        This models reciting يعلمون while يشعـرون is current and يعلمون also exists later in
        the 20-word window. The global alignment deletes the current word; the prefix-only local
        alignment sees a replacement and must return a confirmed low-scoring attempt.
        """
        monkeypatch.setattr(config, "muaalem_context_pad_words", 0)
        monkeypatch.setattr(config, "muaalem_weight_tajweed", 0.0)
        monkeypatch.setattr(am, "_qt_verse_words", lambda s, a: tuple(self.WORDS))
        monkeypatch.setattr(am, "_phonetize", lambda text: _FakeRef())
        monkeypatch.setattr(am, "_run_muaalem", lambda w, r, sampling_rate: [_FakeOut()])

        class _Raw:
            def __init__(self, err, span):
                self.err = err
                self.uthmani_pos = span

        global_raw = [_Raw(_err(speech="delete"), (0, 5))]
        local_raw = [_Raw(_err(speech="replace", predicted_ph="wrong"), (0, 5))]
        full_text = " ".join(self.WORDS)
        monkeypatch.setattr(
            am,
            "_explain",
            lambda text, *args: global_raw if text == full_text else local_raw,
        )
        monkeypatch.setattr(am, "_to_word_error", lambda r: r.err)

        res = am.MuaalemBackend().score(
            AUDIO, [], self.EXPECTED, word_meta=self.META
        )

        assert res.best_words[0] == self.WORDS[0]
        assert res.scores[0] == 0.25
        assert res.errors[0][0].speech_error_type == "replace"

    def test_local_rescue_ignores_the_rest_of_the_utterance(self, monkeypatch):
        """The trailing decode must not be charged to the word being rescued.

        The local reference stops at the current word, so every phoneme the reciter went on to
        recite comes back as a zero-width insert. explain_error anchors those to the last mapped
        reference character rather than past the span, so position cannot tell them from a real
        added phoneme — reciting إِيَّاكَ نَعْبُدُ وَإِيَّاكَ نَسْتَعِينُ put 12 of them inside
        an 8-character إِيَّاكَ and scored it 0.125.
        """
        monkeypatch.setattr(config, "muaalem_context_pad_words", 0)
        monkeypatch.setattr(config, "muaalem_weight_tajweed", 0.0)
        monkeypatch.setattr(am, "_qt_verse_words", lambda s, a: tuple(self.WORDS))
        monkeypatch.setattr(am, "_phonetize", lambda text: _FakeRef())
        monkeypatch.setattr(am, "_run_muaalem", lambda w, r, sampling_rate: [_FakeOut()])

        class _Raw:
            def __init__(self, err, span):
                self.err = err
                self.uthmani_pos = span

        global_raw = [_Raw(_err(speech="delete"), (0, 5))]
        # One real replace on the current word, then the next words' phonemes as zero-width
        # inserts anchored inside its span — the shape explain_error actually produces.
        local_raw = [_Raw(_err(speech="replace", predicted_ph="wrong"), (0, 5))] + [
            _Raw(_err(speech="insert", predicted_ph=ph), (3, 3))
            for ph in ("نَ", "ع", "بُ", "دُ", "وَ", "ي")
        ]
        full_text = " ".join(self.WORDS)
        monkeypatch.setattr(
            am,
            "_explain",
            lambda text, *args: global_raw if text == full_text else local_raw,
        )
        monkeypatch.setattr(am, "_to_word_error", lambda r: r.err)

        res = am.MuaalemBackend().score(AUDIO, [], self.EXPECTED, word_meta=self.META)

        # Same score as with no trailing inserts at all: they cost nothing.
        assert res.scores[0] == 0.25
        # And they are not reported back as errors on this word.
        assert [e.speech_error_type for e in res.errors[0]] == ["replace"]

    def test_previous_words_are_sliced_off(self, monkeypatch):
        res = self._score(monkeypatch, [], previous=2)
        assert len(res.scores) == 3
        assert res.best_words == ["ccccc", "ddddd", "eeeee"]

    def test_all_lists_are_parallel_to_expected_words(self, monkeypatch):
        res = self._score(monkeypatch, [(_err("tajweed", "replace"), (0, 5))])
        n = len(self.EXPECTED)
        for lst in (res.scores, res.char_scores, res.diac_scores,
                    res.best_words, res.tajweed_scores, res.errors):
            assert len(lst) == n

    def test_tajweed_error_is_reported_without_failing_the_word(self, monkeypatch):
        res = self._score(monkeypatch, [(_err("tajweed", "replace"), (0, 5))])
        assert res.scores[0] == 1.0        # not punished at the default weight
        assert res.tajweed_scores[0] == 0.0
        assert len(res.errors[0]) == 1
        assert res.errors[0][0].error_type == "tajweed"

    def test_empty_prediction_falls_back_to_neutral(self, monkeypatch):
        monkeypatch.setattr(config, "muaalem_context_pad_words", 0)
        monkeypatch.setattr(am, "_qt_verse_words", lambda s, a: tuple(self.WORDS))
        monkeypatch.setattr(am, "_phonetize", lambda text: _FakeRef())

        class _Empty:
            class phonemes:
                text = "   "

        monkeypatch.setattr(am, "_run_muaalem", lambda w, r, sampling_rate: [_Empty()])
        res = am.MuaalemBackend().score(AUDIO, [], self.EXPECTED, word_meta=self.META)
        assert res.scores == [0.5] * 5
        assert res.best_words == [""] * 5

    def test_unmappable_verse_falls_back_to_neutral(self, monkeypatch):
        monkeypatch.setattr(am, "_qt_verse_words", lambda s, a: None)
        res = am.MuaalemBackend().score(AUDIO, [], self.EXPECTED, word_meta=self.META)
        assert res.scores == [0.5] * 5

    def test_no_expected_words(self):
        res = am.MuaalemBackend().score(AUDIO, [], [], word_meta=[])
        assert res.scores == []

    def test_missing_word_meta_is_rejected(self):
        with pytest.raises(ValueError, match="word_meta"):
            am.MuaalemBackend().score(AUDIO, [], self.EXPECTED, word_meta=None)

    def test_mismatched_word_meta_length_is_rejected(self):
        with pytest.raises(ValueError, match="expected"):
            am.MuaalemBackend().score(AUDIO, [], self.EXPECTED, word_meta=self.META[:2])


class TestReconstructSpan:
    REF = "ABCDEFGH"

    def test_no_edits_returns_reference_slice(self):
        assert am._reconstruct_span(self.REF, [], (2, 5)) == "CDE"

    def test_whole_span(self):
        assert am._reconstruct_span(self.REF, [], (0, 8)) == self.REF

    def test_replace_inside(self):
        assert am._reconstruct_span(self.REF, [(2, 4, "xy")], (2, 6)) == "xyEF"

    def test_delete_inside(self):
        assert am._reconstruct_span(self.REF, [(2, 4, "")], (2, 6)) == "EF"

    def test_insert_inside(self):
        assert am._reconstruct_span(self.REF, [(3, 3, "z")], (2, 6)) == "CzDEF"

    def test_edit_outside_span_is_ignored(self):
        assert am._reconstruct_span(self.REF, [(0, 2, "q")], (4, 7)) == "EFG"

    def test_multiple_edits_reconstruct_in_order(self):
        # delete AB->'', replace CD->'x' inside span (0,6)
        assert am._reconstruct_span("ABCDEF", [(0, 2, ""), (2, 4, "x")], (0, 6)) == "xEF"


class TestWordGroups:
    @staticmethod
    def _stub(monkeypatch, counts):
        # counts: {joined_prefix_text: n_groups}; encode as that many space-separated tokens.
        monkeypatch.setattr(am, "_spaced_phonemes", lambda text: " ".join(["X"] * counts[text]))

    def test_empty(self):
        assert am._word_groups([]) == []

    def test_single_word_needs_no_phonetize(self):
        assert am._word_groups(["a"]) == [[0]]

    def test_all_standalone(self, monkeypatch):
        self._stub(monkeypatch, {"a b": 2, "a b c": 3})
        assert am._word_groups(["a", "b", "c"]) == [[0], [1], [2]]

    def test_a_merge(self, monkeypatch):
        # adding "c" does not add a group -> b and c merged
        self._stub(monkeypatch, {"a b": 2, "a b c": 2})
        assert am._word_groups(["a", "b", "c"]) == [[0], [1, 2]]

    def test_all_merge_into_one(self, monkeypatch):
        self._stub(monkeypatch, {"a b": 1, "a b c": 1})
        assert am._word_groups(["a", "b", "c"]) == [[0, 1, 2]]

    def test_unexpected_jump_returns_none(self, monkeypatch):
        self._stub(monkeypatch, {"a b": 3})  # +2 in one step is impossible
        assert am._word_groups(["a", "b"]) is None


class TestAssembleRecited:
    def test_standalone_words(self):
        units = am._assemble_recited(["w0", "w1"], "ABCD", ["AB", "CD"], [[0], [1]], [])
        assert (units[0].ph, units[0].expected_ph, units[0].words) == ("AB", "AB", ["w0"])
        assert (units[1].ph, units[1].expected_ph, units[1].words) == ("CD", "CD", ["w1"])

    def test_merged_words_share_one_unit(self):
        units = am._assemble_recited(["w0", "w1"], "ABCD", ["ABCD"], [[0, 1]], [])
        assert units[0] is units[1]
        assert units[0].words == ["w0", "w1"]
        assert units[0].ph == "ABCD"

    def test_edit_is_reflected_in_recited_not_expected(self):
        units = am._assemble_recited(["w0", "w1"], "ABCD", ["AB", "CD"], [[0], [1]], [(2, 4, "x")])
        assert units[1].expected_ph == "CD"
        assert units[1].ph == "x"


class TestScoreRecited:
    """score() populates AcousticResult.recited (model + phonetizer stubbed)."""

    WORDS = ["aaaaa", "bbbbb", "ccccc"]
    META = [_meta(1, 1, i + 1) for i in range(3)]
    EXPECTED = [(w, w) for w in WORDS]

    def _score(self, monkeypatch, ref_ph, groups, word_groups, raw):
        monkeypatch.setattr(config, "muaalem_context_pad_words", 0)
        monkeypatch.setattr(am, "_qt_verse_words", lambda s, a: tuple(self.WORDS))

        class _Ref:
            phonemes = ref_ph
            mappings = []

        class _Out:
            class phonemes:
                text = ref_ph  # unused by recited path (edits come from raw)

        monkeypatch.setattr(am, "_phonetize", lambda text: _Ref())
        monkeypatch.setattr(am, "_run_muaalem", lambda w, r, sampling_rate: [_Out()])
        monkeypatch.setattr(am, "_explain", lambda *a, **k: raw)
        monkeypatch.setattr(am, "_to_word_error", lambda r: r.err)
        monkeypatch.setattr(am, "_spaced_phonemes", lambda text: " ".join(groups))
        monkeypatch.setattr(am, "_word_groups", lambda words: word_groups)
        return am.MuaalemBackend().score(AUDIO, [], self.EXPECTED, word_meta=self.META)

    def test_recited_per_word(self, monkeypatch):
        res = self._score(monkeypatch, "AABBCC", ["AA", "BB", "CC"], [[0], [1], [2]], [])
        assert [u.ph for u in res.recited] == ["AA", "BB", "CC"]
        assert [u.words for u in res.recited] == [["aaaaa"], ["bbbbb"], ["ccccc"]]

    def test_merged_words_share_recited(self, monkeypatch):
        res = self._score(monkeypatch, "AABBCC", ["AABB", "CC"], [[0, 1], [2]], [])
        assert res.recited[0] is res.recited[1]
        assert res.recited[0].words == ["aaaaa", "bbbbb"]
        assert res.recited[2].words == ["ccccc"]

    def test_model_decode_failure_falls_back_to_neutral(self, monkeypatch):
        # A third-party decoder crash (e.g. quran_muaalem's empty-decode arity bug) must not
        # kill the streaming loop: the chunk gets neutral scores, like an empty decode.
        monkeypatch.setattr(config, "muaalem_context_pad_words", 0)
        monkeypatch.setattr(am, "_qt_verse_words", lambda s, a: tuple(self.WORDS))
        monkeypatch.setattr(am, "_phonetize", lambda text: _FakeRef())

        def _boom(*a, **k):
            raise ValueError("too many values to unpack (expected 2)")

        monkeypatch.setattr(am, "_run_muaalem", _boom)
        res = am.MuaalemBackend().score(AUDIO, [], self.EXPECTED, word_meta=self.META)
        assert res.scores == [0.5] * len(self.EXPECTED)
        assert res.best_words == [""] * len(self.EXPECTED)

    def test_recited_failure_degrades_to_none_without_breaking_scoring(self, monkeypatch):
        # _spaced_phonemes blows up -> recited omitted, but scores still computed.
        monkeypatch.setattr(config, "muaalem_context_pad_words", 0)
        monkeypatch.setattr(am, "_qt_verse_words", lambda s, a: tuple(self.WORDS))

        class _Ref:
            phonemes = "AABBCC"
            mappings = []

        class _Out:
            class phonemes:
                text = "AABBCC"

        monkeypatch.setattr(am, "_phonetize", lambda text: _Ref())
        monkeypatch.setattr(am, "_run_muaalem", lambda w, r, sampling_rate: [_Out()])
        monkeypatch.setattr(am, "_explain", lambda *a, **k: [])
        monkeypatch.setattr(am, "_spaced_phonemes", lambda text: (_ for _ in ()).throw(RuntimeError("boom")))
        res = am.MuaalemBackend().score(AUDIO, [], self.EXPECTED, word_meta=self.META)
        assert res.scores == [1.0, 1.0, 1.0]
        assert res.recited == [None, None, None]
