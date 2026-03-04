import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

# Project root (parent of backend/) so relative paths work when cwd is not project root (e.g. Modal)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_path(path: str) -> str:
    """Resolve relative file paths against project root."""
    if path.startswith("."):
        return os.path.abspath(os.path.join(_PROJECT_ROOT, path))
    return path


@dataclass
class Config:
    hf_model_path: str = os.getenv("HF_MODEL_PATH", "./guff/whisper-quran-v1")
    hafs_json_path: str = os.getenv("HAFS_JSON_PATH", "./assets/narrations/hafs.json")
    weight_char: float = float(os.getenv("WEIGHT_CHAR", "0.6"))
    weight_diacritic: float = float(os.getenv("WEIGHT_DIACRITIC", "0.4"))
    enable_acoustic_score: bool = os.getenv("ENABLE_ACOUSTIC_SCORE", "false").lower() in ("1", "true", "yes")
    weight_acoustic: float = float(os.getenv("WEIGHT_ACOUSTIC", "0.3"))
    wav2vec2_quran_asr_model: str = os.getenv(
        "WAV2VEC2_QURAN_ASR_MODEL", "HamzaSidhu786/wav2vec2-base-word-by-word-quran-asr"
    )
    wav2vec2_lm_path: str = os.getenv("WAV2VEC2_LM_PATH", "./assets/quran_lm.arpa")
    wav2vec2_beam_width: int = int(os.getenv("WAV2VEC2_BEAM_WIDTH", "100"))
    wav2vec2_lm_alpha: float = float(os.getenv("WAV2VEC2_LM_ALPHA", "0.5"))
    wav2vec2_lm_beta: float = float(os.getenv("WAV2VEC2_LM_BETA", "1.5"))
    score_threshold: float = float(os.getenv("SCORE_THRESHOLD", "0.5"))
    max_edits_for_correction: int = int(os.getenv("MAX_EDITS_FOR_CORRECTION", "2"))
    silence_timeout_ms: int = int(os.getenv("SILENCE_TIMEOUT_MS", "3000"))
    audio_sample_rate: int = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
    # When True, word_result includes transcribed, expected, char_score, diacritic_score, total_score, acoustic_score.
    send_word_result_details: bool = os.getenv("SEND_WORD_RESULT_DETAILS", "false").lower() in ("1", "true", "yes")
    # When set, socket connections must send this value in handshake auth.api_key; when empty, auth is disabled.
    socket_auth_api_key: str = os.getenv("SOCKET_AUTH_API_KEY", "")
    # When True, save each transcribed audio chunk to backend/chunks/ for testing/debugging.
    save_audio_chunks: bool = os.getenv("SAVE_AUDIO_CHUNKS", "false").lower() in ("1", "true", "yes")

    def __post_init__(self) -> None:
        """Resolve relative paths so they work when cwd is not project root (e.g. Modal)."""
        self.hf_model_path = _resolve_path(self.hf_model_path)
        self.hafs_json_path = _resolve_path(self.hafs_json_path)
        self.wav2vec2_lm_path = _resolve_path(self.wav2vec2_lm_path)


config = Config()
