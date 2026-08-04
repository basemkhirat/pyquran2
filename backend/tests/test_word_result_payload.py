"""The `word_result` wire contract, exercised through main._do_process_speech.

The point of these is the promise that adding muaalem changed nothing for existing clients:
a wav2vec2 session's payload must be byte-identical to what it was before this backend
existed, and the muaalem extras must be *absent keys*, not nulls. Only the model is stubbed.
"""
import asyncio
import sys
import types

import numpy as np
import pytest

# backend.main imports backend.vad, which pulls in torch + silero_vad at module level.
if "backend.vad" not in sys.modules:
    _vad_stub = types.ModuleType("backend.vad")

    class _StubVAD:
        def __init__(self, *args, **kwargs):
            pass

    _vad_stub.VADProcessor = _StubVAD
    sys.modules["backend.vad"] = _vad_stub

from backend import acoustic_scorer, main  # noqa: E402
from backend.acoustic_scorer import AcousticResult, RecitedUnit, WordError  # noqa: E402
from backend.config import config  # noqa: E402


# 1s of audio at the configured rate — comfortably over the 0.5s floor.
AUDIO = np.zeros(config.audio_sample_rate, dtype=np.float32)

WORDS = [
    {"surah": 1, "ayah": 1, "word_index": 1, "emlaey_text": "بسم", "uthmani_text": "بِسْمِ"},
    {"surah": 1, "ayah": 1, "word_index": 2, "emlaey_text": "الله", "uthmani_text": "ٱللَّهِ"},
]

_ERROR = WordError(
    error_type="tajweed",
    speech_error_type="replace",
    expected_ph="ۦۦۦۦ",
    predicted_ph="ۦۦ",
    expected_len=4,
    predicted_len=2,
)


def _session(model="wav2vec2", **overrides):
    session = {
        "words": WORDS,
        "current_index": 0,
        "mode": "continuous",
        "model": model,
        "score_threshold": 0.5,
        "streaming_start_idx": 0,
        "timeline_cursor_sec": None,
        "result_words": [],
        "last_interim_index": None,
        "last_interim_acoustic": None,
        "last_interim_n_decoded": None,
        "total_samples": len(AUDIO),
        "ended": False,
        "id": "sess-1",
        "origin": "https://recite.example",
        "streaming_task": None,
        "store": None,
        "store_queue": None,
        "store_task": None,
    }
    session.update(overrides)
    return session


@pytest.fixture
def emitted(monkeypatch):
    calls = []

    async def fake_emit(event, payload=None, room=None):
        calls.append((event, payload))

    monkeypatch.setattr(main.sio, "emit", fake_emit)
    return calls


@pytest.fixture
def stub_scores(monkeypatch):
    """Replace the acoustic model with a fixed AcousticResult."""

    def _stub(result: AcousticResult):
        monkeypatch.setattr(
            acoustic_scorer, "get_acoustic_scores", lambda *a, **kw: result
        )

    return _stub


def _wav2vec2_result():
    return AcousticResult(
        scores=[0.9, 0.9],
        char_scores=[0.9, 0.9],
        diac_scores=[0.9, 0.9],
        best_words=["بِسْمِ", "ٱللَّهِ"],
        n_decoded=2,
        offsets=[(0.0, 0.4), (0.4, 0.9)],
    )


def _muaalem_result():
    return AcousticResult(
        scores=[0.9, 0.9],
        char_scores=[0.9, 0.9],
        diac_scores=[0.9, 0.9],
        best_words=["بِسْمِ", "ٱللَّهِ"],
        n_decoded=2,
        offsets=[None, None],
        tajweed_scores=[1.0, 0.9],
        errors=[[], [_ERROR]],
        recited=[
            RecitedUnit(ph="بِسمِ", expected_ph="بِسمِ", words=["بِسْمِ"]),
            RecitedUnit(ph="للَااهِ", expected_ph="للَااهِ", words=["ٱللَّهِ"]),
        ],
    )


def _skipped_first_word_result(model="muaalem"):
    """Nothing aligned to word 1; word 2 decoded confidently — the reciter moved on.

    This is the real شكل of the bug: مِن إِيَّاكَ نَعْبُدُ decoded with إِيَّاكَ unattributed, its
    phonemes surfacing as inserts on the next word.
    """
    common = dict(
        scores=[0.0, 0.9],
        char_scores=[0.0, 0.9],
        diac_scores=[0.0, 0.9],
        best_words=["", "ٱللَّهِ"],
        n_decoded=1,
    )
    if model == "wav2vec2":
        return AcousticResult(**common, offsets=[None, (0.4, 0.9)])
    return AcousticResult(
        **common,
        offsets=[None, None],
        tajweed_scores=[0.0, 0.9],
        errors=[[], [_ERROR]],
        recited=[None, RecitedUnit(ph="للَااهِ", expected_ph="للَااهِ", words=["ٱللَّهِ"])],
    )


def _run(session, stub_scores, result, is_final=True):
    stub_scores(result)
    asyncio.run(main._do_process_speech("sid", session, AUDIO, is_final=is_final))


def _word_results(emitted):
    return [p for event, p in emitted if event == "word_result"]


pytestmark = pytest.mark.skipif(
    not config.enable_acoustic_score or config.enable_text_score,
    reason="these assert the acoustic-only payload shape",
)


class TestWav2Vec2PayloadIsUnchanged:
    """No muaalem key may appear for a wav2vec2 session — not even set to null."""

    # Live payload keys plus the two the recorded timeline stores instead of `recited`.
    MUAALEM_KEYS = (
        "errors", "error_type", "tajweed_score", "recited", "detected_ph", "expected_ph",
    )

    def test_payload_has_exactly_the_original_keys(self, emitted, stub_scores):
        _run(_session("wav2vec2"), stub_scores, _wav2vec2_result())
        payload = _word_results(emitted)[0]
        assert set(payload) == {
            "chapter_number", "verse_number", "word_number",
            "status", "total_score", "expected_text", "detected_text",
        }

    def test_no_muaalem_key_is_present(self, emitted, stub_scores):
        _run(_session("wav2vec2"), stub_scores, _wav2vec2_result())
        for payload in _word_results(emitted):
            for key in self.MUAALEM_KEYS:
                assert key not in payload

    def test_timeline_entries_carry_no_muaalem_fields(self, emitted, stub_scores):
        session = _session("wav2vec2")
        _run(session, stub_scores, _wav2vec2_result())
        for entry in session["result_words"]:
            for key in self.MUAALEM_KEYS:
                assert key not in entry

    def test_word_offsets_still_drive_timeline_timings(self, emitted, stub_scores):
        # wav2vec2 offsets are relative to the segment start (0 here), in seconds -> ms.
        session = _session("wav2vec2")
        _run(session, stub_scores, _wav2vec2_result())
        assert session["result_words"][0]["start_time"] == 0
        assert session["result_words"][0]["end_time"] == 400


class TestMuaalemPayloadExtras:
    def test_errors_and_dominant_type_are_attached(self, emitted, stub_scores):
        _run(_session("muaalem"), stub_scores, _muaalem_result())
        second = _word_results(emitted)[1]
        assert second["error_type"] == "tajweed"
        assert second["errors"] == [
            {
                "error_type": "tajweed",
                "speech_error_type": "replace",
                "expected_ph": "ۦۦۦۦ",
                "predicted_ph": "ۦۦ",
                "expected_len": 4,
                "predicted_len": 2,
                "rules": [],
            }
        ]

    def test_clean_word_reports_no_errors_key(self, emitted, stub_scores):
        """An empty error list is omitted, so the UI's `!!errors?.length` check is enough."""
        _run(_session("muaalem"), stub_scores, _muaalem_result())
        clean = _word_results(emitted)[0]
        assert "errors" not in clean and "error_type" not in clean
        # ...but its tajweed score is still reported.
        assert clean["tajweed_score"] == 1.0

    def test_tajweed_score_and_recited_are_attached(self, emitted, stub_scores):
        _run(_session("muaalem"), stub_scores, _muaalem_result())
        second = _word_results(emitted)[1]
        assert second["tajweed_score"] == 0.9
        assert second["recited"] == {
            "ph": "للَااهِ", "expected_ph": "للَااهِ", "words": ["ٱللَّهِ"],
        }

    def test_tajweed_error_does_not_fail_the_word(self, emitted, stub_scores):
        # MUAALEM_WEIGHT_TAJWEED defaults to 0: the badge shows, the word still passes.
        _run(_session("muaalem"), stub_scores, _muaalem_result())
        assert _word_results(emitted)[1]["status"] == "correct"

    def test_muaalem_fields_reach_the_recorded_timeline(self, stub_scores, emitted):
        """Recording the extras is what lets playback replay the same detail."""
        session = _session("muaalem")
        _run(session, stub_scores, _muaalem_result())
        second = session["result_words"][1]
        assert second["errors"] == _word_results(emitted)[1]["errors"]
        assert second["detected_ph"] == "للَااهِ"
        assert second["expected_ph"] == "للَااهِ"

    def test_the_timeline_flattens_recited_and_drops_derivable_fields(self, stub_scores, emitted):
        """error_type/tajweed_score are recomputable from `errors`, so they aren't stored."""
        session = _session("muaalem")
        _run(session, stub_scores, _muaalem_result())
        for entry in session["result_words"]:
            for key in ("recited", "error_type", "tajweed_score"):
                assert key not in entry

    def test_a_clean_word_records_empty_errors_and_its_phonemes(self, stub_scores, emitted):
        """Unlike the live payload, a clean muaalem word still carries `errors` — as []."""
        session = _session("muaalem")
        _run(session, stub_scores, _muaalem_result())
        first = session["result_words"][0]
        assert first["errors"] == []
        assert first["detected_ph"] == "بِسمِ"

    def test_missing_offsets_fall_back_to_proportional_timings(self, stub_scores, emitted):
        """Muaalem has no CTC offsets, so timings come from the char-proportional cursor."""
        session = _session("muaalem")
        _run(session, stub_scores, _muaalem_result())
        entries = session["result_words"]
        assert entries[0]["start_time"] == 0
        # Still monotonic and inside the 1000ms segment, just less precise.
        assert 0 < entries[0]["end_time"] <= entries[1]["end_time"] <= 1000


class TestSkippedWordIsStillRecorded:
    """A word the aligner could not attribute is recorded, not dropped.

    It usually *was* recited — the decode just landed on the next word, whose payload shows the
    leftovers as inserts. Dropping it made the session claim the word was never read, and handed
    its share of the segment to the following word, so playback highlighted one word ahead of the
    audio for the rest of the recitation.
    """

    def test_the_unmatched_word_reaches_the_timeline(self, stub_scores, emitted):
        session = _session("muaalem")
        _run(session, stub_scores, _skipped_first_word_result())
        entries = session["result_words"]
        assert [e["word_number"] for e in entries] == [1, 2]
        assert entries[0]["status"] == "incorrect"
        assert entries[0]["total_score"] == 0.0
        assert entries[0]["detected_text"] == ""

    def test_it_still_emits_the_same_word_result(self, stub_scores, emitted):
        """The client contract is unchanged: a flagged 0% miss, confirmed (not interim)."""
        session = _session("muaalem")
        _run(session, stub_scores, _skipped_first_word_result())
        missed = _word_results(emitted)[0]
        assert missed["status"] == "incorrect" and missed["total_score"] == 0.0
        assert "is_interim" not in missed          # is_final=True -> confirmed

    def test_it_takes_its_own_slice_so_the_next_word_is_not_shifted(self, stub_scores, emitted):
        session = _session("muaalem")
        _run(session, stub_scores, _skipped_first_word_result())
        first, second = session["result_words"]
        # The whole point: the following word no longer starts at the segment start.
        assert second["start_time"] > 0
        assert first["end_time"] == second["start_time"]
        assert first["start_time"] < first["end_time"] <= second["end_time"] <= 1000

    def test_a_muaalem_entry_still_carries_the_errors_key(self, stub_scores, emitted):
        """Empty: no decode reached it, so there is no per-phoneme detail to report."""
        session = _session("muaalem")
        _run(session, stub_scores, _skipped_first_word_result())
        assert session["result_words"][0]["errors"] == []

    def test_a_wav2vec2_entry_gains_no_muaalem_keys(self, stub_scores, emitted):
        session = _session("wav2vec2")
        _run(session, stub_scores, _skipped_first_word_result("wav2vec2"))
        entry = session["result_words"][0]
        assert entry["word_number"] == 1 and entry["total_score"] == 0.0
        for key in ("errors", "detected_ph", "expected_ph"):
            assert key not in entry
