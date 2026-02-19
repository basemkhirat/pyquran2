import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    transcription_backend: str = os.getenv("TRANSCRIPTION_BACKEND", "whisper_cpp")
    whisper_model_path: str = os.getenv("WHISPER_MODEL_PATH", "./whisper_cpp/epoch-best/ggml-model.bin")
    mlx_model_path: str = os.getenv("MLX_MODEL_PATH", "./mlx_models/epoch-best")
    hafs_json_path: str = os.getenv("HAFS_JSON_PATH", "./assets/narrations/hafs.json")
    weight_char: float = float(os.getenv("WEIGHT_CHAR", "0.6"))
    weight_diacritic: float = float(os.getenv("WEIGHT_DIACRITIC", "0.4"))
    score_threshold: float = float(os.getenv("SCORE_THRESHOLD", "0.5"))
    silence_timeout_ms: int = int(os.getenv("SILENCE_TIMEOUT_MS", "3000"))
    audio_sample_rate: int = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))


config = Config()
