"""Tests for verse_detection module."""
import numpy as np
import pytest

from backend.verse_detection import (
    detect_start_verse,
    _build_verse_candidates,
    _verse_start_offsets,
)


# Sample word list mimicking quran_data.get_words_range() output for Al-Fatiha (7 verses)
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

# Sample words spanning two chapters for cross-chapter testing
CROSS_CHAPTER_WORDS = [
    # End of chapter 1
    {"surah": 1, "ayah": 6, "word_index": 1, "emlaey_text": "اهدنا", "uthmani_text": "ٱهْدِنَا"},
    {"surah": 1, "ayah": 6, "word_index": 2, "emlaey_text": "الصراط", "uthmani_text": "ٱلصِّرَٰطَ"},
    {"surah": 1, "ayah": 7, "word_index": 1, "emlaey_text": "صراط", "uthmani_text": "صِرَٰطَ"},
    {"surah": 1, "ayah": 7, "word_index": 2, "emlaey_text": "الذين", "uthmani_text": "ٱلَّذِينَ"},
    # Start of chapter 2
    {"surah": 2, "ayah": 1, "word_index": 1, "emlaey_text": "الم", "uthmani_text": "الٓمٓ"},
    {"surah": 2, "ayah": 2, "word_index": 1, "emlaey_text": "ذلك", "uthmani_text": "ذَٰلِكَ"},
    {"surah": 2, "ayah": 2, "word_index": 2, "emlaey_text": "الكتاب", "uthmani_text": "ٱلْكِتَٰبُ"},
]


def _mk(surah, ayah, tokens):
    """Build a verse's word dicts (word_index restarts at 1, like real data)."""
    return [
        {"surah": surah, "ayah": ayah, "word_index": i, "emlaey_text": t, "uthmani_text": t}
        for i, t in enumerate(tokens, start=1)
    ]


# Al-Rahman-like slice: the refrain فبأي آلاء ربكما تكذبان repeats at verses 16/18/21
# (identical), interleaved with distinct verses. Mirrors the real ambiguity.
REFRAIN = ["فبأي", "آلاء", "ربكما", "تكذبان"]
RAHMAN_WORDS = (
    _mk(55, 16, REFRAIN)                                    # idx 0-3
    + _mk(55, 17, ["رب", "المشرقين", "ورب", "المغربين"])    # idx 4-7
    + _mk(55, 18, REFRAIN)                                  # idx 8-11
    + _mk(55, 19, ["مرج", "البحرين", "يلتقيان"])            # idx 12-14
    + _mk(55, 20, ["بينهما", "برزخ", "لا", "يبغيان"])        # idx 15-18
    + _mk(55, 21, REFRAIN)                                  # idx 19-22
    + _mk(55, 22, ["يخرج", "منهما", "اللؤلؤ", "والمرجان"])  # idx 23-26
)


def _patch_decode(monkeypatch, text):
    monkeypatch.setattr("backend.verse_detection._decode_audio", lambda _: text)


AUDIO = np.zeros(16000, dtype=np.float32)


class TestBuildVerseCandidates:
    """Test the verse candidate builder."""

    def test_builds_correct_candidates(self):
        candidates = _build_verse_candidates(SAMPLE_WORDS, n_words=2)
        assert len(candidates) == 4
        assert (1, 1) in candidates
        assert (1, 2) in candidates
        assert (1, 3) in candidates
        assert (1, 4) in candidates
        # Check first 2 words of each verse
        assert candidates[(1, 1)][0] == "بسم الله"
        assert candidates[(1, 2)][0] == "الحمد لله"
        assert candidates[(1, 3)][0] == "الرحمن الرحيم"
        assert candidates[(1, 4)][0] == "مالك يوم"

    def test_word_indices(self):
        candidates = _build_verse_candidates(SAMPLE_WORDS, n_words=2)
        assert candidates[(1, 1)][1] == 0   # verse 1 starts at index 0
        assert candidates[(1, 2)][1] == 4   # verse 2 starts at index 4
        assert candidates[(1, 3)][1] == 8   # verse 3 starts at index 8
        assert candidates[(1, 4)][1] == 10  # verse 4 starts at index 10

    def test_partial_range(self):
        # Filter words to only include verses 2 and 3 (simulating what quran_data would return)
        partial_words = [w for w in SAMPLE_WORDS if 2 <= w["ayah"] <= 3]
        candidates = _build_verse_candidates(partial_words, n_words=2)
        assert len(candidates) == 2
        assert (1, 1) not in candidates
        assert (1, 2) in candidates
        assert (1, 3) in candidates

    def test_single_word_verse(self):
        """Verse with fewer words than n_words should still produce a candidate."""
        words = [
            {"surah": 1, "ayah": 1, "word_index": 1, "emlaey_text": "قل", "uthmani_text": "قُلْ"},
        ]
        candidates = _build_verse_candidates(words, n_words=2)
        assert len(candidates) == 1
        assert candidates[(1, 1)][0] == "قل"


class TestVerseStartOffsets:
    """Test the verse-start enumerator."""

    def test_offsets_in_order(self):
        offsets = _verse_start_offsets(SAMPLE_WORDS)
        assert offsets == [(1, 1, 0), (1, 2, 4), (1, 3, 8), (1, 4, 10)]

    def test_cross_chapter(self):
        offsets = _verse_start_offsets(CROSS_CHAPTER_WORDS)
        assert offsets == [(1, 6, 0), (1, 7, 2), (2, 1, 4), (2, 2, 5)]


class TestDetectStartVerse:
    """Test detect_start_verse with mocked wav2vec2 decoding."""

    def test_exact_match_verse1(self, monkeypatch):
        _patch_decode(monkeypatch, "بسم الله")
        result = detect_start_verse(AUDIO, SAMPLE_WORDS)
        assert result.status == "commit"
        assert result.chapter == 1
        assert result.ayah == 1
        assert result.word_index == 0
        assert result.score >= 0.6

    def test_exact_match_verse4(self, monkeypatch):
        _patch_decode(monkeypatch, "مالك يوم")
        result = detect_start_verse(AUDIO, SAMPLE_WORDS)
        assert result.status == "commit"
        assert result.chapter == 1
        assert result.ayah == 4
        assert result.word_index == 10

    def test_best_match_among_candidates(self, monkeypatch):
        """Slightly imperfect match should still find the best candidate."""
        _patch_decode(monkeypatch, "الحمد لله")  # exact match for verse 2
        result = detect_start_verse(AUDIO, SAMPLE_WORDS)
        assert result.status == "commit"
        assert result.chapter == 1
        assert result.ayah == 2

    def test_below_threshold(self, monkeypatch):
        """Completely unrelated speech should fail detection."""
        _patch_decode(monkeypatch, "كلام عشوائي تماما")  # random unrelated text
        result = detect_start_verse(AUDIO, SAMPLE_WORDS)
        assert result.status == "none"

    def test_empty_decoded_text(self, monkeypatch):
        _patch_decode(monkeypatch, "")
        result = detect_start_verse(AUDIO, SAMPLE_WORDS)
        assert result.status == "none"

    def test_single_verse_range(self, monkeypatch):
        _patch_decode(monkeypatch, "مالك يوم")
        # Filter words to only include verse 4
        verse4_words = [w for w in SAMPLE_WORDS if w["ayah"] == 4]
        result = detect_start_verse(AUDIO, verse4_words)
        assert result.status == "commit"
        assert result.chapter == 1
        assert result.ayah == 4

    def test_verse_outside_range_not_matched(self, monkeypatch):
        """Speech matching verse 1 should not match when word list only has verses 3-4."""
        _patch_decode(monkeypatch, "بسم الله")
        # Filter words to only include verses 3 and 4
        partial_words = [w for w in SAMPLE_WORDS if 3 <= w["ayah"] <= 4]
        result = detect_start_verse(AUDIO, partial_words)
        # Should not confidently commit to verse 1
        assert result.status == "none" or result.ayah != 1

    def test_cross_chapter_detection(self, monkeypatch):
        """Test detection across chapter boundaries."""
        _patch_decode(monkeypatch, "ذلك الكتاب")  # Start of Al-Baqarah verse 2
        result = detect_start_verse(AUDIO, CROSS_CHAPTER_WORDS)
        assert result.status == "commit"
        assert result.chapter == 2  # Should detect in chapter 2
        assert result.ayah == 2     # verse 2 of Al-Baqarah

    def test_cross_chapter_first_chapter_still_works(self, monkeypatch):
        """Ensure verses from the first chapter in a cross-chapter range still work."""
        _patch_decode(monkeypatch, "اهدنا الصراط")  # Al-Fatiha verse 6
        result = detect_start_verse(AUDIO, CROSS_CHAPTER_WORDS)
        assert result.status == "commit"
        assert result.chapter == 1  # Should detect in chapter 1
        assert result.ayah == 6     # verse 6


class TestPrefixCollision:
    """Verses that share a prefix but diverge later are resolved by a longer utterance."""

    WORDS = _mk(112, 1, ["قل", "هو", "الله", "احد"]) + _mk(112, 100, ["قل", "هو", "الرحمن", "رحيم"])

    def test_short_shared_prefix_is_ambiguous(self, monkeypatch):
        _patch_decode(monkeypatch, "قل هو")  # matches both verses equally
        result = detect_start_verse(AUDIO, self.WORDS)
        assert result.status == "ambiguous"
        assert len(result.candidates) == 2

    def test_full_utterance_resolves_collision(self, monkeypatch):
        _patch_decode(monkeypatch, "قل هو الله احد")  # only the first verse
        result = detect_start_verse(AUDIO, self.WORDS)
        assert result.status == "commit"
        assert result.ayah == 1


class TestRefrainDisambiguation:
    """The Al-Rahman case: identical refrains must not be guessed prematurely."""

    def test_lone_refrain_is_ambiguous(self, monkeypatch):
        _patch_decode(monkeypatch, " ".join(REFRAIN))
        result = detect_start_verse(AUDIO, RAHMAN_WORDS, is_final=False)
        assert result.status == "ambiguous"
        # verses 16, 18, 21 all match identically
        assert {a for _, a, _ in result.candidates} == {16, 18, 21}

    def test_refrain_plus_following_verse_resolves(self, monkeypatch):
        # refrain followed by verse 19's text uniquely identifies the verse-18 occurrence
        _patch_decode(monkeypatch, " ".join(REFRAIN + ["مرج", "البحرين", "يلتقيان"]))
        result = detect_start_verse(AUDIO, RAHMAN_WORDS, is_final=False)
        assert result.status == "commit"
        assert result.ayah == 18

    def test_refrain_plus_verse17_resolves_to_16(self, monkeypatch):
        _patch_decode(monkeypatch, " ".join(REFRAIN + ["رب", "المشرقين"]))
        result = detect_start_verse(AUDIO, RAHMAN_WORDS, is_final=False)
        assert result.status == "commit"
        assert result.ayah == 16

    def test_final_fallback_biases_to_start_verse(self, monkeypatch):
        _patch_decode(monkeypatch, " ".join(REFRAIN))
        # On speech end with an unresolved tie, pick the earliest occurrence >= start.
        r18 = detect_start_verse(
            AUDIO, RAHMAN_WORDS, start_chapter=55, start_verse=18, is_final=True
        )
        assert r18.status == "commit"
        assert r18.ayah == 18

        r16 = detect_start_verse(
            AUDIO, RAHMAN_WORDS, start_chapter=55, start_verse=16, is_final=True
        )
        assert r16.status == "commit"
        assert r16.ayah == 16

        r21 = detect_start_verse(
            AUDIO, RAHMAN_WORDS, start_chapter=55, start_verse=21, is_final=True
        )
        assert r21.status == "commit"
        assert r21.ayah == 21
