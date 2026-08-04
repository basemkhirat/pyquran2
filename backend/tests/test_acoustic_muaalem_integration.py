"""End-to-end muaalem scoring against the *real* quran_transcript library.

test_acoustic_muaalem.py stubs `_phonetize`/`_explain` so the attribution logic can be unit
tested without the library. That is fast but blind in one specific way: hand-written fakes
put one tidy `delete` per unspoken word, whereas real `explain_error` output never covers an
unspoken word completely (characters that produce no phonemes sit outside every delete span).
A coverage-based unrecited check passed 40 stub tests and still mis-scored real verses.

So these tests run the genuine phonetizer and error explainer, and stub only the model — the
one component that needs weights and a GPU. They are skipped when quran-transcript is absent.

`quran_muaalem` itself is never imported here: `_run_muaalem` is the only function that
touches it, and it is replaced wholesale.
"""
import numpy as np
import pytest

from backend import acoustic_muaalem as am

pytest.importorskip("quran_transcript", reason="muaalem backend deps not installed")


AUDIO = np.zeros(16000, dtype=np.float32)

# Al-Fatiha 1:1 — بِسْمِ ٱللَّهِ ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ (4 words).
FATIHA_1_1 = [{"surah": 1, "ayah": 1, "word_index": i} for i in range(1, 5)]
EXPECTED = [("", "")] * 4


class _FakeOutput:
    """The one field of MuaalemOutput the backend reads."""

    def __init__(self, text: str):
        self.phonemes = type("P", (), {"text": text})()


@pytest.fixture
def score(monkeypatch):
    """Score a predicted phoneme string against 1:1, with only the model stubbed out."""

    def _score(predicted: str, word_meta=FATIHA_1_1, expected=EXPECTED):
        monkeypatch.setattr(am, "_run_muaalem", lambda waves, refs, sr: [_FakeOutput(predicted)])
        return am.MuaalemBackend().score(AUDIO, [], expected, word_meta)

    return _score


def reference_phonemes(word_meta=FATIHA_1_1) -> str:
    """What a flawless recitation of these words decodes to."""
    return am._phonetize(" ".join(am._reference_words(word_meta))).phonemes


def reference_word_groups(word_meta=FATIHA_1_1) -> list[str]:
    """Reference phonemes split at real phonetic word boundaries (merges collapse)."""
    return am._spaced_phonemes(" ".join(am._reference_words(word_meta))).split(" ")


class TestReferenceText:
    def test_reference_comes_from_quran_transcript_not_hafs_json(self):
        """The two disagree character-for-character, and only quran_transcript's phonetizes.

        They are different Uthmani *presentation* conventions (U+0652 vs U+06E1 for sukoon,
        tatweel, and ~74 other contextual differences), so feeding hafs.json text to the
        phonetizer raises IndexError on ~51% of verses. Word counts do agree, which is what
        makes the positional mapping valid.
        """
        from backend import quran_data

        qt_words = am._reference_words(FATIHA_1_1)
        hafs_words = [w["uthmani_text"] for w in quran_data.get_words_range(1, 1, 1, 1)]

        assert len(qt_words) == len(hafs_words)
        assert qt_words != hafs_words, "expected the two conventions to differ"
        # The reference actually used must be the one the phonetizer accepts.
        assert am._phonetize(" ".join(qt_words)).phonemes

    def test_word_counts_agree_so_positional_mapping_is_valid(self):
        from backend import quran_data

        for surah, ayah in [(1, 1), (2, 255), (112, 1), (114, 1)]:
            qt = am._qt_verse_words(surah, ayah)
            hafs = quran_data.get_words_range(surah, ayah, surah, ayah)
            assert qt is not None and len(qt) == len(hafs), f"{surah}:{ayah}"

    def test_known_word_count_mismatch_is_reported_as_unmappable(self):
        """15:7 is the one verse the two sources segment differently.

        hafs.json fuses لَّوۡمَا into one word where quran_transcript splits لَّوْ + مَا (7 vs 8).
        Indexing past that point would silently score the wrong words, so _qt_verse_words
        returns None and the caller treats the chunk as unscorable.
        """
        import quran_transcript

        from backend import quran_data

        raw_qt = quran_transcript.Aya(15, 7).get().uthmani.split()
        hafs = quran_data.get_words_range(15, 7, 15, 7)
        assert len(raw_qt) != len(hafs), "15:7 now agrees; the None guard may be removable"
        assert am._qt_verse_words(15, 7) is None

    def test_unmappable_verse_falls_back_to_neutral_scores(self, score):
        res = score(reference_phonemes(), word_meta=[{"surah": 15, "ayah": 7, "word_index": 1}],
                    expected=[("", "")])
        assert res.scores == [0.5]
        assert res.best_words == [""]

    def test_phonetizing_the_whole_quran_range_does_not_crash(self):
        # MOSHAF_MADD_AARED_LEN=4 is what makes arbitrary word slices safe; at 2 these raise
        # KeyError from inside the phonetizer on madd-al-leen words.
        for surah, ayah in [(1, 1), (55, 17), (90, 8), (106, 1), (112, 1)]:
            words = am._qt_verse_words(surah, ayah)
            assert words, f"{surah}:{ayah} produced no words"
            assert am._phonetize(" ".join(words)).phonemes


class TestFlawlessRecitation:
    def test_every_word_scores_one(self, score):
        res = score(reference_phonemes())
        assert res.scores == [1.0, 1.0, 1.0, 1.0]
        assert res.tajweed_scores == [1.0, 1.0, 1.0, 1.0]

    def test_no_errors_are_reported(self, score):
        assert score(reference_phonemes()).errors == [[], [], [], []]

    def test_every_word_is_matched(self, score):
        # best_words != "" is the signal main.py uses to keep processing the chunk.
        assert all(score(reference_phonemes()).best_words)

    def test_recited_phonemes_are_reconstructed_per_word(self, score):
        res = score(reference_phonemes())
        assert [u.ph for u in res.recited] == reference_word_groups()


class TestUnrecitedTail:
    """The load-bearing case: the reference window runs ~20 words past the reciter.

    Every word not yet reached comes back fully deleted from explain_error. Scoring those as
    0.0 would paint the rest of the verse red on every streaming tick, so they must instead
    report best_word="" — the same "unmatched" signal wav2vec2 gives.
    """

    def _first_two_words(self) -> str:
        return "".join(reference_word_groups()[:2])

    def test_unspoken_words_are_unmatched_not_wrong(self, score):
        res = score(self._first_two_words())
        assert res.best_words[2] == ""
        assert res.best_words[3] == ""

    def test_spoken_words_still_score_normally(self, score):
        res = score(self._first_two_words())
        assert res.scores[:2] == [1.0, 1.0]

    def test_only_the_spoken_words_count_as_decoded(self, score):
        assert score(self._first_two_words()).n_decoded == 2

    def test_silence_falls_back_to_neutral_scores(self, score):
        # Mirrors the wav2vec2 backend's 0.5 for an empty decode.
        res = score("")
        assert res.scores == [0.5] * 4
        assert res.best_words == [""] * 4
        assert res.n_decoded == 0


class TestTajweedError:
    """A shortened madd: real, detectable, and by default not a failure."""

    def _shortened_madd(self) -> str:
        # ررَحِۦۦۦۦم -> ررَحِۦۦم, i.e. a 4-count madd recited as 2.
        return reference_phonemes().replace("ۦۦۦۦ", "ۦۦ")

    def test_error_is_classified_as_tajweed(self, score):
        errors = score(self._shortened_madd()).errors[3]
        assert [e.error_type for e in errors] == ["tajweed"]

    def test_madd_lengths_are_reported(self, score):
        error = score(self._shortened_madd()).errors[3][0]
        assert (error.expected_len, error.predicted_len) == (4, 2)

    def test_tajweed_is_surfaced_without_failing_the_word(self, score):
        # MUAALEM_WEIGHT_TAJWEED defaults to 0, so the total is untouched while the separate
        # tajweed score drops — the UI shows a gold badge on a word that still reads green.
        res = score(self._shortened_madd())
        assert res.scores[3] == 1.0
        assert res.tajweed_scores[3] < 1.0

    def test_earlier_words_are_unaffected(self, score):
        res = score(self._shortened_madd())
        assert res.errors[:3] == [[], [], []]


class TestPreviousWordsAreSlicedOff:
    def test_confirmed_prefix_is_dropped_from_the_result(self, monkeypatch):
        """previous_words cover audio already scored; the caller only wants what follows."""
        monkeypatch.setattr(
            am, "_run_muaalem", lambda w, r, s: [_FakeOutput(reference_phonemes())]
        )
        res = am.MuaalemBackend().score(
            AUDIO,
            [("", "")] * 2,     # the first two words are already confirmed
            [("", "")] * 2,     # only these two are wanted back
            FATIHA_1_1,         # word_meta covers previous + expected, in that order
        )
        # Every parallel list is sliced to the expected words only.
        assert len(res.scores) == len(res.best_words) == len(res.errors) == 2
        assert len(res.tajweed_scores) == len(res.recited) == 2
        # Words 3 and 4 of 1:1, recited correctly.
        assert res.scores == [1.0, 1.0]
        assert [u.ph for u in res.recited] == reference_word_groups()[2:]

    def test_offsets_are_present_but_empty_of_timings(self, monkeypatch):
        """Muaalem has no per-word frame offsets; main.py falls back to its timing cursor."""
        monkeypatch.setattr(
            am, "_run_muaalem", lambda w, r, s: [_FakeOutput(reference_phonemes())]
        )
        res = am.MuaalemBackend().score(AUDIO, [], EXPECTED, FATIHA_1_1)
        assert res.offsets == [None] * 4

    def test_word_meta_is_required(self):
        with pytest.raises(ValueError, match="word_meta"):
            am.MuaalemBackend().score(AUDIO, [], EXPECTED, None)

    def test_word_meta_length_must_cover_previous_plus_expected(self):
        with pytest.raises(ValueError, match="word_meta has"):
            am.MuaalemBackend().score(AUDIO, [], EXPECTED, FATIHA_1_1[:2])
