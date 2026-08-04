"""Per-session acoustic backend selection: resolution, caching, and the wav2vec2 default.

Sessions pick a backend by name at start_session; ACOUSTIC_BACKEND is only the fallback for
clients that send nothing. Both backends can therefore be resident at once.

MuaalemBackend is stubbed rather than imported for real, so these run without quran-muaalem.
"""
import sys

import numpy as np
import pytest

from backend import acoustic_scorer
from backend.config import Config, config


AUDIO = np.zeros(1600, dtype=np.float32)


@pytest.fixture(autouse=True)
def _reset():
    acoustic_scorer._reset_backends()
    yield
    acoustic_scorer._reset_backends()


def _stub_muaalem(monkeypatch, sentinel):
    """Stand in for backend.acoustic_muaalem so the lazy import resolves without the library."""
    module = type("M", (), {"MuaalemBackend": lambda self=None: sentinel})()
    monkeypatch.setitem(sys.modules, "backend.acoustic_muaalem", module)


class TestNameResolution:
    def test_known_names_pass_through(self):
        assert acoustic_scorer.resolve_backend_name("muaalem") == "muaalem"
        assert acoustic_scorer.resolve_backend_name("wav2vec2") == "wav2vec2"

    def test_surrounding_whitespace_is_tolerated(self):
        assert acoustic_scorer.resolve_backend_name("  muaalem ") == "muaalem"

    @pytest.mark.parametrize("raw", [None, "", "something-else", 7, {}, "MUAALEM"])
    def test_unknown_values_fall_back_to_the_configured_default(self, raw, monkeypatch):
        # The wire value must never be trusted as a model id — it only selects one of a
        # fixed set of backends, so anything unrecognized falls back rather than erroring.
        monkeypatch.setattr(config, "acoustic_backend", "wav2vec2")
        assert acoustic_scorer.resolve_backend_name(raw) == "wav2vec2"

    def test_fallback_follows_the_configured_default(self, monkeypatch):
        monkeypatch.setattr(config, "acoustic_backend", "muaalem")
        assert acoustic_scorer.resolve_backend_name("nonsense") == "muaalem"


class TestBackendSelection:
    def test_defaults_to_the_configured_backend(self, monkeypatch):
        monkeypatch.setattr(config, "acoustic_backend", "wav2vec2")
        assert isinstance(acoustic_scorer.get_backend(), acoustic_scorer.Wav2Vec2Backend)

    def test_explicit_name_overrides_the_default(self, monkeypatch):
        monkeypatch.setattr(config, "acoustic_backend", "wav2vec2")
        sentinel = object()
        _stub_muaalem(monkeypatch, sentinel)
        assert acoustic_scorer.get_backend("muaalem") is sentinel

    def test_muaalem_is_imported_lazily(self, monkeypatch):
        # Nothing may import quran_muaalem until a session actually asks for it.
        monkeypatch.setattr(config, "acoustic_backend", "wav2vec2")
        monkeypatch.delitem(sys.modules, "backend.acoustic_muaalem", raising=False)
        acoustic_scorer.get_backend("wav2vec2")
        assert "backend.acoustic_muaalem" not in sys.modules

    def test_each_backend_is_cached_separately(self, monkeypatch):
        _stub_muaalem(monkeypatch, object())
        assert acoustic_scorer.get_backend("wav2vec2") is acoustic_scorer.get_backend("wav2vec2")
        assert acoustic_scorer.get_backend("muaalem") is acoustic_scorer.get_backend("muaalem")

    def test_both_backends_stay_resident_together(self, monkeypatch):
        # Two concurrent sessions on different models must not evict each other.
        _stub_muaalem(monkeypatch, object())
        w = acoustic_scorer.get_backend("wav2vec2")
        m = acoustic_scorer.get_backend("muaalem")
        assert w is not m
        assert acoustic_scorer.get_backend("wav2vec2") is w
        assert acoustic_scorer.get_backend("muaalem") is m

    def test_reset_drops_the_cache(self, monkeypatch):
        monkeypatch.setattr(config, "acoustic_backend", "wav2vec2")
        first = acoustic_scorer.get_backend()
        acoustic_scorer._reset_backends()
        assert acoustic_scorer.get_backend() is not first


class TestStartupPreload:
    """Startup loads both models, so switching mid-deployment never blocks on a download."""

    def _record_loads(self, monkeypatch, muaalem_load=None):
        loaded = []

        def _w2v_load(self):
            loaded.append("wav2vec2")

        def _mu_load(self):
            if muaalem_load is not None:
                muaalem_load()
            loaded.append("muaalem")

        monkeypatch.setattr(acoustic_scorer.Wav2Vec2Backend, "load", _w2v_load)
        _stub_muaalem(monkeypatch, type("Stub", (), {"load": _mu_load})())
        return loaded

    def test_every_backend_is_preloaded(self, monkeypatch):
        monkeypatch.setattr(config, "acoustic_backend", "wav2vec2")
        loaded = self._record_loads(monkeypatch)

        acoustic_scorer.load_model()

        assert sorted(loaded) == ["muaalem", "wav2vec2"]

    def test_the_default_is_loaded_first(self, monkeypatch):
        """A secondary model's slow download must not delay the one every session needs."""
        monkeypatch.setattr(config, "acoustic_backend", "muaalem")
        loaded = self._record_loads(monkeypatch)

        acoustic_scorer.load_model()

        assert loaded[0] == "muaalem"

    def test_a_failing_secondary_backend_does_not_break_startup(self, monkeypatch):
        monkeypatch.setattr(config, "acoustic_backend", "wav2vec2")

        def _boom():
            raise ImportError("quran_muaalem is not installed")

        loaded = self._record_loads(monkeypatch, muaalem_load=_boom)

        acoustic_scorer.load_model()  # must not raise

        assert loaded == ["wav2vec2"]

    def test_a_failing_default_backend_still_propagates(self, monkeypatch):
        """Without the default there is nothing to serve, so startup should fail loudly."""
        monkeypatch.setattr(config, "acoustic_backend", "wav2vec2")

        def _boom(self):
            raise RuntimeError("no weights")

        monkeypatch.setattr(acoustic_scorer.Wav2Vec2Backend, "load", _boom)
        _stub_muaalem(monkeypatch, type("Stub", (), {"load": lambda self: None})())

        with pytest.raises(RuntimeError):
            acoustic_scorer.load_model()


class TestThresholds:
    """Each backend carries its own cutoffs; muaalem's score distribution is more bimodal."""

    def test_wav2vec2_uses_the_global_thresholds(self, monkeypatch):
        monkeypatch.setattr(config, "score_threshold", 0.5)
        monkeypatch.setattr(config, "verse_detection_threshold", 0.61)
        backend = acoustic_scorer.get_backend("wav2vec2")
        assert backend.score_threshold == 0.5
        assert backend.verse_detection_threshold == 0.61

    def test_muaalem_uses_its_own_thresholds(self, monkeypatch):
        monkeypatch.setattr(config, "score_threshold", 0.5)
        monkeypatch.setattr(config, "muaalem_score_threshold", 0.82)
        monkeypatch.setattr(config, "muaalem_verse_detection_threshold", 0.33)
        # The real backend: it only touches the model in load()/score().
        from backend.acoustic_muaalem import MuaalemBackend

        backend = MuaalemBackend()
        assert backend.score_threshold == 0.82
        assert backend.verse_detection_threshold == 0.33


class TestConfigValidation:
    def test_rejects_unsupported_madd_aared_len(self):
        # quran_transcript cannot phonetize a 2-count leen madd; fail loudly at config time
        # instead of surfacing as `KeyError: 'ن'` from deep inside the library at runtime.
        with pytest.raises(ValueError, match="MOSHAF_MADD_AARED_LEN"):
            Config(moshaf_madd_aared_len=2)

    @pytest.mark.parametrize("aared", [4, 6])
    def test_accepts_supported_madd_aared_len(self, aared):
        assert Config(moshaf_madd_aared_len=aared)

    def test_madd_is_validated_even_when_the_default_is_wav2vec2(self):
        """Any session may select muaalem, so a bad moshaf must fail at startup, not mid-recitation."""
        with pytest.raises(ValueError, match="MOSHAF_MADD_AARED_LEN"):
            Config(acoustic_backend="wav2vec2", moshaf_madd_aared_len=2)

    def test_unknown_backend_is_rejected(self):
        with pytest.raises(ValueError, match="ACOUSTIC_BACKEND"):
            Config(acoustic_backend="bogus")

    def test_negative_unanchored_limit_is_rejected(self):
        with pytest.raises(ValueError, match="MUAALEM_CONTINUOUS_MAX_UNANCHORED_WORDS"):
            Config(muaalem_continuous_max_unanchored_words=-1)


class TestWav2Vec2BackendDelegates:
    def test_score_matches_the_module_level_implementation(self, monkeypatch):
        monkeypatch.setattr(acoustic_scorer, "_decode_audio", lambda _: ("بسم الله", []))
        expected = [("بِسْمِ", "بِسْمِ"), ("اللَّهِ", "اللَّهِ")]

        via_facade = acoustic_scorer.get_acoustic_scores(AUDIO, [], expected, model="wav2vec2")
        direct = acoustic_scorer._wav2vec2_scores(AUDIO, [], expected)
        assert via_facade.scores == direct.scores
        assert via_facade.best_words == direct.best_words

    def test_word_meta_is_accepted_and_ignored(self, monkeypatch):
        monkeypatch.setattr(acoustic_scorer, "_decode_audio", lambda _: ("بسم", []))
        expected = [("بِسْمِ", "بِسْمِ")]
        meta = [{"surah": 1, "ayah": 1, "word_index": 1}]
        assert (
            acoustic_scorer.get_acoustic_scores(AUDIO, [], expected, meta, "wav2vec2").scores
            == acoustic_scorer.get_acoustic_scores(AUDIO, [], expected, model="wav2vec2").scores
        )

    def test_muaalem_only_fields_default_empty_for_wav2vec2(self, monkeypatch):
        # The wav2vec2 word_result payload must stay byte-identical to what it was before
        # muaalem existed — main.py omits these keys entirely when the lists are empty.
        monkeypatch.setattr(acoustic_scorer, "_decode_audio", lambda _: ("بسم", []))
        res = acoustic_scorer.get_acoustic_scores(AUDIO, [], [("بِسْمِ", "بِسْمِ")], model="wav2vec2")
        assert res.tajweed_scores == []
        assert res.errors == []
        assert res.recited == []

    def test_detection_probe_drops_the_word_offsets(self, monkeypatch):
        # _decode_audio returns (text, offsets); verse detection compares text only.
        monkeypatch.setattr(
            acoustic_scorer, "_decode_audio", lambda _: ("بسم الله", [(0.0, 0.5), (0.5, 1.0)])
        )
        assert acoustic_scorer.detection_probe(AUDIO, [], "wav2vec2") == "بسم الله"
