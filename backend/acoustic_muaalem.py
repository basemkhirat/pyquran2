"""Acoustic scoring via the Muaalem model. Selected per session by `model="muaalem"`.

Unlike the wav2vec2 backend, this model decodes Quran Phonetic Script (QPS) rather than
Arabic text, so a word cannot be scored by comparing letters. Instead:

  1. the expected words are phonetized into a reference (`quran_transcript.quran_phonetizer`),
  2. the model decodes the audio into predicted phonemes,
  3. `quran_transcript.explain_error` aligns the two and returns pronunciation errors, each
     classified as tajweed / tashkeel / normal and mapped to a span of the reference text,
  4. each error is attributed to the word whose characters it overlaps, and a word's score is
     1 - (share of its characters carrying errors).

Two facts drive the design and are easy to break:

* **The reference text must come from `quran_transcript`, not hafs.json.** They use different
  Uthmani presentation conventions (U+06E1 vs U+0652 for sukoon, and ~75 other contextual
  differences); feeding hafs.json text to the phonetizer raises IndexError on ~51% of verses.
  Their *uthmani* word counts agree for every verse but 15:7, so the mapping is positional.
  Their *imlaey* word counts disagree for 364 verses -- do not map through imlaey.
* **The model's reference argument does not bias decoding.** Phonemes are decoded greedily
  from the audio alone; the reference is only used afterwards to align the sifat levels. So
  the scores are honest, and verse detection can read the phonemes with any plausible
  reference passed in.

The library is imported inside functions so this module (and the tests) import cleanly
without `quran-muaalem` installed. `_phonetize`, `_run_muaalem` and `_explain` are the only
functions that touch it -- everything else is pure and unit-testable.
"""
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from backend import quran_data
from backend.acoustic_scorer import (
    AcousticBackend,
    AcousticResult,
    RecitedUnit,
    TajweedRule,
    WordError,
)
from backend.config import config
from backend.scorer import compute_text_score
from backend.terminal_arabic import display_arabic

logger = logging.getLogger(__name__)

# An inserted phoneme run has no reference span, so it cannot be charged by overlap. Charge it
# a flat character-equivalent instead, capped so a long hallucinated run can't dominate a word
# that was otherwise recited correctly.
_INSERT_PENALTY_CAP = 2

_ERROR_TYPES = ("normal", "tashkeel", "tajweed")

# Words phonetized per verse-detection candidate. Generous enough to overshoot a typical
# opening utterance (which is then trimmed to the probe), small enough to stay cheap.
_DETECTION_WINDOW_WORDS = 25


# --- library seams (the only functions that touch quran_muaalem / quran_transcript) -------


@lru_cache(maxsize=1)
def _moshaf():
    """The recitation style the reference is phonetized for. Immutable per process."""
    from quran_transcript import MoshafAttributes

    return MoshafAttributes(
        rewaya=config.moshaf_rewaya,
        madd_monfasel_len=config.moshaf_madd_monfasel_len,
        madd_mottasel_len=config.moshaf_madd_mottasel_len,
        madd_mottasel_waqf=config.moshaf_madd_mottasel_waqf,
        madd_aared_len=config.moshaf_madd_aared_len,
    )


@lru_cache(maxsize=4096)
def _phonetize(uthmani_text: str):
    """Phonetize Uthmani text into the reference script (QuranPhoneticScriptOutput).

    Cached because this is called for the same chunk on every streaming tick, and for every
    candidate verse on every tick until detection commits -- without the cache it would run
    tens of times a second. The returned object is treated as immutable.
    """
    from quran_transcript import quran_phonetizer

    return quran_phonetizer(uthmani_text, _moshaf(), remove_spaces=True)


@lru_cache(maxsize=4096)
def _spaced_phonemes(uthmani_text: str) -> str:
    """Reference phonemes WITH word-boundary spaces (remove_spaces=False).

    Where two words merge phonetically the space between them is absent, so splitting on
    spaces yields the recitation's real phonetic groups. Stripping the spaces reproduces
    `_phonetize(...).phonemes` exactly (verified across the whole Quran).
    """
    from quran_transcript import quran_phonetizer

    return quran_phonetizer(uthmani_text, _moshaf(), remove_spaces=False).phonemes


def _run_muaalem(waves: List[np.ndarray], refs: List[Any], sampling_rate: int) -> List[Any]:
    """Run the model on 16kHz float32 mono audio. Returns one MuaalemOutput per wave."""
    return _get_model()(waves, refs, sampling_rate=sampling_rate)


def _explain(uthmani_text: str, ref_ph_text: str, predicted_ph_text: str, mappings) -> List[Any]:
    """Align predicted phonemes against the reference and classify the differences."""
    from quran_transcript import explain_error

    return explain_error(
        uthmani_text=uthmani_text,
        ref_ph_text=ref_ph_text,
        predicted_ph_text=predicted_ph_text,
        mappings=mappings,
    )


_model = None


def _patch_muaalem_library() -> None:
    """Fix a known arity bug in quran_muaalem==0.1.0 so short/empty decodes don't crash.

    `quran_muaalem.decode.align_predicted_sequence` is documented (and used) as returning an
    ``(aligned_ids, mask)`` pair, but its empty-`predicted` branch returns a bare list:
    ``if m == 0: return [missing_placeholder] * n``. The caller then does
    ``ref_aligned_ids, mask = align_predicted_sequence(...)``, which raises
    ``ValueError: too many values to unpack (expected 2)``. This fires whenever a sifat level
    decodes to nothing (short or unclear audio -- e.g. a brief final streaming chunk).

    We wrap the function to return the correct 2-tuple for the empty case and delegate to the
    original otherwise. `multilevel_greedy_decode` resolves the name from the module's globals,
    so patching the module attribute is enough. Idempotent.
    """
    import quran_muaalem.decode as decode_mod

    if getattr(decode_mod.align_predicted_sequence, "_pyquran_patched", False):
        return
    original = decode_mod.align_predicted_sequence

    def patched(ref, predicted, missing_placeholder=-100):
        if len(predicted) == 0:
            return [missing_placeholder] * len(ref), []
        return original(ref, predicted, missing_placeholder=missing_placeholder)

    patched._pyquran_patched = True
    decode_mod.align_predicted_sequence = patched
    logger.info("Patched quran_muaalem.decode.align_predicted_sequence (empty-decode arity bug)")


def _get_model():
    global _model
    if _model is None:
        import torch
        from quran_muaalem import Muaalem

        _patch_muaalem_library()

        device = config.muaalem_device or ("cuda" if torch.cuda.is_available() else "cpu")
        if config.muaalem_dtype:
            dtype = getattr(torch, config.muaalem_dtype)
        else:
            # The library defaults to bfloat16, which is right on an L4 but a trap on CPU:
            # bf16 CPU kernels are incomplete/slow for this model's conv frontend.
            dtype = torch.bfloat16 if device == "cuda" else torch.float32
        logger.info(
            "Loading Muaalem from %s (device=%s, dtype=%s)", config.muaalem_model, device, dtype
        )
        if device == "cpu":
            logger.warning("Muaalem on CPU is impractically slow for live scoring; expect timeouts.")
        _model = Muaalem(model_name_or_path=config.muaalem_model, device=device, dtype=dtype)
    return _model


# quran_transcript's ghunnah phonemes: alphabet.py builds `phonetic_groups.ghonna` as
# noon + meem + noon_mokhfah + meem_mokhfah. Held nasals are written as a *run* of these --
# `operations.ghonna_len = 3` frames for the ~2-count hold -- exactly the way a madd repeats
# ۦ/ۥ. Hardcoded rather than imported so this module stays importable without the library.
_GHONNA_PHONEMES = frozenset("نمں۾")  # ن م ں ۾


def _is_ghunnah_duration(err: WordError) -> bool:
    """True when the difference is only in how a ghunnah was held, not which letters were said.

    explain_error classifies a *madd* length difference as `tajweed` but a *ghunnah* one as
    `normal`, so a shortened ikhfa is charged as a wrong letter and fails the word outright:
    measured over the recorded sessions, 49 of ~340 scored words (مِن دون 0.62, يُنفِقُونَ 0.88)
    versus madd runs that cost nothing. Both are duration, so both belong on the tajweed axis.

    The reference side must be a run of one nasal (a hold, not a single letter), and whatever
    was produced must be nasal too -- empty, shorter, longer, or the neighbouring nasal, as when
    an ikhfa meem comes back as a plain م. Anything else is a genuine letter error.
    """
    expected = err.expected_ph
    if len(expected) < 2 or len(set(expected)) != 1:
        return False
    if not set(expected) <= _GHONNA_PHONEMES:
        return False
    return set(err.predicted_ph) <= _GHONNA_PHONEMES


def _to_word_error(err: Any) -> WordError:
    """Adapt a quran_transcript ReciterError to our own type.

    Keeping our own type is what lets the attribution logic below be unit-tested without the
    model or the library. Note `preditected_ph` -- the misspelling is theirs.
    """
    rules = [
        TajweedRule(
            name_ar=getattr(r, "name_ar", "") or "",
            name_en=getattr(r, "name_en", "") or "",
            golden_len=getattr(r, "golden_len", None),
            correctness_type=getattr(r, "correctness_type", None),
        )
        for r in (getattr(err, "ref_tajweed_rules", None) or [])
    ]
    error = WordError(
        error_type=err.error_type,
        speech_error_type=err.speech_error_type,
        expected_ph=getattr(err, "expected_ph", "") or "",
        predicted_ph=getattr(err, "preditected_ph", "") or "",
        expected_len=getattr(err, "expected_len", None),
        predicted_len=getattr(err, "predicted_len", None),
        rules=rules,
    )
    # Ghunnah duration reads as a letter error out of the library; move it onto the tajweed
    # axis, where madd duration already sits. Only from `normal` -- an error the library has
    # already classified as tajweed or tashkeel is left exactly as it came.
    if error.error_type == "normal" and _is_ghunnah_duration(error):
        error.error_type = "tajweed"
    return error


# --- reference text -----------------------------------------------------------------------


@lru_cache(maxsize=8192)
def _qt_verse_words(surah: int, ayah: int) -> Optional[Tuple[str, ...]]:
    """quran_transcript's Uthmani words for a verse, positionally matching hafs.json words.

    Returns None when the two disagree on word count (only 15:7 today, where hafs.json fuses
    `لَّوۡمَا` into one word), because indexing past that point would silently score the wrong
    words. Callers must treat None as "cannot score this chunk".
    """
    from quran_transcript import Aya

    qt = tuple(Aya(surah, ayah).get().uthmani.split())
    hafs_n = len(quran_data.get_words_range(surah, ayah, surah, ayah))
    if len(qt) != hafs_n:
        logger.warning(
            "Verse %d:%d word-count mismatch (hafs=%d, quran_transcript=%d); "
            "skipping acoustic scoring for it",
            surah, ayah, hafs_n, len(qt),
        )
        return None
    return qt


def _reference_words(word_meta: Sequence[Dict[str, Any]]) -> Optional[List[str]]:
    """Uthmani text for each word, sourced from quran_transcript. None if any verse can't map."""
    out: List[str] = []
    for meta in word_meta:
        verse = _qt_verse_words(meta["surah"], meta["ayah"])
        if verse is None:
            return None
        i = meta["word_index"] - 1  # hafs word_index is 1-based
        if not 0 <= i < len(verse):
            logger.warning(
                "Word index %d out of range for %d:%d (%d words)",
                meta["word_index"], meta["surah"], meta["ayah"], len(verse),
            )
            return None
        out.append(verse[i])
    return out


def _pad_words(last: Dict[str, Any], n: int) -> List[str]:
    """Up to `n` words following `last` within its verse.

    quran_phonetizer applies waqf (pause) rules at the end of whatever text it is given, so a
    chunk cut mid-verse would have its final word judged against a pause the reciter never
    made. These padding words absorb that and are dropped before scoring.
    """
    if n <= 0:
        return []
    verse = _qt_verse_words(last["surah"], last["ayah"])
    if verse is None:
        return []
    start = last["word_index"]  # 1-based index of `last` == 0-based index of the next word
    return list(verse[start : start + n])


# --- pure helpers (the actual algorithm; no model, no library) ----------------------------


def _word_spans(words: Sequence[str]) -> List[Tuple[int, int]]:
    """Character span of each word within `" ".join(words)`."""
    spans: List[Tuple[int, int]] = []
    pos = 0
    for w in words:
        spans.append((pos, pos + len(w)))
        pos += len(w) + 1  # the joining space
    return spans


def _overlap(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    """Number of characters two spans share."""
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def _locate_insert(pos: int, spans: Sequence[Tuple[int, int]]) -> Optional[int]:
    """Index of the word an inserted (zero-width) error belongs to.

    Inserts land inside a word, or in the gap between two. A gap insert is charged to the
    *preceding* word: the reciter added something after finishing it.
    """
    if not spans:
        return None
    if pos <= spans[0][0]:
        return 0
    for i, (s, e) in enumerate(spans):
        if s < pos <= e:
            return i
    preceding = 0
    for i, (_s, e) in enumerate(spans):
        if e <= pos:
            preceding = i
    return preceding


def _attribute_errors(
    errors: Sequence[WordError],
    error_spans: Sequence[Tuple[int, int]],
    word_spans: Sequence[Tuple[int, int]],
) -> Tuple[List[List[WordError]], List[Dict[str, int]], List[int]]:
    """Attribute each error to the words it overlaps.

    Returns, parallel to word_spans: the errors touching each word, the penalty per
    error_type (in reference characters), and how many characters `delete` errors cover.
    """
    per_word: List[List[WordError]] = [[] for _ in word_spans]
    penalties: List[Dict[str, int]] = [{t: 0 for t in _ERROR_TYPES} for _ in word_spans]
    deleted: List[int] = [0] * len(word_spans)

    for err, span in zip(errors, error_spans):
        start, end = span
        if start == end:
            # Insert: no reference span to overlap, so charge a flat capped penalty.
            i = _locate_insert(start, word_spans)
            if i is None:
                continue
            per_word[i].append(err)
            cost = min(len(err.predicted_ph) or 1, _INSERT_PENALTY_CAP)
            penalties[i][err.error_type] = penalties[i].get(err.error_type, 0) + cost
            continue
        for i, wspan in enumerate(word_spans):
            ov = _overlap(span, wspan)
            if ov <= 0:
                continue
            per_word[i].append(err)
            penalties[i][err.error_type] = penalties[i].get(err.error_type, 0) + ov
            if err.speech_error_type == "delete":
                deleted[i] += ov
    return per_word, penalties, deleted


# Deletes never cover an unspoken word completely: characters that produce no phonemes are
# never part of a delete span, and they sit at a word's edges as well as between its deletes
# (e.g. the ٱل of ٱلرَّحْمَـٰنِ assimilates into the following shadda). Measured against real
# explain_error output, a fully unspoken word lands around 0.7 coverage while a word that was
# spoken with a dropped phoneme lands under 0.1, so the two separate cleanly here.
_UNRECITED_MIN_COVERAGE = 0.5


def _is_unrecited(errs: Sequence[WordError], deleted: int, span_len: int) -> bool:
    """True when a word was never spoken, as opposed to spoken badly.

    This is the load-bearing rule of this module. The expected chunk runs up to 20 words
    ahead of where the reciter actually is, and explain_error aligns the *whole* reference,
    so every word not yet reached comes back deleted. Scoring those as 0.0 would paint the
    rest of the verse red on every streaming tick.

    Two conditions, and the first does most of the work: a word the reciter actually attempted
    picks up a `replace` or `insert` (the model decoded *something* for it), so an all-`delete`
    word is one no audio was aligned to. The coverage floor then rules out a word that was
    spoken with a phoneme or two dropped. Callers map True to best_word="" -- the same signal
    wav2vec2 gives for an unmatched word, which main.py's `last_matched` uses to stop
    processing at the end of what was actually said.
    """
    if not errs or span_len <= 0:
        return False
    if not all(e.speech_error_type == "delete" for e in errs):
        return False
    return deleted >= span_len * _UNRECITED_MIN_COVERAGE


def _score_word(penalty: Dict[str, int], span_len: int) -> Tuple[float, float, float, float]:
    """Return (total, char, diacritic, tajweed) scores in [0, 1] for one word.

    The char/diacritic blend reuses the text scorer so this backend and wav2vec2 weight the
    two the same way. Tajweed is a third axis wav2vec2 cannot measure at all; it is folded in
    only if MUAALEM_WEIGHT_TAJWEED is raised above its 0 default, so by default tajweed
    errors are reported to the UI without failing a word.
    """
    denom = max(1, span_len)

    def sub(kind: str) -> float:
        return max(0.0, 1.0 - min(1.0, penalty.get(kind, 0) / denom))

    char_score = sub("normal")
    diac_score = sub("tashkeel")
    tajweed_score = sub("tajweed")

    base = compute_text_score(char_score, diac_score)
    w = config.muaalem_weight_tajweed
    total = base if w <= 0 else (1.0 - w) * base + w * tajweed_score
    return total, char_score, diac_score, tajweed_score


def _word_groups(words: Sequence[str]) -> Optional[List[List[int]]]:
    """Group words into phonetic units. Returns per-group lists of word indices.

    Adjacent words merge phonetically (idgham, hamzat wasl); a group holding more than one
    word is such a merge. Merge decisions are local to a boundary, so phonetizing each growing
    prefix reveals whether that prefix's final boundary survived -- verified to reproduce the
    full text's grouping across the whole Quran. Returns None on an unexpected group-count
    jump (never observed, but keeps the caller from trusting a bad grouping).
    """
    if not words:
        return []
    groups: List[List[int]] = [[0]]
    g_prev = 1
    for i in range(1, len(words)):
        g_now = _spaced_phonemes(" ".join(words[: i + 1])).count(" ") + 1
        if g_now - g_prev == 1:
            groups.append([i])            # boundary survived -> new unit
        elif g_now == g_prev:
            groups[-1].append(i)           # merged into the previous unit
        else:
            return None
        g_prev = g_now
    return groups


def _reconstruct_span(
    ref_ph: str, ph_edits: Sequence[Tuple[int, int, str]], span: Tuple[int, int]
) -> str:
    """Rebuild the predicted phonemes for one reference span from the reference + edits.

    `ph_edits` are (ref_start, ref_end, predicted_ph) in spaceless reference-phoneme coords;
    an insert has ref_start == ref_end. Between edits the prediction equals the reference, so
    splicing the edits into the reference reconstructs exactly what the model decoded for that
    span (verified: concatenating every span reproduces the model's output string).
    """
    ps0, pe0 = span
    out: List[str] = []
    cursor = ps0
    for ps, pe, pred in sorted(ph_edits):
        if pe < ps0 or ps > pe0:
            continue
        if ps == pe:                       # insert: only if strictly inside the span
            if not (ps0 <= ps < pe0):
                continue
        elif pe <= ps0 or ps >= pe0:       # replace/delete not overlapping the span
            continue
        s = max(cursor, min(ps, pe0))
        out.append(ref_ph[cursor:s])
        out.append(pred)
        cursor = min(max(cursor, pe), pe0)
    out.append(ref_ph[cursor:pe0])
    return "".join(out)


def _assemble_recited(
    scored_words: Sequence[str],
    ref_phonemes: str,
    groups_ph: Sequence[str],
    word_groups: Sequence[Sequence[int]],
    ph_edits: Sequence[Tuple[int, int, str]],
) -> List[Optional[RecitedUnit]]:
    """Build a RecitedUnit per word (the merged words of a unit share one instance)."""
    spans: List[Tuple[int, int]] = []
    pos = 0
    for g in groups_ph:
        spans.append((pos, pos + len(g)))
        pos += len(g)

    units: List[Optional[RecitedUnit]] = [None] * len(scored_words)
    for gi, members in enumerate(word_groups):
        span = spans[gi]
        unit = RecitedUnit(
            ph=_reconstruct_span(ref_phonemes, ph_edits, span),
            expected_ph=ref_phonemes[span[0]:span[1]],
            words=[scored_words[wi] for wi in members],
        )
        for wi in members:
            units[wi] = unit
    return units


def _build_recited(
    scored_words: Sequence[str], ref_phonemes: str, ph_edits: Sequence[Tuple[int, int, str]]
) -> Optional[List[Optional[RecitedUnit]]]:
    """Per-word recited phonemes for the chunk, or None if the grouping can't be trusted."""
    spaced = _spaced_phonemes(" ".join(scored_words))
    if spaced.replace(" ", "") != ref_phonemes:
        return None
    groups_ph = spaced.split(" ")
    word_groups = _word_groups(scored_words)
    if word_groups is None or len(word_groups) != len(groups_ph):
        return None
    return _assemble_recited(scored_words, ref_phonemes, groups_ph, word_groups, ph_edits)


def _neutral(n: int) -> AcousticResult:
    """Fallback when the audio decodes to nothing, mirroring the wav2vec2 backend's 0.5."""
    return AcousticResult(
        scores=[0.5] * n,
        char_scores=[0.5] * n,
        diac_scores=[0.5] * n,
        best_words=[""] * n,
        n_decoded=0,
        offsets=[None] * n,
        tajweed_scores=[0.5] * n,
        errors=[[] for _ in range(n)],
    )


def _score_current_locally(
    predicted: str, ref_words: Sequence[str], current_index: int
) -> Optional[Tuple[float, float, float, float, List[WordError], Optional[RecitedUnit]]]:
    """Score the current word against a prefix-only alignment.

    The full streaming reference contains up to 20 future words. If the reciter substitutes
    the current word with text that occurs later (for example ``يعلمون`` for ``يشعرون``), the
    global edit alignment can leap to that exact future occurrence and report the current word
    as wholly unrecited. Re-aligning the same model decode against only the already-confirmed
    prefix plus the current word removes that ambiguity without running the model again.

    Returns None when the local alignment also says the current word is genuinely unrecited.
    """
    local_words = list(ref_words[: current_index + 1])
    if not local_words or not 0 <= current_index < len(local_words):
        return None

    uthmani_text = " ".join(local_words)
    spans = _word_spans(local_words)
    ref = _phonetize(uthmani_text)
    raw = _explain(uthmani_text, ref.phonemes, predicted, ref.mappings)
    converted = [_to_word_error(e) for e in raw]
    error_spans = [tuple(e.uthmani_pos) for e in raw]
    per_word, penalties, deleted = _attribute_errors(converted, error_spans, spans)

    span = spans[current_index]
    span_len = span[1] - span[0]
    if _is_unrecited(per_word[current_index], deleted[current_index], span_len):
        return None

    total, cs, ds, ts = _score_word(penalties[current_index], span_len)
    current_recited: Optional[RecitedUnit] = None
    try:
        ph_edits = [(e.ph_pos[0], e.ph_pos[1], e.preditected_ph or "") for e in raw]
        rebuilt = _build_recited(local_words, ref.phonemes, ph_edits)
        if rebuilt:
            current_recited = rebuilt[current_index]
    except Exception:  # noqa: BLE001 - display-only; the local score is still valid
        logger.debug("Local current-word recited reconstruction failed", exc_info=True)

    return total, cs, ds, ts, per_word[current_index], current_recited


class MuaalemBackend(AcousticBackend):
    """Phoneme + tajweed scoring via obadx/muaalem-model-v3_2."""

    name = "muaalem"

    @property
    def verse_detection_threshold(self) -> float:
        # Phoneme strings are longer and finer-grained than the base-letter text
        # VERSE_DETECTION_THRESHOLD was calibrated against, so muaalem carries its own.
        return config.muaalem_verse_detection_threshold

    @property
    def score_threshold(self) -> float:
        # Scores here come from discrete pronunciation errors rather than a smooth CER
        # blend, so the distribution is more bimodal and needs its own cutoff.
        return config.muaalem_score_threshold

    def load(self) -> None:
        _get_model()

    def score(self, audio, previous_words, expected_words, word_meta=None) -> AcousticResult:
        n_expected = len(expected_words)
        if n_expected == 0:
            return AcousticResult([], [], [], [], 0, [])
        if word_meta is None:
            raise ValueError(
                "The muaalem backend needs word_meta (surah/ayah/word_index per word) to look "
                "up reference text; pass the quran_data word dicts to get_acoustic_scores()."
            )
        n_all = len(previous_words) + n_expected
        if len(word_meta) != n_all:
            raise ValueError(
                f"word_meta has {len(word_meta)} entries, expected {n_all} "
                "(previous_words + expected_words)"
            )

        ref_words = _reference_words(word_meta)
        if ref_words is None:
            return _neutral(n_expected)

        pad = _pad_words(word_meta[-1], config.muaalem_context_pad_words)
        scored_words = ref_words + pad
        uthmani_text = " ".join(scored_words)
        spans = _word_spans(scored_words)

        ref = _phonetize(uthmani_text)
        # The model + phoneme aligner are third-party; a single odd chunk must not kill the
        # streaming loop. Degrade to neutral scores (like an empty decode) on any failure.
        try:
            out = _run_muaalem([audio], [ref], config.audio_sample_rate)[0]
            predicted = (out.phonemes.text or "").strip()
        except Exception:  # noqa: BLE001 - external decoder; keep the session alive
            logger.warning("muaalem decode failed for this chunk; using neutral scores",
                           exc_info=True)
            return _neutral(n_expected)
        logger.info("  muaalem decoded: '%s'", predicted)
        if not predicted:
            return _neutral(n_expected)

        raw = _explain(uthmani_text, ref.phonemes, predicted, ref.mappings)
        errors = [_to_word_error(e) for e in raw]
        error_spans = [tuple(e.uthmani_pos) for e in raw]
        per_word, penalties, deleted = _attribute_errors(errors, error_spans, spans)

        # What the reciter actually produced, per word (merged neighbours share a unit).
        # Never let this optional display detail break scoring: degrade to no recited on error.
        recited: List[Optional[RecitedUnit]] = [None] * len(scored_words)
        try:
            ph_edits = [(e.ph_pos[0], e.ph_pos[1], e.preditected_ph or "") for e in raw]
            recited = _build_recited(scored_words, ref.phonemes, ph_edits) or recited
        except Exception:  # noqa: BLE001 - display-only; scoring must survive any phonetizer hiccup
            logger.warning("muaalem recited reconstruction failed; omitting recited phonemes",
                           exc_info=True)

        scores: List[float] = []
        char_scores: List[float] = []
        diac_scores: List[float] = []
        tajweed_scores: List[float] = []
        best_words: List[str] = []
        word_errors: List[List[WordError]] = []

        for i, (word, span) in enumerate(zip(scored_words, spans)):
            span_len = span[1] - span[0]
            if _is_unrecited(per_word[i], deleted[i], span_len):
                scores.append(0.0)
                char_scores.append(0.0)
                diac_scores.append(0.0)
                tajweed_scores.append(0.0)
                best_words.append("")
                word_errors.append([])
                continue
            total, cs, ds, ts = _score_word(penalties[i], span_len)
            scores.append(total)
            char_scores.append(cs)
            diac_scores.append(ds)
            tajweed_scores.append(ts)
            best_words.append(word)
            word_errors.append(per_word[i])

        # The first not-yet-confirmed word is the only one allowed a prefix-only rescue. Future
        # words remain governed by the global alignment + continuous resync guard, so this cannot
        # pull the cursor forward. This specifically handles a spoken substitution that happens
        # to match a later Quran word exactly.
        current_i = len(previous_words)
        if current_i < len(ref_words) and not best_words[current_i]:
            try:
                local = _score_current_locally(predicted, ref_words, current_i)
            except Exception:  # noqa: BLE001 - supplementary alignment; keep global result
                logger.warning("Muaalem local current-word alignment failed", exc_info=True)
                local = None
            if local is not None:
                total, cs, ds, ts, local_errors, local_recited = local
                scores[current_i] = total
                char_scores[current_i] = cs
                diac_scores[current_i] = ds
                tajweed_scores[current_i] = ts
                best_words[current_i] = ref_words[current_i]
                word_errors[current_i] = local_errors
                if local_recited is not None:
                    recited[current_i] = local_recited
                logger.info(
                    "  muaalem local rescue: current '%s' was spoken but globally aligned ahead",
                    display_arabic(ref_words[current_i]),
                )

        # Drop the padding words, then the already-confirmed ones, leaving expected_words.
        end = len(ref_words)
        k = len(previous_words)
        n_decoded = sum(1 for w in best_words[:end] if w)

        for word, score, best in zip(ref_words, scores, best_words):
            logger.debug(
                "  muaalem: expected='%s' score=%.2f recited=%s",
                display_arabic(word), score, bool(best),
            )

        return AcousticResult(
            scores=scores[k:end],
            char_scores=char_scores[k:end],
            diac_scores=diac_scores[k:end],
            best_words=best_words[k:end],
            n_decoded=n_decoded,
            # Muaalem decodes one continuous phoneme stream with no frame offsets per word,
            # so recorded sessions fall back to main.py's char-proportional timing cursor.
            offsets=[None] * len(scores[k:end]),
            tajweed_scores=tajweed_scores[k:end],
            errors=word_errors[k:end],
            recited=recited[k:end],
        )

    def detection_probe(self, audio: np.ndarray, words=None) -> str:
        """Predicted phonemes for the utterance, to match candidate verses against.

        The model requires a reference, but does not decode against it (it only aligns the
        sifat levels afterwards), so any valid reference yields the same phonemes. We pass
        the range's opening words rather than a dummy: it is free thanks to the phonetize
        cache, and keeps the sifat alignment on a code path we don't control well-behaved.
        """
        if not words:
            return ""
        ref_words = _reference_words(words[:_DETECTION_WINDOW_WORDS])
        if ref_words is None:
            return ""
        ref = _phonetize(" ".join(ref_words))
        out = _run_muaalem([audio], [ref], config.audio_sample_rate)[0]
        return (out.phonemes.text or "").strip()

    def detection_reference(self, words, start, probe) -> str:
        """Reference phonemes from `words[start]`, trimmed to the probe's length.

        wav2vec2 windows by decoded word count; phonemes have no word boundaries (the
        phonetizer is called with remove_spaces=True), so we phonetize a fixed generous
        window and trim to the probe instead. The window is constant per start index, so the
        phonetize cache makes this one call per candidate per session rather than per tick.
        """
        ref_words = _reference_words(words[start : start + _DETECTION_WINDOW_WORDS])
        if ref_words is None:
            return ""
        return _phonetize(" ".join(ref_words)).phonemes[: len(probe)]
