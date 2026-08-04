import os
import re
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

# Project root (parent of backend/) so relative paths work when cwd is not project root (e.g. Modal)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean env var. Accepts 1/true/yes/on (case-insensitive); unset -> default."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# --- Diacritics (tashkeel) ---------------------------------------------------------------
# Diacritics split into "scored" and "non-scored":
#   scored     -> counted in the diacritic-accuracy score and kept during acoustic scoring
#   non-scored -> ignored everywhere (stripped before comparison)
#
# Toggle any scored mark on/off with its env var (e.g. SCORE_SUKOON=false). A disabled mark
# automatically moves to the non-scored set, so it stops affecting any score. To score a mark
# that isn't listed yet, add a row below -- no other code changes are needed.
#
# name -> (unicode char, enabled?). Edit a default or flip a toggle here to change scoring.
SCORABLE_DIACRITICS = {
    "fatha":  ("\u064E", _env_bool("SCORE_FATHA",  True)),
    "damma":  ("\u064F", _env_bool("SCORE_DAMMA",  True)),
    "kasra":  ("\u0650", _env_bool("SCORE_KASRA",  True)),
    "shadda": ("\u0651", _env_bool("SCORE_SHADDA", True)),
    "sukoon": ("\u0652", _env_bool("SCORE_SUKOON", True)),
}

# Full range of diacritics the scorer recognises: harakat, tanween, shadda, sukoon,
# the U+0653-U+065E combining block (maddah, combining hamza, subscript alef, inverted
# damma, Uthmani "fatha with two dots" tanween, etc.), superscript alef, and Quranic
# annotation marks. Anything here that is not an enabled scored mark is treated as
# non-scored (stripped before comparison).
_ALL_DIACRITICS_RANGE = "\u0617-\u061A\u064B-\u065E\u0670\u06D6-\u06ED"

# Characters of the currently-enabled scored marks.
_SCORED_CHARS = "".join(char for char, enabled in SCORABLE_DIACRITICS.values() if enabled)

# Matches only enabled scored marks (matches nothing when every mark is disabled).
SCORED_DIACRITICS = re.compile(f"[{_SCORED_CHARS}]" if _SCORED_CHARS else r"(?!x)x")

# Matches any recognised diacritic that is NOT an enabled scored mark (used to strip them).
NON_SCORED_DIACRITICS = re.compile(
    f"(?![{_SCORED_CHARS}])[{_ALL_DIACRITICS_RANGE}]" if _SCORED_CHARS
    else f"[{_ALL_DIACRITICS_RANGE}]"
)
# U+06E1 (ۡ) is alternate sukoon; normalize to U+0652 (ْ) for comparison
SUKOON_VARIANT = "\u06E1"
SUKOON_STANDARD = "\u0652"


def normalize_sukoon(text: str) -> str:
    """Replace alternate sukoon (ۡ U+06E1) with standard (ْ U+0652)."""
    return text.replace(SUKOON_VARIANT, SUKOON_STANDARD)


def _resolve_path(path: str) -> str:
    """Resolve relative file paths against project root."""
    if path.startswith("."):
        return os.path.abspath(os.path.join(_PROJECT_ROOT, path))
    return path


# Acoustic scoring backends. "wav2vec2" decodes Arabic text and scores it by character/diacritic
# accuracy; "muaalem" decodes Quran Phonetic Script and derives scores from classified
# tajweed/tashkeel/normal pronunciation errors. Chosen per session via start_session's `model`
# field; ACOUSTIC_BACKEND below only supplies the default when the client omits it.
ACOUSTIC_BACKENDS = frozenset({"wav2vec2", "muaalem"})


@dataclass
class Config:
    hf_model_path: str = os.getenv("HF_MODEL_PATH", "./models/whisper-quran-v1")
    hafs_json_path: str = os.getenv("HAFS_JSON_PATH", "./assets/narrations/hafs.json")
    weight_char: float = float(os.getenv("WEIGHT_CHAR", "0.75"))
    weight_diacritic: float = float(os.getenv("WEIGHT_DIACRITIC", "0.25"))
    enable_text_score: bool = os.getenv("ENABLE_TEXT_SCORE", "false").lower() in ("1", "true", "yes")
    weight_text: float = float(os.getenv("WEIGHT_TEXT", "0.7"))
    enable_acoustic_score: bool = os.getenv("ENABLE_ACOUSTIC_SCORE", "true").lower() in ("1", "true", "yes")
    weight_acoustic: float = float(os.getenv("WEIGHT_ACOUSTIC", "0.3"))
    wav2vec2_quran_asr_model: str = os.getenv(
        "WAV2VEC2_QURAN_ASR_MODEL", "HamzaSidhu786/wav2vec2-base-word-by-word-quran-asr"
    )
    # Default acoustic model for sessions that don't pick one: "wav2vec2" (CTC over Arabic
    # text) or "muaalem" (obadx/muaalem-model-v3_2 -- phonemes + tajweed/sifat error detection).
    # A client overrides it per session with start_session's `model` field.
    acoustic_backend: str = os.getenv("ACOUSTIC_BACKEND", "wav2vec2")
    # Hugging Face id for the Muaalem model (not path-resolved; hub id only).
    muaalem_model: str = os.getenv("MUAALEM_MODEL", "obadx/muaalem-model-v3_2")
    # Empty -> auto ("cuda" when available, else "cpu"). Muaalem is impractically slow on CPU.
    muaalem_device: str = os.getenv("MUAALEM_DEVICE", "")
    # Empty -> bfloat16 on cuda, float32 on cpu (CPU bf16 kernels are slow/incomplete).
    muaalem_dtype: str = os.getenv("MUAALEM_DTYPE", "")
    # How much a word's tajweed accuracy pulls its total score. 0 (default) surfaces tajweed
    # errors in the UI without letting them fail a word -- the wav2vec2 backend cannot detect
    # tajweed at all, so gating on it would drop pass rates for unchanged recitation.
    muaalem_weight_tajweed: float = float(os.getenv("MUAALEM_WEIGHT_TAJWEED", "0.0"))
    # Muaalem's score distribution is more bimodal than wav2vec2's smooth CER blend, so it
    # gets its own cutoff instead of reusing the wav2vec2-calibrated SCORE_THRESHOLD.
    muaalem_score_threshold: float = float(os.getenv("MUAALEM_SCORE_THRESHOLD", "0.76"))
    # In acoustic-only continuous mode, do not let one distant false match drag the cursor
    # across an arbitrary run of low-confidence words. This many consecutive misses may be
    # bridged while looking for a nearby confident resynchronization anchor. Applies to both
    # backends; the MUAALEM_-prefixed name is still read so existing .env files keep working.
    continuous_max_unanchored_words: int = int(
        os.getenv(
            "CONTINUOUS_MAX_UNANCHORED_WORDS",
            os.getenv("MUAALEM_CONTINUOUS_MAX_UNANCHORED_WORDS", "2"),
        )
    )
    # Extra words phonetized past the scored chunk. quran_phonetizer applies waqf (pause)
    # rules at the end of whatever text it is given; these padding words absorb that artifact
    # and are discarded, so the last scored word is not judged against a pause it never made.
    muaalem_context_pad_words: int = int(os.getenv("MUAALEM_CONTEXT_PAD_WORDS", "5"))
    # Verse detection cutoff for the Muaalem backend. VERSE_DETECTION_THRESHOLD is calibrated
    # for Arabic-letter CER; phoneme strings are longer and finer-grained, so they need re-tuning.
    muaalem_verse_detection_threshold: float = float(
        os.getenv("MUAALEM_VERSE_DETECTION_THRESHOLD", "0.6")
    )

    # --- Moshaf (recitation) attributes, passed to quran_transcript.quran_phonetizer ---
    # These describe the recitation style the reference is phonetized for; they change what
    # counts as correct, so they must match how the reciter actually recites.
    moshaf_rewaya: str = os.getenv("MOSHAF_REWAYA", "hafs")
    moshaf_madd_monfasel_len: int = int(os.getenv("MOSHAF_MADD_MONFASEL_LEN", "2"))
    moshaf_madd_mottasel_len: int = int(os.getenv("MOSHAF_MADD_MOTTASEL_LEN", "4"))
    moshaf_madd_mottasel_waqf: int = int(os.getenv("MOSHAF_MADD_MOTTASEL_WAQF", "4"))
    # Must be 4 or 6. quran_transcript derives madd_alleen_len from this, and a 2-count leen
    # madd raises KeyError deep inside its phonetizer (8/6236 verses and ~3% of word slices,
    # all madd-al-leen words). See _validate_acoustic_backend. The upstream docs example uses 2.
    moshaf_madd_aared_len: int = int(os.getenv("MOSHAF_MADD_AARED_LEN", "4"))
    score_threshold: float = float(os.getenv("SCORE_THRESHOLD", "0.5"))
    pass_on_any_score: bool = os.getenv("PASS_ON_ANY_SCORE", "false").lower() in ("1", "true", "yes")
    max_edits_for_correction: int = int(os.getenv("MAX_EDITS_FOR_CORRECTION", "2"))
    silence_timeout_ms: int = int(os.getenv("SILENCE_TIMEOUT_MS", "3000"))
    audio_sample_rate: int = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
    # When set, socket connections must send this value in handshake auth.api_key; when empty, auth is disabled.
    socket_auth_api_key: str = os.getenv("SOCKET_AUTH_API_KEY", "")
    # When set, the frontend requires this password (validated server-side via POST /api/login)
    # before the UI unlocks. Empty = no password gate.
    app_password: str = os.getenv("APP_PASSWORD", "")
    # Interval in ms between streaming transcription runs
    streaming_interval_ms: int = int(os.getenv("STREAMING_INTERVAL_MS", "1500"))
    # Minimum audio buffer (seconds) before first streaming transcription
    streaming_min_audio_sec: float = float(os.getenv("STREAMING_MIN_AUDIO_SEC", "0.8"))
    # Minimum score (0-1) for verse detection to confirm start verse
    verse_detection_threshold: float = float(os.getenv("VERSE_DETECTION_THRESHOLD", "0.6"))
    # Upper cap on how many words from the utterance/verse start to compare when the
    # decoded utterance is long (keeps alignment cheap; short utterances use their own length).
    verse_detection_word_count: int = int(os.getenv("VERSE_DETECTION_WORD_COUNT", "3"))
    # Candidates whose alignment score is within this margin of the best are treated as a
    # tie (identical/near-identical verses). A genuine tie is left "ambiguous" so detection
    # waits for the next distinct verse instead of guessing the wrong occurrence.
    verse_detection_tie_epsilon: float = float(os.getenv("VERSE_DETECTION_TIE_EPSILON", "0.05"))
    # Fallback for the per-session `record` flag on start_session: used only when the client
    # does not send `record`. When enabled, info.json and recording.wav are persisted to
    # data/sessions/{uuid}/ in background (non-blocking).
    save_session_data: bool = _env_bool("SAVE_SESSION_DATA", False)
    # Where recorded sessions live ({dir}/{uuid}/info.json + recording.wav). Configurable so
    # deployments can point it at durable storage (e.g. a Modal Volume mounted at /data).
    sessions_dir: str = os.getenv("SESSIONS_DIR", "./data/sessions")
    # Public origin used to build absolute URLs in socket payloads (e.g. https://api.example.com).
    # When unset, the origin is derived from the client's handshake headers instead.
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "")

    def __post_init__(self) -> None:
        """Resolve relative paths so they work when cwd is not project root (e.g. Modal)."""
        self.hf_model_path = _resolve_path(self.hf_model_path)
        self.hafs_json_path = _resolve_path(self.hafs_json_path)
        self.sessions_dir = _resolve_path(self.sessions_dir)
        self._validate_acoustic_backend()

    def _validate_acoustic_backend(self) -> None:
        """Fail fast on acoustic settings that would otherwise surface as opaque library errors.

        The moshaf checks are unconditional: any session may select the muaalem backend
        regardless of which one ACOUSTIC_BACKEND makes the default, so a bad moshaf setting
        must fail at startup rather than mid-recitation.
        """
        if self.acoustic_backend not in ACOUSTIC_BACKENDS:
            raise ValueError(
                f"ACOUSTIC_BACKEND={self.acoustic_backend!r} is not one of {sorted(ACOUSTIC_BACKENDS)}"
            )
        # quran_transcript derives madd_alleen_len from madd_aared_len, and only has phoneme
        # tags for a 4- or 6-count leen madd; 2 raises `KeyError: <letter>` from deep inside
        # quran_phonetizer on any madd-al-leen word (e.g. عَيْنَيْنِ, قُرَيْشٍ, ٱلصَّيْفِ).
        if self.moshaf_madd_aared_len not in (4, 6):
            raise ValueError(
                f"MOSHAF_MADD_AARED_LEN={self.moshaf_madd_aared_len} is unsupported; use 4 or 6. "
                "quran_transcript cannot phonetize a leen madd shorter than 4."
            )
        if self.continuous_max_unanchored_words < 0:
            raise ValueError(
                "CONTINUOUS_MAX_UNANCHORED_WORDS must be zero or greater."
            )


config = Config()
