"""Small pure regressions for continuous streaming state in backend.main."""

from backend.main import _new_final_decode_budget, _restore_cached_acoustic_interim


def _session(index=7):
    return {
        "last_interim_index": index,
        "last_interim_acoustic": {
            "score": 0.375,
            "decoded": "عَظِيمٌ",
            "tajweed": 0.8,
            "errors": ["cached-error"],
            "recited": "cached-recited",
        },
    }


def test_final_unmatched_word_restores_its_interim_acoustic_match():
    scores = [0.0]
    decoded = [""]
    tajweed = [0.0]
    errors = [[]]
    recited = [None]

    restored = _restore_cached_acoustic_interim(
        _session(), 7, scores, decoded, tajweed, errors, recited
    )

    assert restored is True
    assert scores == [0.375]
    assert decoded == ["عَظِيمٌ"]
    assert tajweed == [0.8]
    assert errors == [["cached-error"]]
    assert recited == ["cached-recited"]


def test_real_final_match_is_never_replaced_by_interim_cache():
    scores = [1.0]
    decoded = ["عَظِيمٌ-final"]
    tajweed = [1.0]
    errors = [[]]
    recited = ["final-recited"]

    restored = _restore_cached_acoustic_interim(
        _session(), 7, scores, decoded, tajweed, errors, recited
    )

    assert restored is False
    assert scores == [1.0]
    assert decoded == ["عَظِيمٌ-final"]


def test_cache_from_another_word_is_not_reused():
    scores = [0.0]
    decoded = [""]

    restored = _restore_cached_acoustic_interim(
        _session(index=6), 7, scores, decoded, [0.0], [[]], [None]
    )

    assert restored is False
    assert decoded == [""]


def test_one_new_final_decode_confirms_one_unmatched_substitution():
    session = {"last_interim_n_decoded": 6}
    assert _new_final_decode_budget(session, final_decoded=7, max_unanchored=2) == 1


def test_unchanged_final_decode_does_not_turn_silence_into_a_miss():
    session = {"last_interim_n_decoded": 6}
    assert _new_final_decode_budget(session, final_decoded=6, max_unanchored=2) == 0


def test_final_decode_evidence_is_capped_by_resync_limit():
    session = {"last_interim_n_decoded": 3}
    assert _new_final_decode_budget(session, final_decoded=20, max_unanchored=2) == 2


def test_no_interim_baseline_means_no_substitution_evidence():
    assert _new_final_decode_budget({}, final_decoded=1, max_unanchored=2) == 0
