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
    wav2vec2_lm_path: str = os.getenv("WAV2VEC2_LM_PATH", "./assets/quran_lm.arpa")
    wav2vec2_beam_width: int = int(os.getenv("WAV2VEC2_BEAM_WIDTH", "100"))
    wav2vec2_lm_alpha: float = float(os.getenv("WAV2VEC2_LM_ALPHA", "0.5"))
    wav2vec2_lm_beta: float = float(os.getenv("WAV2VEC2_LM_BETA", "1.5"))
    score_threshold: float = float(os.getenv("SCORE_THRESHOLD", "0.5"))
    pass_on_any_score: bool = os.getenv("PASS_ON_ANY_SCORE", "false").lower() in ("1", "true", "yes")
    max_edits_for_correction: int = int(os.getenv("MAX_EDITS_FOR_CORRECTION", "2"))
    silence_timeout_ms: int = int(os.getenv("SILENCE_TIMEOUT_MS", "3000"))
    audio_sample_rate: int = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
    # When True, word_result includes transcribed, expected, char_score, diacritic_score, total_score, acoustic_score.
    send_word_result_details: bool = os.getenv("SEND_WORD_RESULT_DETAILS", "false").lower() in ("1", "true", "yes")
    # When set, socket connections must send this value in handshake auth.api_key; when empty, auth is disabled.
    socket_auth_api_key: str = os.getenv("SOCKET_AUTH_API_KEY", "")
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
    # When True, persist data.json and recording.wav to data/sessions/{uuid}/ in background (non-blocking)
    save_session_data: bool = os.getenv("SAVE_SESSION_DATA", "true").lower() in ("1", "true", "yes")

    def __post_init__(self) -> None:
        """Resolve relative paths so they work when cwd is not project root (e.g. Modal)."""
        self.hf_model_path = _resolve_path(self.hf_model_path)
        self.hafs_json_path = _resolve_path(self.hafs_json_path)
        self.wav2vec2_lm_path = _resolve_path(self.wav2vec2_lm_path)


config = Config()
